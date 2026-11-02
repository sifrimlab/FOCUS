"""Bind MSI samples, run settings and lipid references to the preprocessing workflow."""

from focus.preprocessing.msi.lipid_database import read_lipid_database

from focus.preprocessing.msi.run_state import MsiRunSettings
from focus.preprocessing.msi.workflow import execute_dataset

from focus.config.types import MassSetting

from focus.preprocessing.msi.validation import validate_parameters
from focus.runtime.reporting import StepReporter

import numpy as np
from focus.runtime.reporter_context import get_reporter
import pandas as pd

from focus.config.options import (
    MsiIntensityNormalization,
    MsiSampleType,
    SpotResolution,
)
from focus.preprocessing.base import DatasetState
from focus.preprocessing.msi.sample import MsiSample


class MsiDataset(DatasetState[MsiSample]):
    """Coordinate MSI reference selection, mass binning, caching, and dataset persistence."""

    lipid_annotations: dict[str, np.ndarray]
    lipid_annotation_db: pd.DataFrame | None

    _LEIDEN_RESOLUTION = 0.5
    _H5AD_COMPRESSION = "gzip"
    _H5AD_INTERMEDIATE_COMPRESSION = (
        "gzip"  # per-sample files; matches project-wide convention
    )

    def __init__(
        self,
        path: str,
        samples: list[MsiSample],
        lipid_annotation_db: str | None = None,
    ) -> None:
        """
        MSI dataset containing multiple samples. This class provide utilities to preprocess the raw experiments
        and generate an aligned and corrected AnnData object.

        Parameters
        ----------
        path : str
                The root path where the samples are stored.
        samples : list[MsiSample]
                List of MsiSample objects.
        lipid_annotation_db : str | None
                Path to the lipid annotation database. If None, no lipid annotation will be performed.
                The file can be a CSV or a JSON and it must contain three columns: db_name, ionized_mass, ion_mode.
        """

        super().__init__(path, samples)
        self._lipid_annotation_db_path = lipid_annotation_db

        if not all(isinstance(sample, MsiSample) for sample in samples):
            raise TypeError("Invalid input type. Expected list of MsiSample objects.")

        self.reference_mz: dict[str, np.ndarray] = {}
        self.interpolated: dict[str, dict[str, np.ndarray]] = {}
        self.normalized: dict[str, dict[str, np.ndarray]] = {}
        self.foreground_masks: dict[str, np.ndarray] = {}

        self.lipid_annotation_db = read_lipid_database(lipid_annotation_db)

    def process_dataset(
        self,
        mass_tolerance: int = 10,
        frequency_threshold: float = 0.01,
        intensity_normalization: str = MsiIntensityNormalization.NONE,
        recalibration_reference: MassSetting = None,
        internal_standard_mz: MassSetting = None,
        detect_background: bool = False,
        sample_type: str = MsiSampleType.TISSUE,
        resolution: str = SpotResolution.STANDARD,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Process the dataset by aligning the M/Z values across all samples and interpolating the intensities.

        Parameters
        ----------
        mass_tolerance : int
                Adaptive mass tolerance in ppm for grouping M/Z values.
        frequency_threshold : float
                Frequency threshold for filtering M/Z values.
        intensity_normalization : MsiIntensityNormalization
                Type of intensity normalization to apply, one of 'none', 'tic', 'log',
                'clr', 'tic_mean_scaled', 'median' or 'internal_standard'. All methods are applied
                independently per sample and ion mode. 'tic' divides each spectrum by its total ion
                current (rows sum to 1); 'tic_mean_scaled' rescales each spectrum to the mean total
                ion current over the sample's spots, preserving absolute intensity scale
                (equivalent to 'tic' times a per-sample constant; not comparable across samples);
                'median' divides each spectrum by the median of its nonzero features;
                'internal_standard' divides each spectrum by the summed intensity of the
                internal standard features (requires internal_standard_mz).
        recalibration_reference : dict[MsiIonMode, np.ndarray] | str | None
                Reference M/Z values for recalibration per ion mode (see parse_mz_by_mode for the
                accepted forms). Selected from the dataset when None.
        internal_standard_mz : dict[MsiIonMode, np.ndarray] | str | None
                M/Z values of the internal standards sprayed over the sections, per ion mode. They are
                always included in the automatically selected recalibration references, and are
                required by 'internal_standard' normalization.
        detect_background : bool
                If True, detects and stores the foreground mask for each sample. If False, the foreground mask covers all the spots.
                Only effective when a lipid annotation database is set.
        sample_type : str
                Background detection strategy. "tissue" fits a GMM + BIC model on spectral complexity features and applies
                morphological cleanup. "microgrid" detects sparse cells as local outliers against a background ion model
                (see focus.preprocessing.msi.microgrid.detect_microgrid_spots).
        resolution : str
                SpotResolution preset for the alignment clustering: 'standard' (multicellular pixels)
                or 'sub_cellular' (weak high-resolution pixels: wider neighbourhood smoothing).
        force_recomputing : bool
                If True, forces recomputation of the reference M/Z vectors and interpolation even if they were already computed.
                If False, the computation is skipped if the merged dataset already exists.

        Returns
        -------
        processed_samples : dict[str, str]
                A dictionary mapping sample IDs to the paths of their processed files.
        """
        recalibration_reference, internal_standard_mz = validate_parameters(
            self,
            mass_tolerance,
            frequency_threshold,
            intensity_normalization,
            recalibration_reference,
            internal_standard_mz,
        )
        reporter = step_reporter or get_reporter()
        settings = MsiRunSettings(
            mass_tolerance,
            frequency_threshold,
            intensity_normalization,
            recalibration_reference,
            internal_standard_mz,
            detect_background,
            sample_type,
            resolution,
            force_recomputing,
        )
        return execute_dataset(self, settings, reporter)
