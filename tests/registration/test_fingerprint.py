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
from focus.registration.spot_aggregation import SpotAggregationRegistration
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
            path = os.path.join(self.root, sid, "target.h5ad")
            target.write_h5ad(path)
            self.anchor_files[sid], self.target_files[sid] = anchor, path

    def teardown_method(self) -> None:
        """Release the isolated fixture resources."""
        self.tmp.cleanup()

    def run_engine(
        self,
        engine: type[SpotInterpolationRegistration]
        | type[SpotAggregationRegistration] = SpotInterpolationRegistration,
        samples: tuple[str, ...] = ("s1", "s2"),
    ) -> dict[str, str]:
        """Register the selected fixture samples and return the strategy output paths."""
        return engine(self.root).register_dataset(
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

    def test_aggregation_sums_members(self) -> None:
        """Verify aggregation sums members."""
        files = self.run_engine(SpotAggregationRegistration, ("s1",))
        out = ad.read_h5ad(files["s1"])
        target = ad.read_h5ad(self.target_files["s1"])
        anchor = ad.read_h5ad(self.anchor_files["s1"])
        xy = target.obsm["spatial"]
        for i in (0, 37, 99):
            cx, cy = anchor.obsm["spatial"][i]
            inside = (np.abs(xy[:, 0] - cx) <= 10) & (np.abs(xy[:, 1] - cy) <= 10)
            np.testing.assert_allclose(
                out.X[i].toarray().ravel(), target.X[inside].sum(0).A1
            )
