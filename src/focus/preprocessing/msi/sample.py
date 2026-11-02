"""Hold MSI input metadata, spectra, recalibration models and foreground selection state."""

from focus.preprocessing.msi.source import mode_paths

from focus.preprocessing.msi.payload import (
    read_vectors,
    select_foreground,
    recalibrate_vectors,
    filter_vectors,
)

from focus.preprocessing.msi.metadata_loading import initialize_sample

from focus.preprocessing.msi.types import SampleMetadata

from functools import cached_property

from focus.preprocessing.msi.imzml import ImzmlReader
from focus.preprocessing.msi.binary import BinaryValidator
from focus.preprocessing.msi.coordinates import MsiCoordinates
from focus.preprocessing.msi.foreground import MsiForeground
import numpy as np
import pandas as pd

from focus.config.keys import MsiMetadata
from focus.config.options import MsiSampleType, MsiIonMode
from focus.preprocessing.base import SampleSource
from focus.preprocessing.msi.recalibration import (
    MsiRecalibration,
)


class MsiSample(SampleSource):
    """Source metadata, spectra, and lifecycle coordination for one MSI sample."""

    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
        double_ion_mode: bool = False,
        ion_mode: str | None = None,
    ) -> None:
        """
        Load and preprocess a MSI sample using ImzML and IBD files.

        Parameters
        ----------
        source_path : str
                Path to the data source directory. If double_ion_mode is True, this should be the parent directory containing both ion mode subdirectories.
        sample_id : str
                Sample ID.
        modality_name : str
                Name of the modality.
        double_ion_mode : bool
                If True, the sample contains both positive and negative ion mode data in separate subdirectories with format input_path/pos and input_path/neg.
                Each of the two subdirectories must hold a complete .imzML + .ibd pair; an empty one raises FileNotFoundError.
                Prefer letting _create_msi_samples infer this from the files on disk rather than asserting it here.
        ion_mode : MsiIonMode | None
                If double_ion_mode is False, specify the ion mode of the sample. If double_ion_mode is True, this should be None.
        """

        # Ensure consistency of input parameters
        if not double_ion_mode and ion_mode not in MsiIonMode.list():
            raise ValueError(
                f"Invalid ion_mode value. Expected one of {MsiIonMode.list()} when double_ion_mode is False."
            )

        super().__init__(source_path, sample_id, modality_name)

        # Which ion modes are present is decided by the imzML/IBD pairs on disk, not by directory
        # presence: the GUI scaffolds both pos/ and neg/ for every MSI modality, so an empty
        # ion mode directory only means that polarity was not acquired.
        self.input_paths = mode_paths(
            source_path, sample_id, modality_name, double_ion_mode, ion_mode
        )

        self.double_ion_mode = double_ion_mode
        self.ion_mode = ion_mode

        self.recalibration_reference = None  # To be set during dataset preprocessing
        self.recalibration_model: dict[
            str, MsiRecalibration
        ] = {}  # Estimated during dataset preprocessing
        self.filtered_idx: list[int] | None = (
            None  # To be computed during dataset preprocessing
        )
        self._sample_type: str = MsiSampleType.TISSUE  # To be set from process_dataset

        # Initialize the other variables
        self._metadata_files: dict[
            str, str
        ] = {}  # For each ion mode, store the absolute path to the imzML file
        self._binary_files: dict[
            str, str
        ] = {}  # For each ion mode, store the absolute path to the IBD file
        self._metadata: dict[
            str, SampleMetadata
        ] = {}  # For each ion mode, store the metadata extracted from the imzML file
        self._aligned_mz: dict[
            str, np.ndarray
        ] = {}  # For each ion mode, store the aligned M/Z values (obtained from preprocessing)

    @property
    def ion_modes(self) -> list[str]:
        """
        Get the list of ion modes available in this sample.

        Returns
        -------
        list[MsiIonMode]
                List of ion modes available in this sample.
        """
        return list(self.input_paths.keys())

    @property
    def recalibration_reference(self) -> dict[str, np.ndarray] | None:
        """
        Get the recalibration reference M/Z vector for this sample.

        Returns
        -------
        dict[MsiIonMode, np.ndarray] | None
                Dictionary containing the recalibration reference M/Z vector for each ion mode.
                If no recalibration reference is set, returns None.
        """
        return self._recalibration_reference

    @recalibration_reference.setter
    def recalibration_reference(self, value: dict[str, np.ndarray] | None) -> None:
        """
        Set the recalibration reference M/Z vector for this sample.

        Parameters
        ----------
        value : dict[MsiIonMode, np.ndarray] | None
                Dictionary containing the recalibration reference M/Z vector for each ion mode.
                If no recalibration reference is to be set, provide None.
        """
        if value is not None:
            if not isinstance(value, dict):
                raise TypeError(
                    "Recalibration reference must be a dictionary mapping ion modes to M/Z vectors."
                )
            for mode in value.keys():
                if not isinstance(value[mode], np.ndarray):
                    raise TypeError(
                        f"Recalibration reference for ion mode {mode} must be a numpy array."
                    )

        self._recalibration_reference = value

    @property
    def foreground_mask(self) -> np.ndarray | None:
        """
        Get the foreground mask for this sample.

        Returns
        -------
        np.ndarray | None
                Boolean array indicating which spots are foreground (True) and which are background (False).
        """
        foreground_mask = np.zeros(
            self._metadata[self.ion_modes[0]][MsiMetadata.PIXEL_COORDINATES].shape[0],
            dtype=bool,
        )
        if self.filtered_idx is not None:
            foreground_mask[self.filtered_idx] = True

        # If no foreground spots are defined, invert the mask to consider every spot as foreground
        if not np.any(foreground_mask):
            foreground_mask = ~foreground_mask

        return foreground_mask

    def initialize_sample(self) -> None:
        """Load metadata and reconcile spot coordinates before payload processing."""
        initialize_sample(self)

    def load_payload(
        self,
        mass_tolerance: int | None = None,
        annotation_db: pd.DataFrame | None = None,
        detect_background: bool = False,
    ) -> tuple[
        dict[str, list[np.ndarray]],
        dict[str, list[np.ndarray]],
        dict[str, list[np.ndarray]],
        dict[str, list[np.ndarray]],
    ]:
        """
        Load the M/Z vectors and the corresponding intensities from the binary IBD files.
        If the sample contains both ion modes, load the data for both modes.
        Before loading, each datapoint is evaluated against the lipid annotation database to filter spots
        that do not contain at least min_annotated_lipids_per_spot annotated lipids. If the database or the minimum number is not provided, the filter is skipped.

        If the recalibration reference and mass_tolerance are set, the M/Z values are recalibrated with
        one per-spectrum model per ion mode (see estimate_recalibration), estimated on first use from all
        spectra and applied to both the raw and the filtered vectors. Otherwise, the raw M/Z values are
        loaded.

        Parameters
        ----------
        annotation_db: pd.DataFrame
                Database containing the annotated ionized_masses and the ion_mode
        mass_tolerance : int
                Mass tollerance in ppm to match the M/Z values against the lipid annotation database.
                Must be provided if annotation_db is provided.
        detect_background : bool
                If True, detect background spots and exclude them from the loaded data.

        Returns
        ----------

        mz_vectors:	dict[list[list[np.float32]]]
                A dictionary with the M/Z vectors for each ion mode.
                Each ion mode contains a list of M/Z vectors, one for each spectrum. Each M/Z vector is a list of variable length.
        intensity_vectors:	dict[list[list[np.float32]]]
                A dictionary with the intensity vectors for each ion mode.
        """

        if annotation_db is not None and not isinstance(annotation_db, pd.DataFrame):
            raise ValueError("If provided, annotation_db must be a pd.DataFrame")
        if annotation_db is not None and mass_tolerance is None:
            raise ValueError(
                "Mass tolerance must be provided if annotation_db is provided"
            )
        mz_vectors, intensity_vectors = read_vectors(self)
        select_foreground(
            self,
            mz_vectors,
            intensity_vectors,
            annotation_db,
            mass_tolerance,
            detect_background,
        )
        recalibrate_vectors(self, mz_vectors, intensity_vectors, mass_tolerance)
        filtered_mz, filtered_intensity = filter_vectors(
            self, mz_vectors, intensity_vectors
        )
        return mz_vectors, intensity_vectors, filtered_mz, filtered_intensity

    @cached_property
    def reader(self) -> ImzmlReader:
        """Return the ImzmlReader component bound to this sample."""
        return ImzmlReader(self)

    @cached_property
    def binary(self) -> BinaryValidator:
        """Return the BinaryValidator component bound to this sample."""
        return BinaryValidator(self)

    @cached_property
    def coordinates(self) -> MsiCoordinates:
        """Return the MsiCoordinates component bound to this sample."""
        return MsiCoordinates(self)

    @cached_property
    def foreground(self) -> MsiForeground:
        """Return the MsiForeground component bound to this sample."""
        return MsiForeground(self)
