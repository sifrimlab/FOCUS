"""Run with: PYTHONPATH=src python -m unittest discover -s tests -v."""
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score, homogeneity_score

from focus.constants import SpotResolution
from focus.preprocessing._utils import (
    _MIN_UNITS, _voronoi_units, clustering_preset, compute_cluster_labels, infer_lattice,
    label_coherence, plan_spatial_units, smoothing_operator,
)
from focus.preprocessing.st_clustering import native_cluster_labels, spot_cluster_labels
from focus.utils import parse_config


def lattice(n0, n1, a0, a1, angle_deg=0.0, origin=(1234.5, -987.6)):
    """Spots i * a0 + j * a1, rotated; returns coords and the true (i, j)."""
    i, j = np.mgrid[:n0, :n1]
    ij = np.column_stack([i.ravel(), j.ravel()])
    a = np.deg2rad(angle_deg)
    rotation = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return (ij @ np.vstack([a0, a1])) @ rotation.T + origin, ij


def same_partition(a, b):
    pairs = set(zip(a.tolist(), b.tolist()))
    return len(pairs) == len(set(a.tolist())) == len(set(b.tolist()))


class LatticeInferenceTests(unittest.TestCase):
    def assert_relabelling(self, idx, truth):
        """idx is an integer affine relabelling of truth (axis swap/flip and shift)."""
        design = np.column_stack([truth, np.ones(len(truth))])
        fit = np.linalg.lstsq(design, idx, rcond=None)[0]
        np.testing.assert_allclose(design @ fit, idx, atol=1e-6)
        np.testing.assert_allclose(fit[:2], np.round(fit[:2]), atol=1e-9)
        self.assertEqual(abs(round(np.linalg.det(fit[:2]))), 1)

    def test_rotated_square_lattice_with_holes(self):
        xy, ij = lattice(400, 300, [16, 0], [0, 16], angle_deg=0.65)
        keep = np.random.default_rng(0).random(len(xy)) < 0.8
        idx, basis = infer_lattice(xy[keep])
        self.assert_relabelling(idx, ij[keep])
        np.testing.assert_allclose(np.linalg.norm(basis, axis=1), [16, 16], rtol=1e-6)

    def test_hexagonal_lattice_and_rectangular_raster(self):
        xy, ij = lattice(60, 80, [100, 0], [50, 100 * np.sqrt(3) / 2], angle_deg=3)
        self.assert_relabelling(infer_lattice(xy)[0], ij)
        xy, ij = lattice(300, 200, [10, 0], [0, 5])
        idx, basis = infer_lattice(xy)
        self.assert_relabelling(idx, ij)
        np.testing.assert_allclose(sorted(np.linalg.norm(basis, axis=1)), [5, 10], rtol=1e-6)

    def test_irregular_jittered_and_duplicated_layouts_are_rejected(self):
        rng = np.random.default_rng(1)
        self.assertIsNone(infer_lattice(rng.uniform(0, 5000, (50_000, 2))))
        xy, _ = lattice(200, 200, [10, 0], [0, 10])
        self.assertIsNone(infer_lattice(xy + rng.uniform(-6, 6, xy.shape)))
        self.assertIsNone(infer_lattice(np.vstack([xy, xy[:10]])))


