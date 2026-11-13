"""
Confirmed landmark warps: turning a browser confirmation into stored coordinates, and
persisting the warp so the alignment can be reopened, re-edited or composed later.

The warp of one pair lives in the moving modality's aligned file as a JSON string at
``uns['focus_alignment_warps'][fixed_name]`` (see :class:`focus.spatial.warp.LandmarkWarp`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, cast

import h5py
import numpy as np
from anndata.io import read_elem

from focus.alignment.result import parse_alignment_result
from focus.alignment.settings import logger
from focus.alignment.types import AlignmentPayload, AlignmentResult
from focus.runtime.reporting import StepReporter
from focus.spatial.warp import LandmarkWarp

WARPS_KEY = "focus_alignment_warps"
_FOLD_WARN_FRACTION = 0.0  # any folded spot is worth a warning


def _fixed_scale_xy(scale_yx: np.ndarray) -> np.ndarray:
    """``(sx, sy)`` from the aligner's ``[y_scale, x_scale]`` display -> full-res factors."""
    s = np.asarray(scale_yx, dtype=np.float64).reshape(2)
    return np.array([s[1], s[0]])


def confirmed_coordinates(
    result: AlignmentResult,
    moving_payload: AlignmentPayload,
    moving_coords: np.ndarray | None,
    fixed_scale_yx: np.ndarray,
    reporter: StepReporter | None = None,
) -> tuple[np.ndarray | None, LandmarkWarp | None]:
    """
    Full-resolution coordinates of every moving spot in the fixed frame, and the warp.

    A matrix-only confirmation keeps the original projective path unchanged. Landmarks or
    exclusion polygons go through the warp, which unplaces (NaN) excluded spots.

    Returns
    -------
    coordinates : np.ndarray | None
        ``(N, 2)`` float32, or the image corner pixels for an image moving layer.
    warp : LandmarkWarp | None
        The confirmed warp, None when the confirmation carried no matrix.
    """
    warp = None
    if "transform_matrix" in result and moving_coords is not None:
        warp = LandmarkWarp.from_browser(
            result["transform_matrix"],
            cast(Mapping[str, Any] | None, result.get("landmarks")),
            result.get("smoothing"),
            cast(Mapping[str, Any] | None, result.get("exclusions")),
            _fixed_scale_xy(fixed_scale_yx),
        )
        if warp.n_landmarks or warp.has_exclusions:
            mapped = warp.map_full(moving_coords).astype(np.float32)
            _report_damage(warp, moving_coords, mapped, reporter)
            return mapped, warp
    coords = parse_alignment_result(result, moving_payload, moving_coords)
    if coords is not None:
        # Display -> full resolution (in place, exactly as before landmarks existed); this
        # also scales the corner pixels of an image moving layer.
        coords[:, 0] *= fixed_scale_yx[1]
        coords[:, 1] *= fixed_scale_yx[0]
    return coords, warp


def _report_damage(
    warp: LandmarkWarp,
    moving: np.ndarray,
    mapped: np.ndarray,
    reporter: StepReporter | None,
) -> None:
    """Tell the operator how many spots were excluded and whether the warp folds tissue."""
    if reporter is None:
        return
    unplaced = int((~np.isfinite(mapped).all(axis=1)).sum())
    if warp.has_exclusions and unplaced:
        reporter.detail(
            f"{unplaced}/{len(mapped)} spots fall in excluded regions and are left unplaced"
        )
    if warp.is_nonrigid:
        folded = float(warp.folded(moving).mean()) if len(moving) else 0.0
        if folded > _FOLD_WARN_FRACTION:
            reporter.warning(
                f"The landmark warp folds the tissue under {folded:.1%} of the spots: "
                f"check for crossed or mismatched landmarks"
            )


def read_stored_warp_dict(path: str, key: str) -> dict[str, Any] | None:
    """The raw JSON object stored at ``uns['focus_alignment_warps'][key]``, or None."""
    if not os.path.exists(path):
        return None
    try:
        with h5py.File(path, "r") as f:
            name = f"uns/{WARPS_KEY}/{key}"
            if name not in f:
                return None
            raw = read_elem(f[name])
        data = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        logger.debug("Stored landmark warp could not be read", exc_info=True)
        return None


def read_stored_warp(path: str, key: str) -> LandmarkWarp | None:
    """The warp stored under ``key`` in an aligned file, or None (missing or unreadable)."""
    data = read_stored_warp_dict(path, key)
    if data is None:
        return None
    try:
        return LandmarkWarp.from_dict(data)
    except (ValueError, KeyError, TypeError):
        logger.debug("Stored landmark warp could not be parsed", exc_info=True)
        return None


def browser_state(
    warp: LandmarkWarp | None, fixed_scale_yx: np.ndarray
) -> dict[str, object] | None:
    """
    The stored warp as the browser's starting state, in the current fixed display frame.

    ``matrix`` is gl-matrix column-major and maps moving-native to fixed-layer display
    coordinates (the ``transform_matrix`` the browser confirmed).
    """
    if warp is None:
        return None
    current = warp.rescaled(_fixed_scale_xy(fixed_scale_yx))
    state = current.to_dict()
    state["matrix"] = current.matrix.reshape(-1, order="F").tolist()
    return state
