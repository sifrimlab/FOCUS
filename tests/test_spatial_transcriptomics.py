"""Run with: PYTHONPATH=src python -m unittest discover -s tests -v."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import anndata as ad
import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.preprocessing.st_clustering import native_cluster_labels
from focus.preprocessing._utils import clustering_preset
from focus.preprocessing.transcriptomic import SpatialTranscriptomic, SpatialTranscriptomicDataset
from focus.alignment.alignment import DirectMappingAligner
from focus.alignment.spot_payload import pack_spots, acquisition_basis
from focus.GUI.direct_mapping_alignment import DirectMappingAlignmentGUI
from focus.provenance import cached_fingerprint
from focus.spot_colors import cluster_colors, label_order


def expression(n=120):
    rng = np.random.default_rng(9)
    means = np.ones((n, 60))
    means[:n//2, :20] = 15
    means[n//2:, 20:40] = 15
    return sp.csr_matrix(rng.poisson(means).astype(np.float32))


class NativeClusteringTests(unittest.TestCase):
    def test_known_domains_determinism_and_no_expression_mutation(self):
        X = expression()
        original = X.copy()
        names = [f'g{i}' for i in range(X.shape[1])]
        labels, eligible, info = native_cluster_labels(X, names)
        repeated, _, _ = native_cluster_labels(X, names)
        np.testing.assert_array_equal(labels, repeated)
        self.assertTrue(eligible.all())
        self.assertEqual((X != original).nnz, 0)
        self.assertGreater(adjusted_rand_score(np.arange(len(labels)) >= len(labels)//2, labels), .9)
        self.assertEqual(info['n_input'], X.shape[0])
        self.assertLessEqual(info['n_selected_genes'], 2000)

    def test_ineligible_spots_stay_in_place_and_have_no_cluster(self):
        X = sp.vstack([sp.csr_matrix((1, 60)), sp.csr_matrix(np.ones((1, 60))), expression()]).tocsr()
        labels, eligible, _ = native_cluster_labels(X, [f'g{i}' for i in range(60)], {'min_counts': 100})
        self.assertEqual(len(labels), X.shape[0])
        np.testing.assert_array_equal(eligible[:2], [False, False])
        np.testing.assert_array_equal(labels[:2], ['unassigned', 'unassigned'])
        labels, _, info = native_cluster_labels(sp.csr_matrix((4, 5)), list('abcde'))
        self.assertTrue(np.all(labels == 'unassigned'))
        self.assertEqual(info['n_clusters'], 0)

    def test_large_input_is_not_pooled(self):
        n = 100_003
        X = sp.csr_matrix(np.tile(np.arange(1, 7), (n, 1)).astype('float32'))
        seen = []
        def pca(sub, **kwargs):
            seen.append(sub.n_obs)
            sub.obsm['X_pca'] = np.zeros((sub.n_obs, 2))
            sub.uns['pca'] = {'variance_ratio': np.array([.7, .3])}
        def leiden(sub, **kwargs):
            sub.obs['cluster'] = np.where(np.arange(sub.n_obs) % 2, 'a', 'b')
        with patch('scanpy.pp.highly_variable_genes', side_effect=lambda sub, **kw: sub.var.__setitem__('highly_variable', True)), \
             patch('scanpy.pp.pca', side_effect=pca), patch('scanpy.pp.neighbors'), patch('scanpy.tl.leiden', side_effect=leiden):
            labels, _, _ = native_cluster_labels(X, list('abcdef'), {'min_counts': 1, 'min_genes': 1})
        self.assertEqual(seen, [n])
        self.assertEqual(len(labels), n)
        self.assertNotEqual(labels[0], labels[1])

    def test_invalid_settings_and_nonraw_counts_fail(self):
        for resolution in ['ultra', None, 'Standard']:
            with self.assertRaises(ValueError): clustering_preset(resolution)
        with self.assertRaisesRegex(ValueError, 'raw integer'):
            native_cluster_labels(sp.csr_matrix([[.5, 1, 2]]), list('abc'))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        folder = self.root / 'sample' / 'ST'
        folder.mkdir(parents=True)
        self.input = folder / 'input.h5ad'
        self.adata = ad.AnnData(expression(30))
        self.adata.var_names = [f'g{i}' for i in range(60)]
        self.adata.obsm['spatial'] = np.column_stack([np.arange(30) * 16, np.zeros(30)]).astype('float32')
        self.adata.uns['spot_size'] = 16.
        self.adata.write_h5ad(self.input)
        self.sample = SpatialTranscriptomic(str(self.root), 'sample', 'ST')

    def tearDown(self): self.tmp.cleanup()

    def test_cache_tracks_settings_input_and_preserves_data(self):
        path = self.sample.preprocess_data()
        first = cached_fingerprint(path)
        mtime = Path(path).stat().st_mtime_ns
        self.sample.preprocess_data()
        self.assertFalse(self.sample.preprocessing_changed)
        self.assertEqual(mtime, Path(path).stat().st_mtime_ns)
        self.sample.preprocess_data(resolution='sub_cellular')
        self.assertNotEqual(first, cached_fingerprint(path))
        output = ad.read_h5ad(path)
        self.assertEqual((output.X != self.adata.X).nnz, 0)
        np.testing.assert_array_equal(output.obsm['spatial'], self.adata.obsm['spatial'])
        self.assertEqual(output.var_names.tolist(), self.adata.var_names.tolist())
        changed = cached_fingerprint(path)
        self.adata.X.data[0] += 1
        self.adata.write_h5ad(self.input)
        self.sample.preprocess_data(resolution='sub_cellular')
        self.assertNotEqual(changed, cached_fingerprint(path))

    def test_output_normalization_does_not_change_clusters_and_merged_provenance_survives(self):
        dataset = SpatialTranscriptomicDataset(str(self.root), [self.sample])
        files = dataset.process_dataset()
        plain = ad.read_h5ad(files['sample'])
        files = dataset.process_dataset(total_counts_normalize=True, log1p_transform=True)
        normalized = ad.read_h5ad(files['sample'])
        np.testing.assert_array_equal(plain.obs['cluster'], normalized.obs['cluster'])
        self.assertEqual((normalized.layers['raw'] != self.adata.X).nnz, 0)
        merged = ad.read_h5ad(files['merged'])
        self.assertIn('sample', merged.uns['focus_clustering'])
        self.assertIn('selected_genes', merged.uns['focus_clustering']['sample'])

    def test_old_alignment_is_refreshed_from_preprocessing(self):
        source = self.sample.preprocess_data()
        old = ad.read_h5ad(source)
        old.obs['cluster'] = 'old'
        old.obsm['other_spatial'] = old.obsm['spatial'] + 10
        aligned = self.root / 'aligned.h5ad'
        old.write_h5ad(aligned)
        aligner = DirectMappingAligner.__new__(DirectMappingAligner)
        aligner._target_modality = {'sample': source}
        aligner._reference_modality = {'sample': source}
        aligner._target_modality_type = 'st'
        aligner._reference_modality_type = 'msi'
        aligner._reference_modality_name = 'other'
        aligner._aligned_output_path = lambda sid: str(aligned)
        self.assertFalse(aligner._cache_matches('sample'))
        fresh = aligner._fresh_aligned_adata('sample')
        self.assertNotIn('old', fresh.obs['cluster'].astype(str).tolist())
        np.testing.assert_array_equal(fresh.obsm['other_spatial'], old.obsm['other_spatial'])
        fresh.uns['focus_alignment_inputs'] = {'other': aligner._input_key('sample')}
        fresh.write_h5ad(aligned)
        self.assertTrue(aligner._cache_matches('sample'))


class NativeDisplayTests(unittest.TestCase):
    def test_packed_payload_retains_ids_classes_and_geometry(self):
        xy = np.array([[1, 2], [3, 4], [5, 6]], np.float32)
        payload = pack_spots(xy, [0, 16, 32], [True, False, True], [8, 4, 1])
        self.assertEqual(payload[:4], b'FSP1')
        self.assertEqual(len(payload), 16 + 17 * 3)
        np.testing.assert_array_equal(np.frombuffer(payload, '<f4', count=6, offset=16).reshape(-1, 2), xy)
        np.testing.assert_array_equal(np.frombuffer(payload, '<u4', count=3, offset=16+12*3), [8, 4, 1])
        gui = DirectMappingAlignmentGUI(1, threading.Event())
        gui._target_metadata = {'modality_type': 'SPOT'}
        gui._target_payload = payload
        response = gui.app.test_client().get('/target/payload')
        self.assertEqual(response.data, payload)
        self.assertEqual(response.mimetype, 'application/octet-stream')

    def test_validated_rotated_lattice_and_irregular_layout(self):
        row, col = np.mgrid[:8, :8]
        theta = .2
        basis = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
        a = ad.AnnData(sp.csr_matrix((row.size, 2)))
        a.obsm['spatial'] = np.column_stack([col.ravel(), row.ravel()]) @ basis * 16
        a.uns['spot_size'] = [16, 16]
        # Inferred from coordinates alone; each axis matches a true axis up to sign and order.
        for axis in np.asarray(acquisition_basis(a)):
            self.assertAlmostEqual(np.max(np.abs(basis @ axis)), 1, places=6)
        a.obsm['spatial'][0] += 8
        self.assertIsNone(acquisition_basis(a))

    def test_large_display_has_every_original_spot_and_transform(self):
        n = 100_003
        xy = np.column_stack([np.arange(n), np.arange(n) % 316]).astype('float32')
        labels = np.array([str(i % 33) for i in range(n)])
        aligner = DirectMappingAligner.__new__(DirectMappingAligner)
        aligner._exact_spots = False
        aligner._load_anndata_spots = lambda _: (xy, np.array([16., 16.]), np.ones(n, bool), labels, cluster_colors(labels))
        meta, payload, full, _ = aligner._prepare_spot_data('unused', 'ST')
        self.assertEqual(meta['spot_size'], [16, 16])
        self.assertEqual(meta['spot_count'], n)
        ids = np.frombuffer(payload, '<u4', count=n, offset=16 + 12*n)
        points = np.frombuffer(payload, '<f4', count=2*n, offset=16).reshape(-1, 2)
        np.testing.assert_array_equal(points, xy[ids])
        classes = np.frombuffer(payload, '<u4', count=n, offset=16+8*n)
        np.testing.assert_array_equal(np.array(meta['cluster_labels'])[classes], labels[ids])
        matrix = np.array([[1, .1, 20], [0, 1, -5], [0, 0, 1]])
        result = aligner._parse_alignment_result({'transform_matrix': matrix.ravel(order='F')}, payload, full)
        np.testing.assert_allclose(result, (np.column_stack([xy, np.ones(n)]) @ matrix.T)[:, :2], rtol=1e-6)

    def test_label_order_and_colors_do_not_cycle(self):
        labels = [str(i) for i in range(100)] + ['cell type', 'unassigned']
        colors = cluster_colors(labels)
        self.assertEqual(len(set(colors.values())), len(labels))
        self.assertEqual(label_order(['10', '2', 'unassigned']), ['2', '10', 'unassigned'])
        self.assertEqual(colors, cluster_colors(list(reversed(labels))))


if __name__ == '__main__': unittest.main()