class UnitPlanTests(unittest.TestCase):
    def test_cap_blocks_hold_k_squared_sites_in_any_pose(self):
        xy, _ = lattice(700, 500, [16, 0], [0, 16], angle_deg=0.65)
        units = plan_spatial_units(xy, cap=250_000)
        self.assertEqual((units.info['method'], units.info['k'], units.info['driver']), ('lattice', 2, 'cap'))
        np.testing.assert_allclose(units.info['unit_size'], [32, 32], rtol=1e-6)
        self.assertEqual(units.n_units, 350 * 250)
        self.assertTrue(np.all(np.bincount(units.bin_ids) == 4))
        theta = np.deg2rad(37)
        rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        moved = plan_spatial_units(xy @ rotation.T + [5e4, -3e3], cap=250_000)
        self.assertTrue(same_partition(moved.bin_ids, units.bin_ids))

    def test_only_tissue_edge_blocks_are_partial(self):
        xy, _ = lattice(700, 700, [16, 0], [0, 16], origin=(0, 0))
        center, radius = np.array([350, 350]) * 16.0, 330 * 16.0
        disc = np.linalg.norm(xy - center, axis=1) < radius
        units = plan_spatial_units(xy[disc], cap=100_000)
        k = units.info['k']
        sizes = np.bincount(units.bin_ids)
        self.assertLessEqual(sizes.max(), k * k)
        partial = sizes[units.bin_ids] < k * k
        self.assertGreater(np.linalg.norm(xy[disc][partial] - center, axis=1).min(), radius - 2 * k * 16 * np.sqrt(2))

    def test_hexagonal_blocks_have_equal_occupancy(self):
        xy, _ = lattice(600, 500, [100, 0], [50, 100 * np.sqrt(3) / 2])
        units = plan_spatial_units(xy, cap=250_000)
        self.assertEqual((units.info['method'], units.info['k']), ('lattice', 2))
        self.assertTrue(np.all(np.bincount(units.bin_ids) == 4))

    def test_signal_target_grows_blocks_and_rich_spots_stay_native(self):
        xy, _ = lattice(300, 300, [2, 0], [0, 2])
        counts = np.random.default_rng(1).poisson(3, len(xy))
        units = plan_spatial_units(xy, counts=counts, min_median_counts=50)
        self.assertEqual((units.info['method'], units.info['k'], units.info['driver']), ('lattice', 5, 'signal'))
        self.assertGreaterEqual(np.median(np.bincount(units.bin_ids, weights=counts)), 50)
        native = plan_spatial_units(xy, counts=counts * 0 + 100, min_median_counts=50)
        self.assertIsNone(native.bin_ids)
        self.assertEqual((native.info['method'], native.info['layout']), ('native', 'lattice'))

    def test_signal_target_keeps_a_minimum_number_of_units(self):
        xy, _ = lattice(300, 300, [2, 0], [0, 2])
        units = plan_spatial_units(xy, counts=np.zeros(len(xy)), min_median_counts=50)
        self.assertTrue(units.info['signal_limited'])
        self.assertEqual(units.info['k'], 9)
        self.assertGreaterEqual(units.n_units, _MIN_UNITS)

    def test_voronoi_supercells_are_deterministic_bounded_and_balanced(self):
        xy = np.random.default_rng(2).uniform(0, 5000, (300_000, 2))
        first = plan_spatial_units(xy, cap=20_000, seed=3)
        again = plan_spatial_units(xy, cap=20_000, seed=3)
        np.testing.assert_array_equal(first.bin_ids, again.bin_ids)
        self.assertEqual((first.info['method'], first.info['layout']), ('voronoi', 'irregular'))
        self.assertLessEqual(first.n_units, 20_000)
        cv = lambda ids: np.bincount(ids).std() / np.bincount(ids).mean()
        self.assertLess(cv(first.bin_ids), 0.4)
        self.assertLess(cv(first.bin_ids), 0.75 * cv(_voronoi_units(xy, 20_000, 3, iterations=0)[0]))

    def test_sparse_irregular_spots_get_supercells_from_the_signal_target(self):
        xy = np.random.default_rng(4).uniform(0, 3000, (40_000, 2))
        units = plan_spatial_units(xy, counts=np.full(len(xy), 5), min_median_counts=50)
        self.assertEqual((units.info['method'], units.info['driver']), ('voronoi', 'signal'))
        self.assertLessEqual(units.n_units, 4_000)


class SmoothingTests(unittest.TestCase):
    def neighbours_of_interior(self, xy, rings=1):
        units = plan_spatial_units(xy)
        op = smoothing_operator(units, rings)
        np.testing.assert_allclose(np.asarray(op.sum(axis=1)).ravel(), 1, rtol=1e-6)
        center = np.argmin(np.linalg.norm(xy - xy.mean(axis=0), axis=1))
        row = op.getrow(center)
        return xy[row.indices] - xy[center], units

    def test_lattice_neighbourhoods_are_isotropic(self):
        square, _ = lattice(40, 40, [16, 0], [0, 16], angle_deg=0.65)
        offsets, units = self.neighbours_of_interior(square)
        self.assertEqual(len(offsets), 9)
        hexagonal, _ = lattice(40, 40, [100, 0], [50, 100 * np.sqrt(3) / 2])
        offsets, _ = self.neighbours_of_interior(hexagonal)
        self.assertEqual(len(offsets), 7)
        np.testing.assert_allclose(sorted(np.linalg.norm(offsets, axis=1))[1:], 100, rtol=1e-6)
        rectangular, _ = lattice(40, 40, [10, 0], [0, 5])
        offsets, _ = self.neighbours_of_interior(rectangular)
        for d in offsets:
            self.assertTrue(np.any(np.all(np.isclose(offsets, -d), axis=1)))
        self.assertLessEqual(np.linalg.norm(offsets, axis=1).max(), 15 + 1e-6)
        self.assertEqual(smoothing_operator(units, 0).nnz, units.n_units)

    def test_irregular_neighbourhoods_use_nearest_units(self):
        xy = np.random.default_rng(3).uniform(0, 1000, (2_000, 2))
        offsets, units = self.neighbours_of_interior(xy)
        self.assertEqual(len(offsets), 7)
        keep = np.arange(units.n_units) % 2 == 0
        self.assertEqual(smoothing_operator(units, 1, keep).shape, (keep.sum(), keep.sum()))

    def test_neighbourhood_recovers_weak_domains_without_patches(self):
        xy, ij = lattice(80, 80, [16, 0], [0, 16])
        truth = ij[:, 1] >= 40
        means = np.full((len(xy), 200), 0.3)
        means[~truth, :30] += 0.25
        means[truth, 30:60] += 0.25
        X = sp.csr_matrix(np.random.default_rng(7).poisson(means).astype(np.float32))
        units = plan_spatial_units(xy)
        genes = [f'g{i}' for i in range(200)]
        plain, eligible, _ = native_cluster_labels(X, genes, units=units, rings=0)
        for rings in (1, 2):
            labels, eligible, info = native_cluster_labels(X, genes, units=units, rings=rings)
            self.assertEqual(info['n_clusters'], 2)
            self.assertGreater(adjusted_rand_score(truth[eligible], labels[eligible]), 0.9)
            self.assertGreater(info['label_coherence'], label_coherence(plain[eligible].astype(str), units, eligible))


