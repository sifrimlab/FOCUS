"""Decode imzML spectrum ranges, acquisition coordinates and storage dtypes."""

from __future__ import annotations
from focus.preprocessing.msi.types import SpectrumRecord, SampleMetadata, BinaryRange
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
import os
import xml.etree.ElementTree as ET

from focus.config.keys import ImzMLFileParser, MsiMetadata
from focus.runtime.reporter_context import get_reporter


class ImzmlReader:
    """Decode one sample's acquisition metadata and IBD spectrum range declarations."""

    def __init__(self, sample: MsiSample) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    # Parameter-group ids used by exporters that omit the array cvParams from the group.
    _MZ_GROUP_IDS = ("mzArray", "mz_array", "mzarray")
    _INTENSITY_GROUP_IDS = ("intensities", "intensityArray", "intensity_array")

    # imzML binary data types, as read from the IBD; integers are cast to float32 at read.
    _DTYPES: dict[str, type[np.generic]] = {
        "32-bit float": np.float32,
        "64-bit float": np.float64,
        "32-bit integer": np.int32,
        "64-bit integer": np.int64,
    }

    _group_kinds: dict[str, str]

    def _array_kind(self, element: ET.Element) -> str | None:
        """'mz', 'intensity' or None for one binaryDataArray, from its own or its group's cvParams."""
        names = {
            cv_param.attrib.get("name", "")
            for cv_param in element.iter(ImzMLFileParser.CV_PARAM)
        }
        if "m/z array" in names:
            return "mz"
        if "intensity array" in names:
            return "intensity"
        reference = element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF)
        if reference is None:
            return None
        return getattr(self, "_group_kinds", {}).get(reference.attrib.get("ref", ""))

    def _spectra_to_dict(self, spectra: ET.Element) -> SpectrumRecord:
        """
        Convert the spectra element to a dictionary.

        Parameters
        ----------
        spectra : ET.Element
                The spectra element from the imzML file.

        Returns
        -------
        dict
                A dictionary with the spectrum data.
        """

        # Check input parameter type
        if not isinstance(spectra, ET.Element):
            raise TypeError("Invalid input type. Expected ET.Element.")

        x, y = None, None
        mzs: BinaryRange | None = None
        intensities: BinaryRange | None = None
        physical_x, physical_y = None, None

        scan_list = cast(ET.Element, spectra.find(ImzMLFileParser.SCAN_LIST))
        scan = cast(ET.Element, scan_list.find(ImzMLFileParser.SCAN))

        for cv_param in scan.iter(ImzMLFileParser.CV_PARAM):
            if cv_param.attrib["name"] == "position x":
                x = int(cv_param.attrib["value"])
            elif cv_param.attrib["name"] == "position y":
                y = int(cv_param.attrib["value"])
        for user_param in scan.iter(ImzMLFileParser.USER_PARAM):
            if user_param.attrib["name"] == "3DPositionX":
                physical_x = float(user_param.attrib["value"])
            if user_param.attrib["name"] == "3DPositionY":
                physical_y = float(user_param.attrib["value"])

        # Record whether true physical coordinates were present before applying fallback
        has_physical_coordinates = physical_x is not None and physical_y is not None
        if physical_x is None:
            physical_x = float(cast(int, x))
        if physical_y is None:
            physical_y = float(cast(int, y))

        bdal = cast(ET.Element, spectra.find(ImzMLFileParser.BINARY_DATA_ARRAY_LIST))

        for element in bdal.findall(ImzMLFileParser.BINARY_DATA_ARRAY):
            for cv_param in element.iter(ImzMLFileParser.CV_PARAM):
                if cv_param.attrib["name"] == "external array length":
                    length = int(cv_param.attrib["value"])
                if cv_param.attrib["name"] == "external encoded length":
                    encoded_length = int(cv_param.attrib["value"])
                if cv_param.attrib["name"] == "external offset":
                    offset = int(cv_param.attrib["value"])

            kind = self._array_kind(element)
            if kind == "mz":
                mzs = {
                    "length": length,
                    "encoded_length": encoded_length,
                    "offset": offset,
                }
            elif kind == "intensity":
                intensities = {
                    "length": length,
                    "encoded_length": encoded_length,
                    "offset": offset,
                }

        return {
            "pixel_x": x,
            "pixel_y": y,
            "mzs": mzs,
            "intensities": intensities,
            "physical_x": physical_x,
            "physical_y": physical_y,
            "has_physical_coordinates": has_physical_coordinates,
        }

    @staticmethod
    def _check_encoding(root: ET.Element, filename: str) -> None:
        """
        Reject binary encodings the IBD reader does not decode.

        The IBD arrays are read as uncompressed floats and every (m/z, intensity) pair is taken as
        a peak, so compressed arrays and profile spectra are refused instead of being decoded into
        wrong values. The cvParams are read from the file description, the referenceable parameter
        groups and the first spectrum, where exporters declare them.
        """
        scopes: list[ET.Element] = []
        for key in (
            ImzMLFileParser.FILE_DESCRIPTION,
            ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_LIST,
        ):
            element = root.find(key)
            if element is not None:
                scopes.append(element)
        run = root.find(ImzMLFileParser.RUN_KEY)
        spectrum_list = (
            run.find(ImzMLFileParser.SPECTRUM_LIST_KEY) if run is not None else None
        )
        first = (
            spectrum_list.find(ImzMLFileParser.SPECTRUM_KEY)
            if spectrum_list is not None
            else None
        )
        if first is not None:
            scopes.append(first)
        names = {
            cv_param.attrib.get("name", "")
            for scope in scopes
            for cv_param in scope.iter(ImzMLFileParser.CV_PARAM)
        }
        compression = sorted(
            name for name in names if "compression" in name and name != "no compression"
        )
        if compression:
            raise ValueError(
                f"{filename}: compressed binary arrays ({', '.join(compression)}) are not supported; "
                "export the imzML without compression"
            )
        if "profile spectrum" in names and "centroid spectrum" not in names:
            raise ValueError(
                f"{filename}: profile spectra are not supported; export centroided (peak-picked) spectra"
            )

    @staticmethod
    def _infer_pixel_size(
        filename: str,
        pixel_xy: np.ndarray,
        physical: np.ndarray,
        has_physical: np.ndarray,
    ) -> np.ndarray:
        """
        Pixel size (x, y) in µm from the stage positions, when the file declares none: the
        least-squares slope of each stage axis on the pixel index of the same axis.
        """
        if not has_physical.all():
            raise ValueError(
                f"{filename}: no pixel size ('pixel size x' / 'pixel size y') and no stage "
                "positions for every spectrum; the pixel size in µm cannot be determined"
            )
        size = np.zeros(2, dtype=np.float32)
        for axis in (0, 1):
            index = pixel_xy[:, axis].astype(np.float64)
            if np.ptp(index) == 0:
                continue
            size[axis] = abs(
                np.polyfit(index, physical[:, axis].astype(np.float64), 1)[0]
            )
        if not size.max() > 0:
            raise ValueError(
                f"{filename}: no pixel size declared and the stage positions do not span the raster"
            )
        size[size == 0] = size.max()
        get_reporter().warning(
            f"{filename}: no pixel size declared; {size[0]:.2f} x {size[1]:.2f} µm inferred from "
            "the stage positions"
        )
        return size

    def _physical_coordinates(
        self,
        filename: str,
        parsed_spectra: list[SpectrumRecord],
        pixel_coordinates: np.ndarray,
        physical_coordinates: np.ndarray,
        raster_size: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """Rotation-corrected physical coordinates, the raster size and the recorded stage positions.

        The stage positions are None unless every spectrum carries one.
        """
        # Where physical coordinates were absent, the fallback stored raw pixel indices.
        # Scale those entries by raster_size so the inter-spot spacing is in micrometers,
        # consistent with the downstream normalization and raster_size/2 centering shift.
        # PIXEL_COORDINATES is (y, x) and raster_size is (x, y): the pixel grid is reordered to
        # (x, y) before scaling, matching the (x, y) order of physical_coordinates.
        has_physical = np.array([m["has_physical_coordinates"] for m in parsed_spectra])
        pixel_xy = pixel_coordinates[:, ::-1].astype(np.float32)
        if not raster_size.min() > 0:
            raster_size = self._infer_pixel_size(
                filename, pixel_xy, physical_coordinates, has_physical
            )
        if not has_physical.any():
            physical_coordinates = pixel_xy * raster_size
        elif not has_physical.all():
            physical_coordinates[~has_physical] = pixel_xy[~has_physical] * raster_size

        # The recorded stage positions are kept until pairing: a sample type that pairs by stage
        # position when the content cannot needs them in the instrument's own frame.
        stage_positions = physical_coordinates.copy() if has_physical.all() else None

        # Align MSI laser grid to the axes using linear regression
        physical_coordinates = self.sample.coordinates._correct_rotation_error(
            physical_coordinates, pixel_coordinates
        )

        return physical_coordinates, raster_size, stage_positions

    def _parse_imzml(self, filename: str) -> SampleMetadata:
        """
        Parse the imzML files to extract the relevant metadata and the M/Z values.

        Returns
        -------
        tuple[dict, list[dict]]
                A tuple containing two dictionaries:
                - The first element is a dictionary that contains the metadata associated with the experiment
                - The second element is a list of dictionaries, each containing the parsed M/Z values for each ion mode
        """

        # Check that the file can be read
        if not os.path.exists(filename):
            raise FileNotFoundError(f"ImzML file {filename} does not exist.")
        if not os.access(filename, os.R_OK):
            raise PermissionError(f"ImzML file {filename} is not readable.")

        # Parse the imzML file
        tree = ET.parse(filename)
        root = tree.getroot()

        # First determine the array kind and data type of every referenceable parameter group: by the
        # "m/z array" and "intensity array" cvParams, then by the usual group ids.
        mz_dtype, intensities_dtype = None, None
        self._group_kinds = {}
        for rpg in cast(
            ET.Element, root.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_LIST)
        ):
            names = [cv_param.attrib.get("name", "") for cv_param in rpg]
            group_id = rpg.attrib.get("id", "")
            if "m/z array" in names or group_id in self._MZ_GROUP_IDS:
                kind = "mz"
            elif "intensity array" in names or group_id in self._INTENSITY_GROUP_IDS:
                kind = "intensity"
            else:
                continue
            self._group_kinds[group_id] = kind
            dtype = next((self._DTYPES[n] for n in names if n in self._DTYPES), None)
            if kind == "mz" and dtype is not None:
                mz_dtype = dtype
            elif kind == "intensity" and dtype is not None:
                intensities_dtype = dtype

        if mz_dtype is None or intensities_dtype is None:
            raise KeyError(
                "Could not find the data types for mz and intensities in the imzML file. Check the metadata name"
            )
        if not np.issubdtype(mz_dtype, np.floating):
            raise ValueError(f"{filename}: integer m/z arrays are not supported")

        self._check_encoding(root, filename)

        # Extract the raster size in micrometers (a decimal value such as "20.0" is accepted).
        raster_size = np.array([0.0, 0.0], dtype=np.float32)
        # Scan lines run along x for a horizontal line scan (the imzML default assumed here) and along
        # y for a vertical one. PIXEL_COORDINATES stores (y, x), so its column 0 indexes horizontal
        # lines and column 1 vertical ones.
        line_axis = 0
        for scan_settings_list in cast(
            ET.Element, root.find(ImzMLFileParser.SCAN_SETTINGS_LIST)
        ):
            for scan_settings in scan_settings_list:
                name = scan_settings.attrib.get("name")
                if name == "pixel size x":
                    raster_size[0] = float(scan_settings.attrib["value"])
                elif name == "pixel size y":
                    raster_size[1] = float(scan_settings.attrib["value"])
                elif name == "vertical line scan":
                    line_axis = 1
                elif name == "horizontal line scan":
                    line_axis = 0
        # One declared axis serves both (square pixels).
        if raster_size[0] > 0 and not raster_size[1] > 0:
            raster_size[1] = raster_size[0]
        elif raster_size[1] > 0 and not raster_size[0] > 0:
            raster_size[0] = raster_size[1]

        run = cast(ET.Element, root.find(ImzMLFileParser.RUN_KEY))
        spectrum_list = cast(ET.Element, run.find(ImzMLFileParser.SPECTRUM_LIST_KEY))
        spectra = spectrum_list.findall(ImzMLFileParser.SPECTRUM_KEY)

        # Decode the binary data from the imzML file
        parsed_spectra = [self._spectra_to_dict(spectrum) for spectrum in spectra]

        # Convert the parsed spectra to a set of Numpy arrays to facilitate further processing
        pixel_coordinates = np.array(
            [(metadata["pixel_y"], metadata["pixel_x"]) for metadata in parsed_spectra],
            dtype=np.int32,
        )  # NOTE: The X and Y axes are inverted between pixel and physical coordinates
        physical_coordinates = np.array(
            [
                (metadata["physical_x"], metadata["physical_y"])
                for metadata in parsed_spectra
            ],
            dtype=np.float32,
        )
        mz_binary_metadata = np.array(
            [
                (
                    cast(BinaryRange, metadata["mzs"])["length"],
                    cast(BinaryRange, metadata["mzs"])["encoded_length"],
                    cast(BinaryRange, metadata["mzs"])["offset"],
                )
                for metadata in parsed_spectra
            ]
        )
        intensities_binary_metadata = np.array(
            [
                (
                    cast(BinaryRange, metadata["intensities"])["length"],
                    cast(BinaryRange, metadata["intensities"])["encoded_length"],
                    cast(BinaryRange, metadata["intensities"])["offset"],
                )
                for metadata in parsed_spectra
            ]
        )

        physical_coordinates, raster_size, stage_positions = self._physical_coordinates(
            filename,
            parsed_spectra,
            pixel_coordinates,
            physical_coordinates,
            raster_size,
        )

        # Define the metadata dictionary
        experiment_metadata: SampleMetadata = {
            MsiMetadata.INTENSITIES_DTYPE: intensities_dtype,
            MsiMetadata.MZ_DTYPE: cast(type[np.floating], mz_dtype),
            MsiMetadata.RASTER_SIZE: raster_size,
            MsiMetadata.LINE_AXIS: line_axis,
            MsiMetadata.STAGE_COORDINATES: stage_positions is not None,
            MsiMetadata.PIXEL_COORDINATES: pixel_coordinates,
            MsiMetadata.PHYSICAL_COORDINATES: physical_coordinates,
            MsiMetadata.MZ_BINARY_METADATA: mz_binary_metadata,
            MsiMetadata.INTENSITIES_BINARY_METADATA: intensities_binary_metadata,
        }
        if stage_positions is not None:
            experiment_metadata[MsiMetadata.STAGE_POSITIONS] = stage_positions

        return experiment_metadata
