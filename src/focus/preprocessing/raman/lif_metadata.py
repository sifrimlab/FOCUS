"""Decode Leica LIF XML fields without loading image planes."""

from __future__ import annotations
from typing import cast
import numpy as np
from readlif.reader import LifFile
from focus.preprocessing.raman.metadata import RamanMetadata
from focus.runtime.reporter_context import get_reporter


def parse_lif_metadata(lif: LifFile) -> dict[str, RamanMetadata]:
    """Parse LIF file XML metadata and extract scan parameters."""
    root = lif.xml_root

    top_level_elements = root.findall("./Element")
    if not top_level_elements:
        top_level_elements = root.findall(".")
        if root.tag != "Element":
            top_level_elements = root.findall(".//Element")

    elements_to_process = []
    for top_element in top_level_elements:
        children_tag = top_element.find("Children")
        if children_tag is not None:
            for image_element in children_tag.findall("Element"):
                elements_to_process.append(image_element)
        else:
            if top_element.find("./Data/Image") is not None:
                elements_to_process.append(top_element)

    # Fallback for collapsed XML structures
    if not elements_to_process and root.tag == "LMSDataContainerHeader":
        root_children_tag = root.find("Children")
        if root_children_tag is not None:
            for image_element in root_children_tag.findall("Element"):
                elements_to_process.append(image_element)
        elif root.find("./Element/Data/Image") is not None:
            for image_element in root.findall("Element"):
                if image_element.find("./Data/Image") is not None:
                    elements_to_process.append(image_element)

    if not elements_to_process:
        raise ValueError(
            "No elements found in the LIF file or unexpected XML structure."
        )

    result: dict[str, RamanMetadata] = {}

    for i, element in enumerate(elements_to_process):
        element_name = element.get("Name", f"Unnamed Element {i + 1}")
        data_image_tag = element.find("./Data/Image")

        metadata = RamanMetadata()
        metadata.name = element_name
        metadata.index = i
        metadata.pixel_size = np.array([0, 0], dtype=np.float32)
        dimension_scaling_factor: list[float | None] = [None, None]

        if data_image_tag is not None:
            image_description = data_image_tag.find("ImageDescription")
            if image_description is not None:
                dimensions = image_description.find("Dimensions")
                if dimensions is not None and len(list(dimensions)) > 0:
                    for dim_desc in dimensions.findall("DimensionDescription"):
                        dim_id = int(dim_desc.get("DimID", None))
                        size = int(dim_desc.get("NumberOfElements", None))
                        length = float(dim_desc.get("Length", None))
                        unit = dim_desc.get("Unit", None)

                        if dim_id == 1:
                            metadata.scan_height = size
                            if unit == "m":
                                dimension_scaling_factor[0] = 1e6
                            elif unit == "um":
                                dimension_scaling_factor[0] = 1e0
                            else:
                                raise ValueError(
                                    f"Unexpected unit '{unit}' for pixel size in image '{element_name}'."
                                )
                            cast(np.ndarray, metadata.pixel_size)[0] = (
                                length * cast(float, dimension_scaling_factor[0])
                            ) / size
                        elif dim_id == 2:
                            metadata.scan_width = size
                            if unit == "m":
                                dimension_scaling_factor[1] = 1e6
                            elif unit == "um":
                                dimension_scaling_factor[1] = 1e0
                            else:
                                raise ValueError(
                                    f"Unexpected unit '{unit}' for pixel size in image '{element_name}'."
                                )
                            cast(np.ndarray, metadata.pixel_size)[1] = (
                                length * cast(float, dimension_scaling_factor[1])
                            ) / size
                        elif dim_id == 9:
                            metadata.lambda_steps = size
                        elif dim_id == 10:
                            metadata.tile_number = size

        # Extract wavelength range and laser info
        atl_confocal_paths = [
            './Data/Image/Attachment[@Name="HardwareSetting"]/ATLConfocalSettingDefinition',
            './Data/Image/Attachment[@Name="HardwareSetting"]/LDM_Block_Sequential/LDM_Block_Sequential_Master/ATLConfocalSettingDefinition',
        ]

        found_lambda, found_stokes = False, False

        for _, atl_path in enumerate(atl_confocal_paths):
            atl_confocal_setting_def = element.find(atl_path)
            if atl_confocal_setting_def is not None:
                if not found_lambda:
                    lambda_definition = atl_confocal_setting_def.find(
                        "LambdaDefinition"
                    )
                    if (
                        lambda_definition is not None
                        and len(list(lambda_definition)) > 0
                    ):
                        lambda_excitation = lambda_definition.find("LambdaExcitation")
                        if lambda_excitation is not None:
                            metadata.lambda_begin = float(
                                lambda_excitation.get(
                                    "LambdaExcitationBeginDouble",
                                    lambda_excitation.get(
                                        "LambdaExcitationBegin", None
                                    ),
                                )
                            )
                            metadata.lambda_end = float(
                                lambda_excitation.get(
                                    "LambdaExcitationEndDouble",
                                    lambda_excitation.get("LambdaExcitationEnd", None),
                                )
                            )
                            found_lambda = True

                if not found_stokes:
                    laser_array = atl_confocal_setting_def.find("LaserArray")
                    if laser_array is not None:
                        for laser_tag in laser_array.findall("Laser"):
                            pump_wavelength = laser_tag.get("PumpWavelength")
                            if pump_wavelength is not None:
                                metadata.laser_type = laser_tag.get(
                                    "LaserName", "Unknown Laser"
                                )
                                metadata.lambda_stokes = float(pump_wavelength)
                                found_stokes = True
                                break

            if found_lambda and found_stokes:
                break

        # Extract tile coordinates
        if metadata.tile_number is not None and metadata.tile_number > 1:
            tiles = element.findall(
                './Data/Image/Attachment[@Name="TileScanInfo"]/Tile'
            )
            metadata.tiles_coordinates = np.zeros(
                (metadata.tile_number, 2), dtype=np.float32
            )

            if len(tiles) != metadata.tile_number:
                get_reporter().warning(
                    f"Element {element_name}: expected {metadata.tile_number} tiles, found {len(tiles)}; ignored"
                )
                continue

            for tile_index, tile in enumerate(tiles):
                cast(np.ndarray, metadata.tiles_coordinates)[tile_index] = (
                    float(tile.get("PosX")) * cast(float, dimension_scaling_factor[0]),
                    float(tile.get("PosY")) * cast(float, dimension_scaling_factor[1]),
                )

        result[cast(str, metadata.name)] = metadata

    return result
