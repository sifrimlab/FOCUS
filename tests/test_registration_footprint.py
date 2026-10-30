"""Footprint geometry and fingerprinted registration. Run with: PYTHONPATH=src python -m unittest discover -s tests -v."""
import os
import tempfile
import unittest

import anndata as ad
import numpy as np
import scipy.sparse as sp
import tifffile
from scipy.spatial import cKDTree

from focus.constants import MODALITY_REGISTRATION, MODALITY_REGISTRATION_MERGED
from focus.provenance import cached_fingerprint
from focus.registration._cache import REGISTRATION_FINGERPRINT_KEY, REGISTRATION_GEOMETRY_KEY
from focus.registration._footprint import (
    AnchorGeometry, classify_lattice, fit_anchor_to_target, footprint_membership,
    footprint_shape, pixel_footprint_membership, row_normalize,
)
from focus.registration.raman_pixel import RamanPixelInterpolationRegistration
from focus.registration.registration import SpotInterpolationRegistration
from focus.registration.spot_aggregation import SpotAggregationRegistration


def square_lattice(n=20, pitch=10.0, angle=0.0, origin=(5.0, 5.0)):
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    pts = np.column_stack([i.ravel(), j.ravel()]) * pitch
    c, s = np.cos(angle), np.sin(angle)
    return pts @ np.array([[c, s], [-s, c]]) + origin


def hex_lattice(n=16, pitch=100.0, angle=0.0):
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    pts = np.column_stack([i.ravel() * pitch + (j.ravel() % 2) * pitch / 2, j.ravel() * pitch * np.sqrt(3) / 2])
    c, s = np.cos(angle), np.sin(angle)
    return pts @ np.array([[c, s], [-s, c]]) + 500.0


def legacy_interpolate(anchor, spot_size, target, features):
    """The pre-footprint implementation: centre-in rectangle and Gaussian, in the target frame."""
    sx, sy = spot_size
    sigma = np.sqrt(sx * sy) / 2.0
    tree = cKDTree(target)
    out = np.zeros((len(anchor), features.shape[1]))
    members = []
    for i, (cx, cy) in enumerate(anchor):
        cand = np.asarray(tree.query_ball_point([cx, cy], r=np.hypot(sx / 2, sy / 2)), dtype=np.intp)
        if cand.size == 0:
            members.append(set())
            continue
        d = target[cand] - [cx, cy]
        keep = (np.abs(d[:, 0]) <= sx / 2) & (np.abs(d[:, 1]) <= sy / 2)
        members.append(set(cand[keep].tolist()))
        if keep.any():
            w = np.exp(-(d[keep] ** 2).sum(1) / (2 * sigma ** 2))
            out[i] = (features[cand[keep]] * (w / w.sum())[:, None]).sum(0)
    return out, members


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.src = square_lattice(30, 13.0, origin=(40.0, 25.0))
        self.spot = np.array([13.0, 13.0])

    def test_identity_and_translation(self):
        t, valid = fit_anchor_to_target(self.src, self.src.astype(np.float32), self.spot)
        self.assertEqual(t.kind, "identity")
        self.assertTrue(valid.all())
        dst = (self.src + [10.25, -3.5]).astype(np.float32)
        t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        self.assertEqual(t.kind, "translation")
        np.testing.assert_allclose(t.to_anchor(dst), self.src, atol=1e-3)

    def test_similarity_with_reflection(self):
        a = np.deg2rad(23)
        lin = 0.37 * np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]) @ np.diag([1.0, -1.0])
        dst = (self.src @ lin.T + [300.0, 900.0]).astype(np.float32)
        t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        self.assertEqual(t.kind, "homography")
        jac = t.jacobian(self.src[:1])[0]
        self.assertLess(np.linalg.det(jac), 0)
        self.assertAlmostEqual(np.sqrt(abs(np.linalg.det(jac))), 0.37, places=5)
        np.testing.assert_allclose(t.to_anchor(dst), self.src, atol=1e-6 * np.ptp(self.src))

    def test_projective_jacobian(self):
        h = np.array([[1.1, 0.05, 20.0], [-0.03, 0.95, 5.0], [1e-4, 5e-5, 1.0]])
        hom = np.column_stack([self.src, np.ones(len(self.src))]) @ h.T
        dst = (hom[:, :2] / hom[:, 2:]).astype(np.float32)
        t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        self.assertEqual(t.kind, "homography")
        p = self.src[[17]]
        eps = 1e-4
        num = np.column_stack([
            (t.to_target(p + [eps, 0]) - t.to_target(p - [eps, 0]))[0] / (2 * eps),
            (t.to_target(p + [0, eps]) - t.to_target(p - [0, eps]))[0] / (2 * eps),
        ])
        np.testing.assert_allclose(t.jacobian(p)[0], num, rtol=1e-5)

    def test_nan_rows_are_excluded(self):
        dst = (self.src * 2.0 + 7.0).astype(np.float32)
        dst[::20] = np.nan
        t, valid = fit_anchor_to_target(self.src, dst, self.spot)
        self.assertEqual(int((~valid).sum()), len(self.src[::20]))
        self.assertEqual(t.kind, "homography")

    def test_inconsistent_coordinates_fall_back_to_local_affine(self):
        rng = np.random.default_rng(1)
        dst = self.src * 2.0 + rng.normal(0, 0.2 * 13.0, self.src.shape)
        with self.assertLogs("focus.registration._footprint", level="WARNING"):
            t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        self.assertEqual(t.kind, "local_affine")
        rows = np.arange(len(self.src))
        np.testing.assert_allclose(t.to_target(self.src, rows), dst, atol=1e-9)
        np.testing.assert_allclose(t.to_anchor(dst, rows), self.src, atol=1e-9)

    def test_too_few_points(self):
        with self.assertRaises(ValueError):
            fit_anchor_to_target(self.src[:2], self.src[:2], self.spot)


