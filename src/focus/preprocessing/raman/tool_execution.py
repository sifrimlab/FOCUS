"""External BaSiCpy and ASHLAR commands, retaining environment and file-polling contracts."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from typing import TYPE_CHECKING, cast

import numpy as np
from focus.preprocessing.raman.tools import _TOOLS_DIR

if TYPE_CHECKING:
    from focus.preprocessing.raman.sample import RamanImage


def require_basic_tool() -> tuple[str, str]:
    """Validate conda, the BaSiCpy environment and script in their original order."""
    if shutil.which("conda") is None:
        raise RuntimeError("conda not found. Make sure conda is installed and in PATH.")

    env_name = "FOCUS_BaSiCpy"
    result = subprocess.run(
        ["conda", "env", "list", "--json"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list conda environments: {str(result.stderr)}")
    env_paths = json.loads(result.stdout).get("envs", [])
    if not any(env_name in path for path in env_paths):
        raise RuntimeError(f"Conda environment '{env_name}' does not exist.")

    main_script = os.path.join(_TOOLS_DIR, "BaSiCpy", "main.py")
    if not os.path.isfile(main_script):
        raise FileNotFoundError(f"BaSiCpy main script not found at {main_script}")

    return env_name, main_script


def correct_channel(
    sample: RamanImage,
    env_name: str,
    main_script: str,
    channel_idx: int,
) -> tuple[int, np.ndarray]:
    """Correct (tiles, H, W) raw channel data externally, reload it and remove its temp files."""
    input_file = os.path.join(sample.output_path, f"basic_input_{channel_idx}.npy")
    output_file = os.path.join(sample.output_path, f"basic_output_{channel_idx}.npy")
    np.save(
        input_file,
        cast(np.ndarray, sample._raw_tiles)[:, channel_idx, :, :],
    )

    env = os.environ.copy()
    env["JAX_PLATFORM_NAME"] = "cpu"

    subprocess.run(
        [
            "conda",
            "run",
            "-n",
            env_name,
            "python",
            main_script,
            sample.output_path,
            str(channel_idx),
        ],
        check=True,
        env=env,
    )

    wait_for_output(output_file)

    corrected = np.load(output_file)
    os.remove(input_file)
    os.remove(output_file)
    return channel_idx, corrected


def wait_for_output(output_file: str) -> None:
    """Poll every 0.2 seconds for a nonempty tool output; raise the original timeout after 10s."""
    # Wait for output file
    timeout, poll_interval = 10, 0.2
    start = time.time()
    while True:
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            break
        if time.time() - start > timeout:
            raise TimeoutError(
                f"Output file {output_file} not created after {timeout}s"
            )
        time.sleep(poll_interval)


def stitch_tiles(output_path: str, align_channel: int, env_name: str) -> None:
    """Run ASHLAR through the original quoted bash command, removing MPLBACKEND only."""
    main_script = os.path.join(_TOOLS_DIR, "ASHLAR", "main.py")
    if not os.path.isfile(main_script):
        raise FileNotFoundError(f"ASHLAR main script not found at {main_script}")

    cmd_parts = [
        "conda",
        "run",
        "-n",
        env_name,
        "python",
        "-u",
        main_script,
        output_path,
        str(align_channel),
    ]
    cmd = " ".join(shlex.quote(part) for part in cmd_parts)

    env = os.environ.copy()
    env.pop("MPLBACKEND", None)

    result = subprocess.run(
        cmd, shell=True, check=True, executable="/bin/bash", env=env
    )
    if result.returncode != 0:
        raise RuntimeError(f"ASHLAR stitching failed: {str(result.stderr)}")
