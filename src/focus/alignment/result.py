"""Decode browser alignment confirmations in the full coordinate frame."""

from __future__ import annotations
import numpy as np
from focus.alignment.types import AlignmentResult, AlignmentPayload


def parse_alignment_result(
    alignment_result: AlignmentResult,
    target_payload: AlignmentPayload,
    full_coordinates: np.ndarray | None = None,
) -> np.ndarray | None:
    """Extract aligned coordinates from the GUI result.

    Parameters
    ----------
    alignment_result : dict
            Payload POSTed by the frontend on /confirm.
    target_payload : bytes | list[dict] | Image.Image
            The display payload that was sent to the browser (FSP1 bytes for spot modalities).
    full_coordinates : np.ndarray | None
            Full (N, 2) float32 coordinate array for SPOT modalities.  When present and the
            frontend supplies a ``transform_matrix`` key, the 3×3 matrix is applied directly to
            every spot, so the result never depends on which spots the browser drew.
    """
    # Preferred path: frontend sends the 3×3 matrix, applied to every original spot.
    if "transform_matrix" in alignment_result and full_coordinates is not None:
        # The frontend serialises a gl-matrix mat3, which is COLUMN-MAJOR, so it must be
        # reshaped column-first (order="F"). A plain row-major reshape(3, 3) would build the
        # TRANSPOSE of the true matrix — transposing the linear part and, critically, pushing
        # the translation into the homogeneous row where it is silently dropped. That is
        # harmless only when the translation is ~0 (no flip, origin-anchored data); a flip
        # needs a large repositioning translation, so dropping it sends every spot into the
        # negative quadrant (off-image -> empty registered features).
        mat = np.array(alignment_result["transform_matrix"], dtype=np.float64).reshape(
            3, 3, order="F"
        )
        n = len(full_coordinates)
        hom = np.column_stack([full_coordinates.astype(np.float64), np.ones(n)])
        out = (mat @ hom.T).T  # (n, 3) homogeneous
        w = out[:, 2:3]
        w[w == 0] = 1.0  # guard; affine transforms keep w == 1
        transformed = (
            out[:, :2] / w
        )  # divide-through handles projective ('distort') too
        return transformed.astype(np.float32)

    if "spots" in alignment_result:
        # Fallback only: SPOT→SPOT / IMAGE→SPOT always send a transform_matrix (handled above).
        spots = alignment_result["spots"]
        # Use the full coordinate count when available so the output array is always
        # sized to match the AnnData obs dimension, not the (possibly smaller) display subset.
        if full_coordinates is not None:
            num_spots = len(full_coordinates)
        elif isinstance(target_payload, list):
            num_spots = len(target_payload)
        else:
            num_spots = (
                max((s.get("id", 0) for s in spots), default=-1) + 1 if spots else 0
            )
        if num_spots <= 0:
            return None

        coords = np.full((num_spots, 2), np.nan, dtype=np.float32)
        for spot in spots:
            idx = spot.get("id")
            px, py = spot.get("pixel_x"), spot.get("pixel_y")
            if (
                idx is not None
                and 0 <= idx < num_spots
                and px is not None
                and py is not None
            ):
                coords[idx] = [px, py]
        return coords

    elif "corner_pixels" in alignment_result:
        return np.array(alignment_result["corner_pixels"], dtype=np.float32)

    return None