class ShapeTests(unittest.TestCase):
    def test_hexagonal_lattice_gives_ellipse(self):
        shape = footprint_shape(hex_lattice(angle=np.deg2rad(3)), np.array([55.0, 55.0]))
        self.assertEqual((shape.lattice, shape.kind), ("hexagonal", "ellipse"))
        self.assertAlmostEqual(shape.sigma, 27.5)

    def test_rotated_square_lattice(self):
        angle = np.deg2rad(0.65)
        shape = footprint_shape(square_lattice(angle=angle), np.array([8.0, 8.0]))
        self.assertEqual((shape.lattice, shape.kind), ("square", "rectangle"))
        self.assertAlmostEqual(shape.angle, angle, places=6)

    def test_rectangular_raster_and_exact_axes(self):
        i, j = np.meshgrid(np.arange(20), np.arange(30))
        pts = np.column_stack([i.ravel() * 10.0, j.ravel() * 5.0])
        shape = footprint_shape(pts, np.array([10.0, 5.0]))
        self.assertEqual((shape.lattice, shape.angle), ("rectangular", 0.0))
        self.assertEqual(footprint_shape(square_lattice(), np.array([10.0, 10.0])).angle, 0.0)

    def test_irregular_points(self):
        pts = np.random.default_rng(2).uniform(0, 1000, (500, 2))
        shape = footprint_shape(pts, np.array([20.0, 20.0]))
        self.assertEqual((shape.lattice, shape.kind, shape.angle), ("irregular", "rectangle", 0.0))
        self.assertEqual(classify_lattice(None), ("irregular", 0.0))


