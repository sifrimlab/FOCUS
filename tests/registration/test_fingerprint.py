"""Fingerprint behavioral scenarios."""

import os
import tempfile

import anndata as ad
import numpy as np
import scipy.sparse as sp

from focus.io.paths import registration_file, merged_registration_file
from focus.io.provenance import cached_fingerprint
from focus.registration.provenance import (
    REGISTRATION_FINGERPRINT_KEY,
    REGISTRATION_GEOMETRY_KEY,
)
from focus.registration.spot_interpolation import SpotInterpolationRegistration
from tests.registration.data import square_lattice, write_anchor


class TestFingerprint:
    """Behavioral contracts for fingerprint."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
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
            target = ad.AnnData(
                X=sp.csr_matrix(rng.poisson(2, (800, 5)).astype(np.float32))
            )
            target.obsm["spatial"] = target_xy.astype(np.float32)
            target.uns["spot_size"] = np.array([8.0, 8.0], dtype=np.float32)
            path = os.path.join(self.root, sid, "target.h5ad")
            target.write_h5ad(path)
            self.anchor_files[sid], self.target_files[sid] = anchor, path

    def teardown_method(self) -> None:
        """Release the isolated fixture resources."""
        self.tmp.cleanup()

    def run_engine(self, samples: tuple[str, ...] = ("s1", "s2")) -> dict[str, str]:
        """Register the selected fixture samples and return the strategy output paths."""
        return SpotInterpolationRegistration(self.root).register_dataset(
            {s: self.anchor_files[s] for s in samples},
            {s: self.target_files[s] for s in samples},
            "ref",
            "st",
        )

    def test_cache_reuse_and_invalidation(self) -> None:
        """Verify cache reuse and invalidation."""
        files = self.run_engine()
        out = registration_file(self.root, "s1", "st", "h5ad")
        assert files["s1"] == out
        key = cached_fingerprint(out, key=REGISTRATION_FINGERPRINT_KEY)
        assert key is not None
        mtime = os.stat(out).st_mtime_ns
        self.run_engine()
        assert os.stat(out).st_mtime_ns == mtime

        # A legacy output (right type and size, no fingerprint) is recomputed.
        legacy = ad.read_h5ad(out)
        del legacy.uns[REGISTRATION_FINGERPRINT_KEY]
        legacy.write_h5ad(out)
        self.run_engine()
        assert cached_fingerprint(out, key=REGISTRATION_FINGERPRINT_KEY) == key

        # Moving the aligned anchor changes the fingerprint.
        a = ad.read_h5ad(self.anchor_files["s1"])
        a.obsm["st_spatial"] = a.obsm["st_spatial"] + 1.0
        a.write_h5ad(self.anchor_files["s1"])
        self.run_engine()
        assert cached_fingerprint(out, key=REGISTRATION_FINGERPRINT_KEY) != key

    def test_merged_follows_the_sample_set(self) -> None:
        """Verify merged follows the sample set."""
        merged = merged_registration_file(self.root, "st", "h5ad")
        self.run_engine()
        assert ad.read_h5ad(merged).n_obs == 200
        self.run_engine(samples=("s1",))
        assert ad.read_h5ad(merged).n_obs == 100
        assert set(ad.read_h5ad(merged).uns[REGISTRATION_GEOMETRY_KEY]) == {"s1"}

    def test_targets_are_read_from_the_merged_panel(self) -> None:
        """Samples with different panels register onto the merged union panel."""
        panels = {
            "s1": ["g0", "g1", "g2", "g3", "g4"],
            "s2": ["g0", "g1", "g2", "g3", "g5"],
        }
        parts = []
        for sid, genes in panels.items():
            target = ad.read_h5ad(self.target_files[sid])
            target.var_names = genes
            target.obs_names = [f"{sid}_{i}" for i in range(target.n_obs)]
            target.obs["sample_id"] = sid
            target.write_h5ad(self.target_files[sid])
            parts.append(target)
        merged = ad.concat(parts, join="outer", fill_value=0)
        merged.uns["spot_size"] = {sid: np.array([8.0, 8.0]) for sid in panels}
        merged_path = os.path.join(self.root, "merged_target.h5ad")
        merged.write_h5ad(merged_path)
        files = SpotInterpolationRegistration(self.root).register_dataset(
            self.anchor_files, {**self.target_files, "merged": merged_path}, "ref", "st"
        )
        union = ["g0", "g1", "g2", "g3", "g4", "g5"]
        assert list(ad.read_h5ad(files["merged"]).var_names) == union
        s2 = ad.read_h5ad(files["s2"])
        assert list(s2.var_names) == union
        np.testing.assert_array_equal(s2[:, "g4"].X.toarray().ravel(), 0)

    def test_a_normalized_target_is_apportioned_then_normalized(self) -> None:
        """Counts are shared by overlap area, then the merged file is normalized once, globally."""
        from focus.preprocessing.transcriptomics.normalization import normalize

        # Target spots of 10 um tile the section; anchors of 20 um start 5 um before a tile edge.
        tile = square_lattice(20, 10.0)
        rng = np.random.default_rng(1)
        parts = []
        for sid in ("s1", "s2"):
            target = ad.AnnData(
                X=sp.csr_matrix(rng.poisson(3, (len(tile), 5)).astype(np.float32))
            )
            target.obsm["spatial"] = tile.astype(np.float32)
            target.obs_names = [f"{sid}_{i}" for i in range(target.n_obs)]
            target.obs["sample_id"] = sid
            parts.append(target)
        merged = ad.concat(parts)
        merged.uns["spot_size"] = {sid: np.array([10.0, 10.0]) for sid in ("s1", "s2")}
        merged.uns["focus_normalization"] = normalize(merged, "log_normalize")
        merged_path = os.path.join(self.root, "merged_target.h5ad")
        merged.write_h5ad(merged_path)
        files = SpotInterpolationRegistration(self.root).register_dataset(
            self.anchor_files, {**self.target_files, "merged": merged_path}, "ref", "st"
        )
        sample = ad.read_h5ad(files["s1"])
        counts = merged.layers["raw"][:400]
        # Anchor i spans [20 i - 5, 20 i + 15] on each axis: tiles 2i-1 (half), 2i (full), 2i+1 (half...)
        ix, iy = 3, 4
        share = np.zeros(400)
        for tx in range(20):
            for ty in range(20):
                wx = _axis_share(tx, ix)
                wy = _axis_share(ty, iy)
                share[ty * 20 + tx] = wx * wy
        anchor_row = iy * 10 + ix
        np.testing.assert_allclose(
            sample.layers["raw"][anchor_row].toarray().ravel(),
            counts.T @ share,
            rtol=1e-4,
        )
        assert sample.obs["coverage"].iloc[anchor_row] == 1.0
        out = ad.read_h5ad(files["merged"])
        raw = out.layers["raw"].toarray()
        library = raw.sum(axis=1)
        median = np.median(library[library > 0])
        factors = np.divide(library, median)
        expected = np.log1p(
            np.divide(
                raw,
                factors[:, None],
                out=np.zeros_like(raw),
                where=factors[:, None] > 0,
            )
        )
        np.testing.assert_allclose(out.X.toarray(), expected, rtol=1e-5)
        assert out.uns["focus_normalization"]["method"] == "log_normalize"


def _axis_share(tile: int, anchor: int) -> float:
    """Share of the 10 um tile ``tile`` (centre 10 tile + 5) inside the 20 um anchor ``anchor``."""
    lo, hi = 20 * anchor - 5, 20 * anchor + 15
    return max(0.0, min(hi, 10 * tile + 10) - max(lo, 10 * tile)) / 10.0
