"""Run with: PYTHONPATH=src python -m unittest discover -s tests -v."""
import unittest

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.preprocessing._utils import (
    _MIN_UNITS, _voronoi_units, compute_cluster_labels, lattice_indices, plan_spatial_units,
)
from focus.preprocessing.st_clustering import spot_cluster_labels


def square_lattice(rows, cols, step=16.0, angle_deg=0.65, origin=(1000.0, 2000.0)):
    r, c = np.mgrid[:rows, :cols]
    r, c = r.ravel(), c.ravel()
    a = np.deg2rad(angle_deg)
    col_axis = np.array([np.cos(a), np.sin(a)]) * step
    row_axis = np.array([-np.sin(a), np.cos(a)]) * step
    xy = np.asarray(origin) + np.outer(c, col_axis) + np.outer(r, row_axis)
    return xy, pd.DataFrame({'array_row': r + 5, 'array_col': c + 3})


class LatticeUnitTests(unittest.TestCase):
    def test_rotated_lattice_blocks_hold_k_squared_sites_and_ignore_pose(self):
        xy, obs = square_lattice(700, 500)
        bin_ids, n_units, info = plan_spatial_units(xy, cap=250_000, obs=obs)
        self.assertEqual((info['method'], info['k'], info['driver']), ('lattice', 2, 'cap'))
        np.testing.assert_allclose(info['unit_size'], [32, 32], rtol=1e-6)
        self.assertEqual(n_units, 350 * 250)
        self.assertTrue(np.all(np.bincount(bin_ids) == 4))
        theta = np.deg2rad(37)
        rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        moved, _, _ = plan_spatial_units(xy @ rotation.T + [5e4, -3e3], cap=250_000, obs=obs)
        np.testing.assert_array_equal(moved, bin_ids)

    def test_only_tissue_edge_blocks_are_partial(self):
        xy, obs = square_lattice(700, 700, angle_deg=0, origin=(0, 0))
        center, radius = np.array([350, 350]) * 16.0, 330 * 16.0
        disc = np.linalg.norm(xy - center, axis=1) < radius
        bin_ids, _, info = plan_spatial_units(xy[disc], cap=100_000, obs=obs[disc].reset_index(drop=True))
        k = info['k']
        sizes = np.bincount(bin_ids)
        self.assertLessEqual(sizes.max(), k * k)
        partial = sizes[bin_ids] < k * k
        distance = np.linalg.norm(xy[disc][partial] - center, axis=1)
        self.assertGreater(distance.min(), radius - 2 * k * 16 * np.sqrt(2))

    def test_hexagonal_lattice_blocks_have_equal_occupancy(self):
        r, c = np.mgrid[:600, :1000]
        keep = (r + c) % 2 == 0
        r, c = r[keep], c[keep]
        xy = np.column_stack([c * 50.0, r * 50.0 * np.sqrt(3)])
        obs = pd.DataFrame({'array_row': r, 'array_col': c})
        bin_ids, n_units, info = plan_spatial_units(xy, cap=250_000, obs=obs)
        self.assertEqual((info['method'], info['k']), ('lattice', 2))
        self.assertTrue(np.all(np.bincount(bin_ids) == 4))

    def test_axis_aligned_raster_is_recovered_and_irregular_input_falls_back(self):
        pitch = np.array([10.0, 5.0])
        i, j = np.mgrid[:600, :500]
        xy = (np.column_stack([i.ravel(), j.ravel()]) + 0.5) * pitch
        idx, step, source = lattice_indices(xy, spot_size=pitch)
        self.assertEqual(source, 'raster')
        np.testing.assert_array_equal(idx.max(axis=0), [599, 499])
        np.testing.assert_array_equal(step, pitch)
        _, _, info = plan_spatial_units(xy, cap=250_000, spot_size=pitch)
        self.assertEqual((info['method'], info['k']), ('lattice', 2))
        jittered = xy + np.random.default_rng(0).uniform(-0.3, 0.3, xy.shape) * pitch
        self.assertIsNone(lattice_indices(jittered, spot_size=pitch))
        self.assertIsNone(lattice_indices(np.vstack([xy, xy[:10]]), spot_size=pitch))
        self.assertEqual(plan_spatial_units(jittered, cap=250_000, spot_size=pitch)[2]['method'], 'voronoi')

    def test_signal_criterion_grows_blocks_until_median_reaches_target(self):
        xy, obs = square_lattice(300, 300, step=2.0, angle_deg=0)
        counts = np.random.default_rng(1).poisson(3, len(xy))
        bin_ids, _, info = plan_spatial_units(xy, cap=250_000, obs=obs, counts=counts, min_median_counts=50)
        self.assertEqual((info['method'], info['k'], info['driver']), ('lattice', 5, 'signal'))
        self.assertGreaterEqual(np.median(np.bincount(bin_ids, weights=counts)), 50)
        native, _, info = plan_spatial_units(xy, cap=250_000, obs=obs, counts=counts * 0 + 100, min_median_counts=50)
        self.assertIsNone(native)
        self.assertEqual(info['method'], 'native')

    def test_signal_criterion_keeps_a_minimum_number_of_units(self):
        xy, obs = square_lattice(300, 300, step=2.0, angle_deg=0)
        _, n_units, info = plan_spatial_units(xy, obs=obs, counts=np.zeros(len(xy)), min_median_counts=50)
        self.assertTrue(info['signal_limited'])
        self.assertEqual(info['k'], 9)
        self.assertGreaterEqual(n_units, _MIN_UNITS)


