"""Validate one spatial count container and coordinate its cached preprocessing output."""

from focus.preprocessing.transcriptomics.parameters import StSampleSettings
from focus.preprocessing.transcriptomics.counts import resolve_counts
from focus.io.provenance import input_signature
from focus.preprocessing.transcriptomics.provenance import sample_fingerprint
from focus.preprocessing.transcriptomics.sample_operations import write_sample
from focus.preprocessing.transcriptomics.technology import technology

from numpy.typing import ArrayLike
from focus.runtime.reporting import StepReporter

import os
import anndata as ad
import numpy as np
import scanpy as sc

from focus.io.paths import preprocessed_file
from focus.config.options import StCapture, StNormalization, StUnit
from focus.runtime.reporter_context import get_reporter
from focus.io.provenance import cached_fingerprint
from focus.preprocessing.base import SampleSource


class SpatialTranscriptomic(SampleSource):
    """
    One spatial transcriptomics sample of any technology.

    The input is one AnnData file holding raw counts (in ``.X``, ``layers['counts']``,
    ``layers['raw']`` or ``.raw``), ``.obsm['spatial']`` in µm and ``.uns['spot_size']`` in µm.
    """

    _H5AD_COMPRESSION = "gzip"

    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
        """Create sample output directories and retain its transcriptomics input directory.

        Parameters
        ----------
        source_path : str
            Existing readable dataset root.
        sample_id : str
            Sample directory identifier.
        modality_name : str
            Count-container input subdirectory and processed-output name."""
        super().__init__(source_path, sample_id, modality_name)
        self.input_path = os.path.join(source_path, sample_id, modality_name)

    def _input_file(self) -> str:
        """Locate the sample's single ``.h5ad`` input; raise when there is none or several."""
        files = sorted(
            os.path.join(self.input_path, f)
            for f in os.listdir(self.input_path)
            if f.endswith(".h5ad")
        )
        if not files:
            raise FileNotFoundError(f"No .h5ad file found in {self.input_path}")
        if len(files) > 1:
            names = ", ".join(os.path.basename(f) for f in files)
            raise ValueError(
                f"Sample {self.sample_id}: {len(files)} .h5ad files in {self.input_path} "
                f"({names}); keep exactly one"
            )
        return files[0]

    def load_data(self, reporter: StepReporter | None = None) -> ad.AnnData:
        """
        Load and validate the sample's AnnData.

        Requires finite ``.obsm['spatial']`` of shape (n_obs, 2) and ``.uns['spot_size']``
        (normalized to a positive float32 ``(2,)`` array). The raw counts are resolved into a
        CSR ``.X`` (``resolve_counts``); every other input matrix is dropped. Duplicate gene names
        are made unique.

        Returns
        -------
        adata : ad.AnnData
            The validated AnnData, raw counts in ``.X``.
        """
        reporter = reporter or get_reporter()
        adata = sc.read_h5ad(self._input_file())

        if "spatial" not in adata.obsm:
            raise ValueError(
                f"Sample {self.sample_id}: AnnData is missing .obsm['spatial']. "
                "Spatial coordinates are required for spatial transcriptomics preprocessing."
            )
        adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)
        coords = adata.obsm["spatial"]
        if coords.shape != (adata.n_obs, 2) or not np.isfinite(coords).all():
            raise ValueError(
                "ST spatial coordinates must be finite with shape (n_obs, 2)"
            )

        if "spot_size" not in adata.uns:
            raise ValueError(
                f"Sample {self.sample_id}: AnnData is missing .uns['spot_size'] "
                "(spot, bin or cell diameter [x, y] in µm)."
            )
        adata.uns["spot_size"] = self._normalize_spot_size(adata.uns["spot_size"])
        if not np.isfinite(adata.uns["spot_size"]).all() or np.any(
            adata.uns["spot_size"] <= 0
        ):
            raise ValueError("spot_size must contain positive finite dimensions")

        adata = resolve_counts(adata, self.sample_id)
        if not adata.var_names.is_unique:
            n_duplicated = int(adata.var_names.duplicated().sum())
            adata.var_names_make_unique()
            reporter.warning(
                f"{n_duplicated} duplicated gene names were made unique with a numeric suffix"
            )
        return adata

    @staticmethod
    def _normalize_spot_size(spot_size: ArrayLike) -> np.ndarray:
        """
        Normalize spot_size to a float32 array of shape (2,).
        - scalar → [val, val]
        - array of 1 → [val, val]
        - array of 2 → as-is
        """
        if isinstance(spot_size, (int, float)):
            return np.array([float(spot_size), float(spot_size)], dtype=np.float32)

        arr = np.asarray(spot_size, dtype=np.float32).flatten()
        if arr.size == 1:
            return np.array([float(arr[0]), float(arr[0])], dtype=np.float32)
        elif arr.size == 2:
            return arr.astype(np.float32)
        else:
            raise ValueError(
                f"spot_size must be a scalar or array of 1-2 values, got shape {arr.shape}"
            )

    def preprocess_data(
        self,
        capture: str = StCapture.SEQUENCING,
        unit: str = StUnit.SPOT,
        normalization: str = StNormalization.AUTO,
        quality_control: bool = True,
        remove_mitochondrial_genes: bool = False,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> str:
        """
        Preprocess a single spatial transcriptomic sample; the output keeps raw counts.

        Pipeline (``docs/scientific/st_methods.md`` §3):
        1. Load and validate; resolve the raw counts; make gene names unique
        2. Remove control features, recording their share in ``obs['pct_counts_control']``
        3. Flag mitochondrial genes (``.var['mt']``)
        4. Quality control: out-of-tissue and zero-count spots, then the technology's outliers
           (``quality_control``)
        5. QC metrics (``scanpy.pp.calculate_qc_metrics``) with mitochondrial genes present
        6. Spot or cell area in ``obs['focus_area']`` (for area normalization)
        7. Optional mitochondrial gene removal
        8. Alignment cluster labels with the unit's preset, on the configured normalization
        9. Save (sparse CSR, gzip, atomic). Normalization is applied to the merged dataset only.

        Parameters
        ----------
        capture : str
            ``sequencing`` or ``imaging`` (``StCapture``).
        unit : str
            ``spot``, ``bin`` or ``cell`` (``StUnit``).
        normalization : str
            Merged normalization (``StNormalization``); the alignment labels are computed on it
            (``none``: the technology's ``auto`` method). The output keeps raw counts.
        quality_control : bool
            Remove the technology's outlier spots (structural cleanup always runs).
        remove_mitochondrial_genes : bool
            Drop the genes flagged in ``.var['mt']`` (``MT-`` / ``MT.`` prefix, any case).
        force_recomputing : bool
            Whether to force recomputing even if output exists.
        step_reporter : StepReporter | None
            Unified reporter for console / log-file / GUI output.

        Returns
        -------
        output_file : str
            Path to the preprocessed AnnData file.
        """

        reporter = step_reporter or get_reporter()
        output_file = preprocessed_file(
            self.source_path, self.sample_id, self.modality_name, "h5ad"
        )

        tech = technology(capture, unit)  # validate before any work
        settings: StSampleSettings = {
            "capture": capture,
            "unit": unit,
            "normalization": normalization,
            "quality_control": quality_control,
            "remove_mitochondrial_genes": remove_mitochondrial_genes,
        }
        signature = input_signature(self._input_file(), output_file)
        cache_key = sample_fingerprint(self, settings, signature)
        self.preprocessing_changed = (
            force_recomputing or cached_fingerprint(output_file) != cache_key
        )
        if not self.preprocessing_changed:
            reporter.cached()
            return output_file
        write_sample(self, tech, settings, reporter, cache_key, signature, output_file)
        return output_file
