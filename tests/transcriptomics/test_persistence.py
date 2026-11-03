"""Persistence behavioral scenarios."""

from unittest.mock import patch

import tempfile
from pathlib import Path

import anndata as ad
import numpy as np

from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic
from focus.preprocessing.transcriptomics.dataset import SpatialTranscriptomicDataset
from focus.alignment.coordinator import DirectMappingAligner
from focus.io.provenance import cached_fingerprint
from tests.transcriptomics.data import expression


class TestPersistence:
    """Behavioral contracts for persistence."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        folder = self.root / "sample" / "ST"
        folder.mkdir(parents=True)
        self.input = folder / "input.h5ad"
        self.adata = ad.AnnData(expression(30))
        self.adata.var_names = [f"g{i}" for i in range(60)]
        self.adata.obsm["spatial"] = np.column_stack(
            [np.arange(30) * 16, np.zeros(30)]
        ).astype("float32")
        self.adata.uns["spot_size"] = 16.0
        self.adata.write_h5ad(self.input)
        self.sample = SpatialTranscriptomic(str(self.root), "sample", "ST")

    def teardown_method(self) -> None:
        """Release the isolated fixture resources."""
        if hasattr(self, "path_patch"):
            self.path_patch.stop()
        self.tmp.cleanup()

    def test_cache_tracks_settings_input_and_preserves_data(self) -> None:
        """Verify cache tracks settings input and preserves data."""
        path = self.sample.preprocess_data()
        first = cached_fingerprint(path)
        mtime = Path(path).stat().st_mtime_ns
        self.sample.preprocess_data()
        assert not self.sample.preprocessing_changed
        assert mtime == Path(path).stat().st_mtime_ns
        self.sample.preprocess_data(unit="bin")
        assert first != cached_fingerprint(path)
        output = ad.read_h5ad(path)
        assert (output.X != self.adata.X).nnz == 0
        np.testing.assert_array_equal(
            output.obsm["spatial"], self.adata.obsm["spatial"]
        )
        assert output.var_names.tolist() == self.adata.var_names.tolist()
        changed = cached_fingerprint(path)
        self.adata.X.data[0] += 1
        self.adata.write_h5ad(self.input)
        self.sample.preprocess_data(unit="bin")
        assert changed != cached_fingerprint(path)

    def test_normalization_drives_the_labels_and_merged_provenance_survives(
        self,
    ) -> None:
        """Samples keep raw counts; their labels are computed on the configured normalization."""
        dataset = SpatialTranscriptomicDataset(str(self.root), [self.sample])
        files = dataset.process_dataset(normalization="none")
        plain = ad.read_h5ad(files["sample"])
        # none clusters on the technology's auto method (log_normalize for sequencing).
        assert plain.uns["focus_clustering"]["representation"] == "log_normalize"
        files = dataset.process_dataset(normalization="none")
        assert not self.sample.preprocessing_changed
        files = dataset.process_dataset(normalization="cp10k_log")
        assert self.sample.preprocessing_changed
        sample = ad.read_h5ad(files["sample"])
        assert sample.uns["focus_clustering"]["representation"] == "cp10k_log"
        assert not sample.layers and (sample.X != self.adata.X).nnz == 0
        merged = ad.read_h5ad(files["merged"])
        kept = self.adata[:, merged.var_names]
        assert (merged.layers["raw"] != kept.X).nnz == 0
        assert merged.X.dtype == np.float32
        assert merged.uns["focus_normalization"]["method"] == "cp10k_log"
        assert "sample" in merged.uns["focus_clustering"]
        assert "selected_features" in merged.uns["focus_clustering"]["sample"]

    def test_old_alignment_is_refreshed_from_preprocessing(self) -> None:
        """Verify old alignment is refreshed from preprocessing."""
        source = self.sample.preprocess_data()
        old = ad.read_h5ad(source)
        old.obs["cluster"] = "old"
        old.obsm["other_spatial"] = old.obsm["spatial"] + 10
        aligned = self.root / "aligned.h5ad"
        old.write_h5ad(aligned)
        aligner = DirectMappingAligner.__new__(DirectMappingAligner)
        aligner._target_modality = {"sample": source}
        aligner._reference_modality = {"sample": source}
        aligner._target_modality_type = "st"
        aligner._reference_modality_type = "msi"
        aligner._reference_modality_name = "other"
        path_patch = patch.object(
            aligner.cache, "_aligned_output_path", return_value=str(aligned)
        )
        path_patch.start()
        self.path_patch = path_patch
        assert not aligner.cache._cache_matches("sample")
        fresh = aligner.cache._fresh_aligned_adata("sample")
        assert "old" not in fresh.obs["cluster"].astype(str).tolist()
        np.testing.assert_array_equal(
            fresh.obsm["other_spatial"], old.obsm["other_spatial"]
        )
        fresh.uns["focus_alignment_inputs"] = {
            "other": aligner.cache._input_key("sample")
        }
        fresh.write_h5ad(aligned)
        assert aligner.cache._cache_matches("sample")
