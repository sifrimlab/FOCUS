"""Argument declarations for the standalone alignment command."""

import argparse


def build_alignment_parser() -> argparse.ArgumentParser:
    """Build the existing argument parser without loading pipeline dependencies."""
    parser = argparse.ArgumentParser(
        description="Run ONLY the FOCUS alignment stage from already-preprocessed files."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Absolute path of the JSON config file (same format as the full pipeline).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging (shows all log levels including HTTP request logs).",
    )
    return parser
