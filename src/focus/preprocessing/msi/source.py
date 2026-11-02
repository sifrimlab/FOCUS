"""Resolve complete MSI input pairs for the ion modes declared by a sample."""

import os
from typing import cast
from focus.config.options import MsiIonMode
from focus.io.discovery import find_imzml_pair


def mode_paths(
    source_path: str,
    sample_id: str,
    modality_name: str,
    double_ion_mode: bool,
    ion_mode: str | None,
) -> dict[str, str]:
    """Return ordered polarity directories; raise the original error for any incomplete input pair."""
    paths = {}
    for mode in MsiIonMode.list() if double_ion_mode else [cast(str, ion_mode)]:
        mode_path = os.path.join(source_path, sample_id, modality_name, mode)
        if find_imzml_pair(mode_path) is None:
            raise FileNotFoundError(
                f"No complete .imzML + .ibd file pair found in '{mode_path}' for ion mode "
                f"'{mode}' of sample '{sample_id}'."
            )
        paths[mode] = mode_path

    return paths