class PresetTests(unittest.TestCase):
    def test_unknown_resolution_is_rejected(self):
        with self.assertRaises(ValueError):
            clustering_preset('ultra')
        with self.assertRaises(ValueError):
            spot_cluster_labels(sp.csr_matrix(np.ones((20, 3))), list('abc'), np.zeros((20, 2)), 'ultra')
        with tempfile.TemporaryDirectory() as root:
            Path(root, 's1', 'ST').mkdir(parents=True)
            config = {'dataset_path': root, 'reference_modality': 'ST', 'perform_alignment': False,
                      'perform_registration': False, 'modalities': [{
                          'name': 'ST', 'type': 'st', 'alignment_strategy': 'manual', 'registration_type': 'none',
                          'processing_settings': {'resolution': SpotResolution.SUB_CELLULAR}, 'registration_settings': {}}]}
            parse_config(copy.deepcopy(config), require_raw_inputs=False)
            config['modalities'][0]['processing_settings']['resolution'] = 'ultra'
            with self.assertRaisesRegex(ValueError, 'resolution must be one of'):
                parse_config(config, require_raw_inputs=False)

    def test_sub_cellular_coarsens_sparse_data_and_labels_reach_every_spot(self):
        xy, ij = lattice(240, 240, [2, 0], [0, 2])
        right = ij[:, 1] >= 120
        means = np.full((len(xy), 60), 0.05)
        means[~right, :20] = 0.6
        means[right, 20:40] = 0.6
        X = np.random.default_rng(5).poisson(means).astype(np.float32)
        X[(ij[:, 0] < 30) & (ij[:, 1] < 30)] = 0
        genes = [f'g{i}' for i in range(60)]
        labels, eligible, info = spot_cluster_labels(sp.csr_matrix(X), genes, xy, SpotResolution.SUB_CELLULAR)
        units = plan_spatial_units(xy, counts=X.sum(axis=1), min_median_counts=1000)
        self.assertEqual(info['units']['method'], 'lattice')
        self.assertEqual(info['units']['k'], units.info['k'])
        self.assertEqual(info['resolution'], SpotResolution.SUB_CELLULAR)
        self.assertEqual(len(labels), len(xy))
        unit_counts = np.bincount(units.bin_ids, weights=X.sum(axis=1))
        empty = unit_counts[units.bin_ids] == 0
        self.assertTrue(empty.any())
        self.assertTrue(np.all(labels[empty] == 'unassigned'))
        self.assertFalse(eligible[empty].any())
        # Blocks straddling the domain boundary hold both domains; on the pure ones, no label
        # may mix the two domains (partial edge blocks may still form their own cluster).
        share = np.bincount(units.bin_ids, weights=right) / np.bincount(units.bin_ids)
        pure = eligible & np.isin(share, (0, 1))[units.bin_ids]
        self.assertGreater(homogeneity_score(right[pure], labels[pure]), 0.99)
        for unit in np.unique(units.bin_ids)[:300]:
            self.assertEqual(len(set(labels[units.bin_ids == unit])), 1)
        _, _, standard = spot_cluster_labels(sp.csr_matrix(X), genes, xy, SpotResolution.STANDARD)
        self.assertEqual(standard['units']['method'], 'native')

    def test_msi_raster_is_clustered_on_blocks_with_smoothing(self):
        xy, ij = lattice(100, 100, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(6)
        X = rng.uniform(0, 1, (len(xy), 30))
        X[ij[:, 0] < 50, :10] += 3
        X /= X.sum(axis=1, keepdims=True)
        labels = compute_cluster_labels(sp.csr_matrix(X), leiden_resolution=0.5, normalize_target_sum=None,
                                        coordinates=xy, smoothing_rings=1, cap=5_000)
        units = plan_spatial_units(xy, cap=5_000)
        self.assertEqual(units.info['k'], 2)
        for unit in range(0, units.n_units, 7):
            self.assertEqual(len(set(labels[units.bin_ids == unit])), 1)
        self.assertGreater(adjusted_rand_score(ij[:, 0] < 50, labels), 0.9)


if __name__ == '__main__': unittest.main()
