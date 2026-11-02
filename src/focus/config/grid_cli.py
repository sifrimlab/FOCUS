"""Command-line arguments for standalone microscopy grid extraction."""

import argparse


def build_grid_parser() -> argparse.ArgumentParser:
    """Build the existing parser without parsing arguments or changing defaults."""
    parser = argparse.ArgumentParser(
        description="Run standalone, anchorless microscopy feature extraction from preprocessed OME-TIFFs."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Absolute path of the JSON config file (same format as the full pipeline).",
    )
    parser.add_argument(
        "--resolution-levels",
        type=int,
        default=1,
        help=(
            "Number of resolution levels to extract from each image's existing OME-TIFF pyramid. "
            "1 = full resolution only (default); 2 = full res + half res; N = pyramid levels 0..N-1 "
            "(level i is the 0.5**i scale). Coarser levels are read from the file's stored pyramid "
            "(no real-time downsampling); their patch coordinates are reconciled to the full-res frame. "
            "If a sample's pyramid has fewer levels than requested, only the available levels are used."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging (shows all log levels including HTTP request logs).",
    )
    return parser