class MembershipTests(unittest.TestCase):
    def test_matches_legacy_when_frames_coincide(self):
        rng = np.random.default_rng(3)
        for anchor in (square_lattice(15, 10.0), np.round(rng.uniform(0, 150, (200, 2)) * 4) / 4):
            target = np.round(rng.uniform(-10, 160, (3000, 2)) * 4) / 4
            features = rng.normal(size=(len(target), 4))
            spot = np.array([10.0, 6.0])
            geom = AnchorGeometry.from_arrays(anchor, anchor.astype(np.float32), spot)
            self.assertEqual(geom.shape.kind, "rectangle")
            w = footprint_membership(geom, target)
            expected, members = legacy_interpolate(anchor, spot, target, features)
            for i in range(len(anchor)):
                self.assertEqual(set(w[i].indices.tolist()), members[i])
            np.testing.assert_allclose(row_normalize(w) @ features, expected, atol=1e-6)

    def test_scaling_both_frames_keeps_membership(self):
        rng = np.random.default_rng(4)
        anchor = square_lattice(12, 10.0)
        target = rng.uniform(0, 120, (2000, 2))
        spot = np.array([10.0, 10.0])
        base = footprint_membership(AnchorGeometry.from_arrays(anchor, anchor, spot), target, "ones")
        moved = footprint_membership(AnchorGeometry.from_arrays(anchor, anchor * 2 + [3, 9], spot), target * 2 + [3, 9], "ones")
        self.assertEqual((base != moved).nnz, 0)

    def test_pixel_membership_indexes_the_crop(self):
        anchor = np.array([[10.5, 10.5], [20.5, 14.5], [3.0, 3.0], [8.0, 30.0]])
        geom = AnchorGeometry.from_arrays(anchor, anchor, np.array([4.0, 4.0]))
        crop = (5, 6, 25, 20)
        w = pixel_footprint_membership(geom, crop, "ones")
        width = crop[2] - crop[0]
        for i, (cx, cy) in enumerate(anchor):
            cols, rows = np.meshgrid(np.arange(crop[0], crop[2]), np.arange(crop[1], crop[3]))
            inside = (np.abs(cols + 0.5 - cx) <= 2) & (np.abs(rows + 0.5 - cy) <= 2)
            expected = set(((rows[inside] - crop[1]) * width + cols[inside] - crop[0]).tolist())
            self.assertEqual(set(w[i].indices.tolist()), expected)


def write_anchor(path, spatial, mapped, target, spot_size):
    a = ad.AnnData(X=np.zeros((len(spatial), 1), dtype=np.float32))
    a.obsm["spatial"] = spatial.astype(np.float32)
    a.obsm[f"{target}_spatial"] = mapped.astype(np.float32)
    a.uns["spot_size"] = np.asarray(spot_size, dtype=np.float32)
    a.write_h5ad(path)


