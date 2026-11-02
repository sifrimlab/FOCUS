"""Ordered configuration validation and default application."""

from typing import cast
from focus.config.keys import ConfigParameters
from focus.config.types import PipelineConfig
from focus.io.discovery import validate_path_readable, discover_sample_ids
from focus.config.structure import (
    require_fields,
    apply_defaults,
    validate_modalities,
    validate_names,
    validate_stage_dependencies,
)
from focus.config.resources import validate_samples, validate_raw_inputs, validate_token
from focus.config.compatibility import (
    validate_resolution,
    validate_registration,
    validate_alignment,
)
from focus.config.annotations import validate_annotations


def parse_config(config: object, require_raw_inputs: bool = True) -> PipelineConfig:
    """
    Validate the FOCUS configuration dictionary and apply defaults.

    Performs all structural, type, path, and compatibility checks upfront
    so that errors are caught before any computation begins.

    Parameters
    ----------
    config : dict
            Raw configuration loaded from JSON.
    require_raw_inputs : bool, default True
            Whether to verify that the on-disk resources consumed only by stages
            *other than* alignment are present. The full pipeline (``main.py``) leaves
            this ``True``. Set it to ``False`` for an alignment-only run that starts
            from already-preprocessed files, where none of these are needed: it skips
            the per-(sample, modality) raw input directory check and the MSI
            lipid-annotation database check (preprocessing inputs), the HuggingFace
            token requirement (a registration credential), and the GeoJSON
            annotation-file existence check (an annotation input). All structural,
            type, naming, reference, and alignment-strategy compatibility checks run
            regardless of this flag.

    Returns
    -------
    dict
            The validated configuration with defaults applied.

    Raises
    ------
    TypeError
            If config or any field has the wrong type.
    KeyError
            If a required key is missing.
    ValueError
            If a value is invalid or constraints are violated.
    """
    if not isinstance(config, dict):
        raise TypeError("Config must be a dictionary.")
    validated = cast(PipelineConfig, config)
    require_fields(validated)
    ignore_samples = apply_defaults(validated)
    dataset_path = validated[ConfigParameters.DATASET_PATH]
    validate_path_readable(dataset_path)
    validate_modalities(validated)
    names = validate_names(validated)
    validate_stage_dependencies(validated)
    sample_ids = discover_sample_ids(dataset_path, ignore_samples=ignore_samples)
    validate_samples(dataset_path, sample_ids)
    validate_raw_inputs(validated, dataset_path, sample_ids, require_raw_inputs)
    validate_resolution(validated)
    validate_registration(validated)
    validate_alignment(validated)
    validate_token(validated, require_raw_inputs)
    validate_annotations(validated, dataset_path, sample_ids, names, require_raw_inputs)
    return validated
