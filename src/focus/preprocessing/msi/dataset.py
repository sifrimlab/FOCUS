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
        self.reference_sigma: dict[str, np.ndarray] = {}
        self.interpolated: dict[str, dict[str, np.ndarray]] = {}
        self.normalized: dict[str, dict[str, np.ndarray]] = {}
        self.foreground_masks: dict[str, np.ndarray] = {}

        self.lipid_annotation_db = read_lipid_database(lipid_annotation_db)

    def process_dataset(
        self,
        mass_tolerance: float = 10,
        intensity_normalization: str = MsiIntensityNormalization.NONE,
        recalibration_reference: MassSetting = None,
        internal_standard_mz: MassSetting = None,
        sample_type: str = MsiSampleType.TISSUE,
        resolution: str = SpotResolution.STANDARD,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Recalibrate the samples, select their m/z features, merge them into one backbone and bin
        every spectrum onto it (docs/scientific/msi_methods.md).

        Parameters
        ----------
        mass_tolerance : float
                Mass tolerance in ppm: the feature kernel, the backbone merge span, the binning reach
                and the annotation window.
        intensity_normalization : MsiIntensityNormalization
                Row normalization of the binned intensities, applied per sample and ion mode at a
                fixed scale: 'none', 'tic' (rows sum to the number of features), 'median' (median of
                the nonzero features is 1), 'rms' (root mean square is 1) or 'internal_standard'
                (standard intensity is 1; requires internal_standard_mz).
        recalibration_reference : dict[MsiIonMode, np.ndarray] | str | None
                Reference M/Z values for recalibration per ion mode (see parse_mz_by_mode for the
                accepted forms). Selected from the dataset when None.
        internal_standard_mz : dict[MsiIonMode, np.ndarray] | str | None
                M/Z values of the internal standards sprayed over the sections, per ion mode. They are
                always included in the automatically selected recalibration references, and are
                required by 'internal_standard' normalization.
        sample_type : MsiSampleType
                Experiment type: 'tissue' (clean background), 'tissue_noisy' (noisy background),
                'cell_culture' (gradient domains) or 'microgrid' (isolated single cells); calibrates
                the foreground detection, its cleanup and the alignment clustering mode
                (focus.preprocessing.msi.technology).
        resolution : str
                SpotResolution of the alignment clustering: 'standard' (native pixels) or
                'sub_cellular' (weak high-resolution pixels, summed into lattice blocks until the
                median block detects enough features). Not used by 'microgrid'.
        force_recomputing : bool
                If True, recompute even when every output carries the current fingerprint.

        Returns
        -------
        processed_samples : dict[str, str]
                A dictionary mapping sample IDs to the paths of their processed files.
        """
        recalibration_reference, internal_standard_mz = validate_parameters(
            self,
            mass_tolerance,
            intensity_normalization,
            sample_type,
            recalibration_reference,
            internal_standard_mz,
        )
        reporter = step_reporter or get_reporter()
        settings = MsiRunSettings(
            mass_tolerance,
            intensity_normalization,
            recalibration_reference,
            internal_standard_mz,
            sample_type,
            resolution,
            force_recomputing,
        )
        return execute_dataset(self, settings, reporter)