class VoronoiUnitTests(unittest.TestCase):
    def test_supercells_are_deterministic_bounded_and_balanced(self):
        xy = np.random.default_rng(2).uniform(0, 5000, (300_000, 2))
        first, n_units, info = plan_spatial_units(xy, cap=20_000, seed=3)
        again, _, _ = plan_spatial_units(xy, cap=20_000, seed=3)
        np.testing.assert_array_equal(first, again)
        self.assertEqual(info['method'], 'voronoi')
        self.assertLessEqual(n_units, 20_000)
        cv = lambda ids: np.bincount(ids).std() / np.bincount(ids).mean()
        self.assertLess(cv(first), 0.4)
        self.assertLess(cv(first), 0.75 * cv(_voronoi_units(xy, 20_000, 3, iterations=0)[0]))

    def test_sparse_irregular_spots_get_supercells_from_the_signal_criterion(self):
        xy = np.random.default_rng(4).uniform(0, 3000, (40_000, 2))
        _, n_units, info = plan_spatial_units(xy, cap=250_000, counts=np.full(len(xy), 5), min_median_counts=50)
        self.assertEqual((info['method'], info['driver']), ('voronoi', 'signal'))
        self.assertLessEqual(n_units, 4_000)


class SpotLabelTests(unittest.TestCase):
    def test_unit_labels_and_eligibility_reach_every_spot(self):
        xy, obs = square_lattice(120, 120, step=2.0, angle_deg=0)
        right = (obs['array_col'] - 3).to_numpy() >= 60
        means = np.full((len(xy), 60), 0.05)
        means[~right, :20] = 0.25
        means[right, 20:40] = 0.25
        X = np.random.default_rng(5).poisson(means).astype(np.float32)
        empty = ((obs['array_row'] - 5).to_numpy() < 9) & ((obs['array_col'] - 3).to_numpy() < 9)
        X[empty] = 0
        labels, eligible, info = spot_cluster_labels(sp.csr_matrix(X), [f'g{i}' for i in range(60)], xy, obs, [2, 2])
        self.assertEqual((info['units']['method'], info['units']['k']), ('lattice', 3))
        self.assertEqual(len(labels), len(xy))
        self.assertTrue(np.all(labels[empty] == 'unassigned'))
        self.assertFalse(eligible[empty].any())
        self.assertEqual(info['n_eligible_spots'], int(eligible.sum()))
        self.assertGreater(adjusted_rand_score(right[eligible], labels[eligible]), 0.9)
        block = ((obs['array_row'] - 5) // 3 * 40 + (obs['array_col'] - 3) // 3).to_numpy()
        for b in np.unique(block)[:200]:
            self.assertEqual(len(set(labels[block == b])), 1)

    def test_msi_raster_is_clustered_on_blocks(self):
        pitch = np.array([20.0, 20.0])
        i, j = np.mgrid[:100, :100]
        xy = (np.column_stack([i.ravel(), j.ravel()]) + 0.5) * pitch
        rng = np.random.default_rng(6)
        X = rng.uniform(0, 1, (len(xy), 30))
        X[i.ravel() < 50, :10] += 3
        X /= X.sum(axis=1, keepdims=True)
        labels = compute_cluster_labels(sp.csr_matrix(X), leiden_resolution=0.5, normalize_target_sum=None,
                                        coordinates=xy, spot_size=pitch, cap=5_000)
        block = (i.ravel() // 2) * 50 + j.ravel() // 2
        for b in range(0, 2500, 7):
            self.assertEqual(len(set(labels[block == b])), 1)
        self.assertGreater(adjusted_rand_score(i.ravel() < 50, labels), 0.9)


if __name__ == '__main__': unittest.main()
