"""Annotations checks in the established configuration-validation sequence."""

from focus.config.types import PipelineConfig
from focus.config.checks import _require_key

import os


from focus.config.keys import ConfigParameters, AnnotationsParameters
from focus.config.options import AnnotationFileType


def validate_annotations(
    config: PipelineConfig,
    dataset_path: str,
    sample_ids: list[str],
    names: list[str],
    require_raw_inputs: bool,
) -> None:
    """Validate spatial annotation settings and their optional raw inputs."""

    # --- Step 11: spatial_annotations ---
    config.setdefault(ConfigParameters.SPATIAL_ANNOTATIONS, None)
    ann_cfg = config[ConfigParameters.SPATIAL_ANNOTATIONS]
    if ann_cfg is not None:
        if not isinstance(ann_cfg, dict):
            raise TypeError("'spatial_annotations' must be a dict or null.")
        _require_key(
            ann_cfg, AnnotationsParameters.MODALITY_NAME, str, "spatial_annotations"
        )
        _require_key(
            ann_cfg, AnnotationsParameters.FILE_TYPE, str, "spatial_annotations"
        )

        ann_mod_name = ann_cfg[AnnotationsParameters.MODALITY_NAME]
        if ann_mod_name not in names:
            raise ValueError(
                f"Annotation modality '{ann_mod_name}' not found in declared modalities: {names}"
            )

        ann_file_type = ann_cfg[AnnotationsParameters.FILE_TYPE]
        if ann_file_type not in AnnotationFileType.list():
            raise ValueError(
                f"Unsupported annotation file type '{ann_file_type}'. "
                f"Supported types: {AnnotationFileType.list()}"
            )

        # The per-sample GeoJSON files are an annotation-transfer input; skip the
        # existence check for an alignment-only run (require_raw_inputs False).
        if require_raw_inputs and ann_file_type == AnnotationFileType.GEOJSON:
            validate_geojson_files(dataset_path, sample_ids, ann_mod_name)

        ref_name = config[ConfigParameters.REFERENCE_MODALITY]
        if ann_mod_name != ref_name and not config[ConfigParameters.PERFORM_ALIGNMENT]:
            raise ValueError(
                f"'spatial_annotations.modality_name' is '{ann_mod_name}' (a non-reference modality). "
                "Transferring annotations from a non-reference modality requires "
                "'perform_alignment' to be true."
            )


def validate_geojson_files(
    dataset_path: str, sample_ids: list[str], ann_mod_name: str
) -> None:
    """Require one GeoJSON per sample, preserving filesystem order and original errors."""
    for sample_id in sample_ids:
        mod_dir = os.path.join(dataset_path, sample_id, ann_mod_name)
        geojson_files = [f for f in os.listdir(mod_dir) if f.endswith(".geojson")]
        if len(geojson_files) == 0:
            raise FileNotFoundError(
                f"No .geojson annotation file found for sample '{sample_id}' "
                f"in '{mod_dir}'."
            )
        if len(geojson_files) > 1:
            raise ValueError(
                f"Multiple .geojson files found for sample '{sample_id}' "
                f"in '{mod_dir}': {geojson_files}. Expected exactly one."
            )
