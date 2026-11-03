"""Coordinate transcriptomic sample processing, merged gene selection and global normalization."""

from focus.preprocessing.transcriptomics.run_settings import StRunSettings
from focus.preprocessing.transcriptomics.validation import validate_settings
from focus.preprocessing.transcriptomics.workflow import execute_dataset

from focus.runtime.reporting import StepReporter


from focus.config.options import StCapture, StNormalization, StUnit
from focus.runtime.reporter_context import get_reporter
from focus.preprocessing.base import DatasetState
from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic


class SpatialTranscriptomicDataset(DatasetState[SpatialTranscriptomic]):
    """
    Spatial transcriptomics samples of one modality.

    Each sample is cleaned and quality-controlled on its raw counts; the merged dataset has the
    union panel restricted to the technology's gene rule and one global normalization.
    """

    _H5AD_COMPRESSION = "gzip"

    def __init__(self, path: str, samples: list[SpatialTranscriptomic]) -> None:
        """Retain the dataset root and ordered modality sample objects.

        Parameters
        ----------
        path : str
            Root used for existing per-sample and merged output paths.
        samples : list
            Modality sample objects retained by reference."""
        super().__init__(path, samples)

    def process_dataset(
        self,
        capture: str = StCapture.SEQUENCING,
        unit: str = StUnit.SPOT,
        normalization: str = StNormalization.AUTO,
        quality_control: bool = True,
        remove_mitochondrial_genes: bool = False,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Process and combine multiple spatial transcriptomic samples.

        Pipeline (``docs/scientific/st_methods.md``):
        1. Preprocess each sample on raw counts (``SpatialTranscriptomic.preprocess_data``)
        2. Select the genes detected in enough spots of at least one sample (technology rule)
        3. Concatenate on disk with an outer join (union panel) and keep the selected genes
        4. Recompute library-size and gene QC on the final panel
        5. Normalize the merged counts once (``normalization``; ``auto`` follows the technology),
           keeping the counts in ``.layers["raw"]``
        6. Save (sparse CSR, gzip, atomic), with ``.uns["spot_size"]`` per sample

        Parameters
        ----------
        capture : str
            ``sequencing`` or ``imaging`` (``StCapture``).
        unit : str
            ``spot``, ``bin`` or ``cell`` (``StUnit``).
        normalization : str
            ``StNormalization`` value applied to the merged dataset.
        quality_control : bool
            Remove the technology's outlier spots.
        remove_mitochondrial_genes : bool
            Drop mitochondrial genes (``MT-`` / ``MT.`` prefix) per sample.
        force_recomputing : bool
            Whether to force recomputing even if output exists.

        Returns
        -------
        processed_samples : dict[str, str]
            Maps sample IDs (and "merged") to output file paths.
        """

        settings = StRunSettings(
            capture,
            unit,
            normalization,
            quality_control,
            remove_mitochondrial_genes,
            force_recomputing,
        )
        validate_settings(settings)
        reporter = step_reporter or get_reporter()
        return execute_dataset(self, settings, reporter)
