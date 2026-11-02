"""Raman tool discovery and subprocess argument contracts after package moves."""

from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location
import shlex
import subprocess
from typing import cast
from unittest.mock import patch

import numpy as np
import tifffile

from focus.preprocessing.raman.sample import RamanImage
from focus.preprocessing.raman.tools import _TOOLS_DIR
from focus.preprocessing.raman import tools as raman_tools


def test_tool_paths_point_to_project_tools() -> None:
    """Resolve the auxiliary entrypoints from the relocated modality package."""
    expected = Path(__file__).resolve().parents[2] / "tools"
    assert Path(_TOOLS_DIR) == expected
    for tool in ("ASHLAR", "BaSiCpy"):
        assert (expected / tool / "main.py").is_file()


def test_tool_paths_preserve_source_symlink(tmp_path: Path) -> None:
    """Retain the original abspath contract when the source checkout is reached by symlink."""
    project = tmp_path / "project"
    relative = Path("src/focus/preprocessing/raman/tools.py")
    target = project / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(Path(raman_tools.__file__).read_bytes())
    linked = tmp_path / "linked-project"
    linked.symlink_to(project, target_is_directory=True)
    spec = spec_from_file_location("controlled_raman_tools", linked / relative)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._TOOLS_DIR == str(linked / "tools")


def test_stitch_command_and_cache(tmp_path: Path) -> None:
    """Retain the conda command, environment cleanup, output rename, and cache reuse."""
    sample = RamanImage(str(tmp_path), "sample", "raman")
    sample._raman_corrected_tiles = np.ones((1, 1, 16, 16), dtype=np.float32)
    sample._tiles_coordinates = np.zeros((1, 1, 2), dtype=np.float32)
    mosaic = np.arange(256, dtype=np.float32).reshape(16, 16)
    default_output = Path(sample.output_path) / "ashlar_output.ome.tiff"
    tifffile.imwrite(default_output, mosaic)
    with (
        patch.object(sample.stitcher, "_prepare_for_ashlar", return_value=3),
        patch(
            "focus.preprocessing.raman.tool_execution.subprocess.run",
            return_value=subprocess.CompletedProcess("command", 0),
        ) as run,
        patch.dict("os.environ", {"MPLBACKEND": "controlled"}),
    ):
        output = sample.stitcher.ashlar_stitch()
        arguments, keywords = run.call_args
        assert shlex.split(arguments[0]) == [
            "conda",
            "run",
            "-n",
            "FOCUS_ASHLAR",
            "python",
            "-u",
            str(Path(_TOOLS_DIR) / "ASHLAR" / "main.py"),
            sample.output_path,
            "3",
        ]
        assert {key: keywords[key] for key in ("shell", "check", "executable")} == {
            "shell": True,
            "check": True,
            "executable": "/bin/bash",
        }
        assert "MPLBACKEND" not in keywords["env"]
        assert sample.stitcher.ashlar_stitch() == output
        assert run.call_count == 1
    assert not default_output.exists()
    np.testing.assert_array_equal(cast(np.ndarray, sample.mosaic), mosaic, strict=True)
