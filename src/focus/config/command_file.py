"""Read and validate command-line configuration files with their original error exits."""

import json
import logging
import os
import sys

from focus.config.types import PipelineConfig
from focus.config.validation import parse_config


def load_command_config(
    path: str, logger: logging.Logger, *, require_raw_inputs: bool = True
) -> PipelineConfig:
    """Load and validate JSON using the selected raw-input policy.

    Parameters
    ----------
    path
        Configuration JSON path supplied on the command line.
    logger
        Console logger configured before file loading starts.

    require_raw_inputs
        Keep full input validation for the pipeline; standalone stage commands pass False.

    Returns
    -------
    PipelineConfig
        The existing validated configuration, including its applied defaults.

    Raises
    ------
    SystemExit
        Exit status 1 after logging a missing file, invalid JSON, or validation error.
    """
    if not os.path.exists(path):
        logger.error(f"Config file not found: {path}")
        sys.exit(1)
    try:
        with open(path, "r") as source:
            config = json.load(source)
    except json.JSONDecodeError as error:
        logger.error(f"Invalid JSON in config file '{path}': {error}")
        sys.exit(1)
    try:
        return parse_config(config, require_raw_inputs=require_raw_inputs)
    except (
        TypeError,
        KeyError,
        ValueError,
        FileNotFoundError,
        PermissionError,
    ) as error:
        logger.error(f"Config validation failed: {error}")
        sys.exit(1)
