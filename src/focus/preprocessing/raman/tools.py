"""Resolve repository auxiliary-tool paths and retain the original Raman logger identity."""

import logging
import os
from pathlib import Path


logger = logging.getLogger("focus.preprocessing.raman")


# Tools remain outside src; resolve from this modality package to the project root.
_TOOLS_DIR = str(Path(os.path.abspath(__file__)).parents[4] / "tools")
