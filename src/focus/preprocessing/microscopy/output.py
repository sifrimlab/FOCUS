"""Output stage: FOCUS annotations, supersession of the replaced file and the OME-TIFF write."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import numpy as np
import zarr

from focus.config.options import MicroscopyImageType
from focus.io.atomic import atomic_path
from focus.io.provenance import (
    CROP_OFFSET_KEY,
    INPUT_SIGNATURE_KEY,
    PIXEL_DIGEST_KEY,
    PREPROCESSING_FINGERPRINT_KEY,
    SUPERSEDED_SIGNATURE_KEY,
)
from focus.preprocessing.microscopy import pyramid, tissue
from focus.preprocessing.microscopy.metadata import METHOD_VERSION
from focus.preprocessing.microscopy.ome_xml import OmeImage, build_ome_xml
from focus.preprocessing.microscopy.source import SourceImage
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.microscopy.workflow import Analysis, Provenance


def focus_annotations(
    analysis: Analysis,
    provenance: Provenance,
    offset: tuple[int, int],
    fill: str,
    source: SourceImage,
    channels_display: dict[str, object],
) -> dict[str, object]:
    """FOCUS MapAnnotation values (the pixel digest is added once level 0 is staged)."""
    return {
        PREPROCESSING_FINGERPRINT_KEY: provenance.key,
        INPUT_SIGNATURE_KEY: provenance.signature,
        CROP_OFFSET_KEY: f"{offset[0]},{offset[1]}",
        "focus_method": METHOD_VERSION,
        "focus_pixel_size_source": analysis.size_source,
        "focus_image_type": analysis.profile.image_type,
        "focus_image_type_evidence": analysis.evidence,
        "focus_z_projection": analysis.z_note or "single plane",
        "focus_source_series": str(source.series),
        "focus_background_fill": fill,
        "focus_display": json.dumps(channels_display, separators=(",", ":")),
    }


def supersession(
    digest: str,
    provenance: Provenance,
    values: dict[str, object],
    reporter: StepReporter,
) -> None:
    """Record the replaced file when the new level-0 pixels equal it; warn when they differ."""
    values[PIXEL_DIGEST_KEY] = digest
    if provenance.previous is None:
        return
    previous_digest, superseded = provenance.previous
    if previous_digest == digest:
        if superseded:
            values[SUPERSEDED_SIGNATURE_KEY] = json.dumps(superseded)
        reporter.detail("Pixels identical to the previous output")
    else:
        reporter.warning(
            "The regenerated image differs from the previous one: alignments made on it must be redone"
        )


def ome_image(
    source: SourceImage,
    analysis: Analysis,
    levels: list[zarr.Array[Any]],
    values: dict[str, object],
    offset: tuple[int, int],
) -> OmeImage:
    """The OME description of the staged level 0 and the source's acquisition metadata."""
    h, w = (
        (levels[0].shape[0], levels[0].shape[1]) if source.rgb else levels[0].shape[1:]
    )
    shown = json.loads(str(values["focus_display"]))
    return OmeImage(
        name=os.path.basename(source.path),
        shape_yx=(int(h), int(w)),
        dtype=source.dtype,
        rgb=source.rgb,
        channels=source.channels,
        colours=[ch["color"] for ch in shown["channels"]],
        pixel_size=analysis.pixel_size,
        significant_bits=source.significant_bits,
        objective=(source.objective_magnification, source.objective_na),
        contours=tissue.contours_level0(analysis.mask, analysis.factor, offset),
    )


def write_staged(
    source: SourceImage,
    analysis: Analysis,
    levels: list[zarr.Array[Any]],
    values: dict[str, object],
    provenance: Provenance,
    offset: tuple[int, int],
    output: str,
    reporter: StepReporter,
) -> None:
    """Digest level 0, build the OME-XML and write the TIFF atomically."""
    supersession(
        pyramid.level0_digest(levels[0], source.rgb), provenance, values, reporter
    )
    xml = build_ome_xml(ome_image(source, analysis, levels, values, offset), values)
    codec = analysis.profile.codec(source.dtype)
    reporter.detail(
        f"{len(levels)} pyramid levels, {codec[0]} lossless, {np.dtype(source.dtype).name}"
    )
    with atomic_path(output) as tmp:
        pyramid.write_pyramid(levels, tmp, source.rgb, xml, codec, analysis.pixel_size)
    if analysis.profile.image_type == MicroscopyImageType.FLUORESCENCE:
        reporter.detail(
            "Fluorescence image: feature_extraction registration does not apply"
        )
