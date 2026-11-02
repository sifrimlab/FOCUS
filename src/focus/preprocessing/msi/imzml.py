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


class ImzmlReader:
    """Decode one sample's acquisition metadata and IBD spectrum range declarations."""

    def __init__(self, sample: MsiSample) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

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

            if cast(
                ET.Element, element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF)
            ).attrib["ref"] in ["mzArray"]:
                mzs = {
                    "length": length,
                    "encoded_length": encoded_length,
                    "offset": offset,
                }
            elif cast(
                ET.Element, element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF)
            ).attrib["ref"] in ["intensities", "intensityArray"]:
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

        # First determine the data types
        mz_dtype, intensities_dtype = None, None

        def str_to_dtype(name: str) -> type[np.float32] | type[np.float64]:
            """Resolve the original imzML float storage type."""
            return np.float32 if name == "32-bit float" else np.float64

        # Extract the data types for mz and intensities
        for rpg in cast(
            ET.Element, root.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_LIST)
        ):
            if rpg.attrib["id"] in ["mzArray"]:
                for cv_param in rpg:
                    if "float" in cv_param.attrib["name"]:
                        mz_dtype = str_to_dtype(cv_param.attrib["name"])
            elif rpg.attrib["id"] in ["intensities", "intensityArray"]:
                for cv_param in rpg:
                    if "float" in cv_param.attrib["name"]:
                        intensities_dtype = str_to_dtype(cv_param.attrib["name"])

        if mz_dtype is None or intensities_dtype is None:
            raise KeyError(
                "Could not find the data types for mz and intensities in the imzML file. Check the metadata name"
            )

        # Extract the raster size in micrometers
        raster_size = np.array([0, 0], dtype=np.int16)
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
                    raster_size[0] = int(scan_settings.attrib["value"])
                elif name == "pixel size y":
                    raster_size[1] = int(scan_settings.attrib["value"])
                elif name == "vertical line scan":
                    line_axis = 1
                elif name == "horizontal line scan":
                    line_axis = 0

        # If raster size is absent from the file, assume unit spacing so that pixel
        # coordinates can be used directly as physical coordinates without overlap.
        if raster_size[0] == 0 or raster_size[1] == 0:
            raster_size = np.array([1, 1], dtype=np.int16)

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

        # Where physical coordinates were absent, the fallback stored raw pixel indices.
        # Scale those entries by raster_size so the inter-spot spacing is in micrometers,
        # consistent with the downstream normalization and raster_size/2 centering shift.
        has_physical = np.array([m["has_physical_coordinates"] for m in parsed_spectra])
        if not has_physical.any():
            physical_coordinates = pixel_coordinates.astype(np.float32) * raster_size
        elif not has_physical.all():
            physical_coordinates[~has_physical] = (
                pixel_coordinates[~has_physical].astype(np.float32) * raster_size
            )

        # Align MSI laser grid to the axes using linear regression
        physical_coordinates = self.sample.coordinates._correct_rotation_error(
            physical_coordinates, pixel_coordinates
        )

        # Define the metadata dictionary
        experiment_metadata: SampleMetadata = {
            MsiMetadata.INTENSITIES_DTYPE: intensities_dtype,
            MsiMetadata.MZ_DTYPE: mz_dtype,
            MsiMetadata.RASTER_SIZE: raster_size,
            MsiMetadata.LINE_AXIS: line_axis,
            MsiMetadata.STAGE_COORDINATES: bool(has_physical.all()),
            MsiMetadata.PIXEL_COORDINATES: pixel_coordinates,
            MsiMetadata.PHYSICAL_COORDINATES: physical_coordinates,
            MsiMetadata.MZ_BINARY_METADATA: mz_binary_metadata,
            MsiMetadata.INTENSITIES_BINARY_METADATA: intensities_binary_metadata,
        }

        return experiment_metadata
