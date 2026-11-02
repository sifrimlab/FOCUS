"""Argument declarations for the main FOCUS command."""

import argparse


def build_main_parser() -> argparse.ArgumentParser:
    """Build the existing argument parser without loading pipeline dependencies."""
    parser = argparse.ArgumentParser(
        description="FOCUS: End-to-end preprocessing, alignment, and registration pipeline"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=False,
        default=None,
        help="Absolute path of the JSON config file. If omitted, the GUI starts.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging (shows all log levels including HTTP request logs).",
    )
    return parser