class RamanTests(unittest.TestCase):
    def test_footprint_honours_the_raman_pixel_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = w = 260
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            image = np.stack([np.ones((h, w), np.float32), xx + 0.5, yy + 0.5])
            raman = os.path.join(tmp, "raman.ome.tiff")
            tifffile.imwrite(raman, image, ome=True, metadata={"axes": "CYX", "PhysicalSizeX": 2.0, "PhysicalSizeY": 2.0})
            spatial = hex_lattice(n=4) - 400.0                    # µm, 55 µm Visium-like discs
            mapped = spatial / 2.0 + [10.0, 20.0]                   # 2 µm per Raman pixel
            anchor = os.path.join(tmp, "anchor.h5ad")
            write_anchor(anchor, spatial, mapped, "raman", [55.0, 55.0])

            files = RamanPixelInterpolationRegistration(tmp).register_dataset(
                {"s1": anchor}, {"s1": raman}, "st", "raman")
            out = ad.read_h5ad(files["s1"])
            np.testing.assert_allclose(out.X[:, 0], 1.0, rtol=1e-6)
            np.testing.assert_allclose(out.X[:, 1:], mapped, atol=0.05)
            self.assertEqual(out.uns[REGISTRATION_GEOMETRY_KEY]["footprint"], "ellipse")

            geom = AnchorGeometry.from_arrays(spatial, mapped, np.array([55.0, 55.0]))
            counts = np.diff(pixel_footprint_membership(geom, (0, 0, w, h), "ones").indptr)
            self.assertTrue(np.all(np.abs(counts - np.pi * 13.75 ** 2) < 2 * np.pi * 13.75))


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        rng = np.random.default_rng(5)
        self.anchor_files, self.target_files = {}, {}
        for sid in ("s1", "s2"):
            os.makedirs(os.path.join(self.root, sid), exist_ok=True)
            spatial = square_lattice(10, 20.0)
            anchor = os.path.join(self.root, sid, "anchor.h5ad")
            write_anchor(anchor, spatial, spatial, "st", [20.0, 20.0])
            target_xy = rng.uniform(0, 200, (800, 2))
            target = ad.AnnData(X=sp.csr_matrix(rng.poisson(2, (800, 5)).astype(np.float32)))
            target.obsm["spatial"] = target_xy.astype(np.float32)
            path = os.path.join(self.root, sid, "target.h5ad")
            target.write_h5ad(path)
            self.anchor_files[sid], self.target_files[sid] = anchor, path

    def tearDown(self):
        self.tmp.cleanup()

    def run_engine(self, engine=SpotInterpolationRegistration, samples=("s1", "s2")):
        return engine(self.root).register_dataset(
            {s: self.anchor_files[s] for s in samples}, {s: self.target_files[s] for s in samples}, "ref", "st")

    def test_cache_reuse_and_invalidation(self):
        files = self.run_engine()
        out = MODALITY_REGISTRATION(self.root, "s1", "st", "h5ad")
        self.assertEqual(files["s1"], out)
        key = cached_fingerprint(out, key=REGISTRATION_FINGERPRINT_KEY)
        self.assertIsNotNone(key)
        mtime = os.stat(out).st_mtime_ns
        self.run_engine()
        self.assertEqual(os.stat(out).st_mtime_ns, mtime)

        # A legacy output (right type and size, no fingerprint) is recomputed.
        legacy = ad.read_h5ad(out)
        del legacy.uns[REGISTRATION_FINGERPRINT_KEY]
        legacy.write_h5ad(out)
        self.run_engine()
        self.assertEqual(cached_fingerprint(out, key=REGISTRATION_FINGERPRINT_KEY), key)

        # Moving the aligned anchor changes the fingerprint.
        a = ad.read_h5ad(self.anchor_files["s1"])
        a.obsm["st_spatial"] = a.obsm["st_spatial"] + 1.0
        a.write_h5ad(self.anchor_files["s1"])
        self.run_engine()
        self.assertNotEqual(cached_fingerprint(out, key=REGISTRATION_FINGERPRINT_KEY), key)

    def test_merged_follows_the_sample_set(self):
        merged = MODALITY_REGISTRATION_MERGED(self.root, "st", "h5ad")
        self.run_engine()
        self.assertEqual(ad.read_h5ad(merged).n_obs, 200)
        self.run_engine(samples=("s1",))
        self.assertEqual(ad.read_h5ad(merged).n_obs, 100)
        self.assertEqual(set(ad.read_h5ad(merged).uns[REGISTRATION_GEOMETRY_KEY]), {"s1"})

    def test_aggregation_sums_members(self):
        files = self.run_engine(SpotAggregationRegistration, ("s1",))
        out = ad.read_h5ad(files["s1"])
        target = ad.read_h5ad(self.target_files["s1"])
        anchor = ad.read_h5ad(self.anchor_files["s1"])
        xy = target.obsm["spatial"]
        for i in (0, 37, 99):
            cx, cy = anchor.obsm["spatial"][i]
            inside = (np.abs(xy[:, 0] - cx) <= 10) & (np.abs(xy[:, 1] - cy) <= 10)
            np.testing.assert_allclose(out.X[i].toarray().ravel(), target.X[inside].sum(0).A1)


if __name__ == "__main__":
    unittest.main()
