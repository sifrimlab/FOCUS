"""Ordered Raman (SRS) preprocessing of one LIF session: inventory, stitch, place, write."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import hashlib
import json
import os

import numpy as np
from readlif.reader import LifFile

from focus.io import ome_write
from focus.io.atomic import atomic_path
from focus.io.provenance import (
    INPUT_SIGNATURE_KEY,
    PIXEL_DIGEST_KEY,
    PREPROCESSING_FINGERPRINT_KEY,
    SUPERSEDED_SIGNATURE_KEY,
)
from focus.preprocessing.raman import flatfield, images, mosaic, output, placement
from focus.preprocessing.raman import settings as tech
from focus.preprocessing.raman.inventory import read_elements, select
from focus.preprocessing.raman.mosaic import Prepared
from focus.preprocessing.raman.parameters import RamanRunSettings
from focus.preprocessing.raman.reader import open_stack
from focus.runtime.reporting import StepReporter

STEPS = 4
# Footprints within this fraction of the largest are tied when choosing the reference image.
_AREA_TIE = 0.02
# CH stretching region (cm-1): lipid and protein contrast, preferred for the reference image.
_CH_REGION = (2800.0, 3100.0)
# Downscale factors of the channel-mean views kept per acquisition (placement and display).
_VIEW_UM = (tech.PLACEMENT_COARSE_UM, tech.PLACEMENT_FINE_UM)


@dataclass(frozen=True)
class Provenance:
    """Identity of the output: fingerprint, input signature and the output it replaces."""

    key: str
    signature: str
    previous: tuple[str, list[str | int] | None] | None


def _factors(pixel_um: float) -> tuple[int, ...]:
    return tuple(sorted({max(1, int(round(um / pixel_um))) for um in _VIEW_UM}))


def inventory(
    lif: LifFile, path: str, reporter: StepReporter
) -> tuple[list[Prepared], str]:
    """Step 1: select the acquisitions and open their tile stacks."""
    reporter.step(1, STEPS, "Reading the LIF session")
    elements = read_elements(path, lif.xml_root)
    acquisitions, decisions = select(elements)
    skipped = Counter(
        d.reason.split(" (")[0] if d.reason.startswith("incomplete") else d.reason
        for d in decisions
        if not d.kept and not d.reason.startswith("superseded")
    )
    reporter.detail(
        f"{os.path.basename(path)}: {len(elements)} images, {len(acquisitions)} SRS "
        f"acquisition{'s' if len(acquisitions) != 1 else ''}"
    )
    for acquisition in acquisitions:
        wn = acquisition.wavenumbers
        first = acquisition.first
        span = (
            f"{wn.min():.0f} cm-1"
            if len(wn) == 1
            else f"{wn.min():.0f}-{wn.max():.0f} cm-1"
        )
        line = (
            f"{acquisition.name}: {acquisition.tile_count} tiles, {first.channels} channel"
            f"{'s' if first.channels != 1 else ''} ({span}), gain {first.gain:g}"
            if first.gain is not None
            else f"{acquisition.name}: {acquisition.tile_count} tiles, {span}"
        )
        if acquisition.superseded:
            line += "; supersedes " + ", ".join(acquisition.superseded)
        reporter.detail(line)
    for reason, count in sorted(skipped.items()):
        reporter.detail(f"Skipped {count}: {reason}")
    if not acquisitions:
        raise ValueError(f"No complete SRS acquisition in {os.path.basename(path)}")
    prepared = [mosaic.read_tile_means(a, open_stack(a, lif)) for a in acquisitions]
    return prepared, output.inventory_annotation(decisions)


def stitch(prepared: list[Prepared], reporter: StepReporter) -> None:
    """Step 2: flat-field (own or borrowed) and tile registration of every acquisition."""
    reporter.step(2, STEPS, "Correcting illumination and stitching")
    own = {id(p): mosaic.own_flatfield(p) for p in prepared}
    donors = [p for p in prepared if own[id(p)] is not None]
    for p in prepared:
        flat = own[id(p)]
        if flat is None:
            if donors:
                donor = min(
                    donors, key=lambda d: abs(d.mean_wavenumber - p.mean_wavenumber)
                )
                flat = flatfield.FlatField(
                    own[id(donor)].profile,  # type: ignore[union-attr]
                    f"borrowed from '{donor.acquisition.name}' ({p.stack.tiles} tiles)",
                )
            else:
                flat = flatfield.FlatField(
                    np.ones(p.stack.tile_shape, dtype=np.float32),
                    f"none (only {p.stack.tiles} tiles)",
                )
                reporter.warning(
                    f"'{p.acquisition.name}': too few tiles for a flat-field, left uncorrected"
                )
        mosaic.register_and_view(p, flat, _factors(float(np.mean(p.pixel_um))))
        p.tile_means = np.empty((0, 0, 0), dtype=np.float32)
        low, high = flat.span
        reporter.detail(
            f"{p.acquisition.name}: illumination {low:.2f} to {high:.2f} ({flat.source}); "
            f"{p.check.describe() if p.check else ''}"
        )
        assert p.registration is not None
        reporter.detail(f"{p.acquisition.name}: {p.registration.describe()}")


def reference_index(prepared: list[Prepared]) -> int:
    """Image 0: the largest footprint (within 2 %), then CH-region channels, then most channels."""

    def area(p: Prepared) -> float:
        rows, cols = p.shape
        return rows * cols * float(np.prod(p.pixel_um))

    largest = max(area(p) for p in prepared)
    tied = [i for i, p in enumerate(prepared) if area(p) >= (1 - _AREA_TIE) * largest]

    def key(index: int) -> tuple[bool, int, int]:
        wn = prepared[index].stack.wavenumbers
        ch = bool(np.any((wn >= _CH_REGION[0]) & (wn <= _CH_REGION[1])))
        return (ch, prepared[index].stack.channels, -index)

    return max(tied, key=key)


def place_all(
    prepared: list[Prepared], reporter: StepReporter
) -> tuple[list[Prepared], list[placement.Placement]]:
    """Step 3: order the images (reference first) and place each on the reference."""
    reporter.step(3, STEPS, "Placing acquisitions")
    ref = reference_index(prepared)
    ordered = [prepared[ref]] + [p for i, p in enumerate(prepared) if i != ref]
    placements = [placement.reference_placement()]
    reporter.detail(f"Reference image: {ordered[0].acquisition.name}")
    for p in ordered[1:]:
        found = placement.place(ordered[0], p, list(zip(ordered, placements)))
        placements.append(found)
        reporter.detail(f"{p.acquisition.name}: {found.describe()}")
        if found.moved_um > tech.PLACEMENT_REPORT_UM:
            reporter.warning(
                f"'{p.acquisition.name}' lies {found.moved_um:.0f} µm from its stage position "
                "relative to the reference (the slide or stage frame moved between scans); "
                f"placed by {found.method}"
            )
    return ordered, placements


def _preliminary(
    p: Prepared, place: placement.Placement, settings: RamanRunSettings
) -> ome_write.OmeImage:
    rows, cols = p.shape
    infos = images.channel_infos(p)
    return ome_write.OmeImage(
        name=p.acquisition.name,
        shape_yx=(rows, cols),
        dtype=np.dtype(np.float32),
        rgb=False,
        channels=infos,
        colours=[tech.OTHER_COLOUR] * len(infos),
        pixel_size=np.asarray(p.pixel_um, dtype=np.float64),
        significant_bits=None,
        objective=(None, None),
        contours=[],
        annotations=images.image_annotations(p, place, settings),
    )


def _file_annotations(
    provenance: Provenance,
    inventory_json: str,
    display_json: str,
    tissue_fraction: float,
) -> dict[str, object]:
    return {
        PREPROCESSING_FINGERPRINT_KEY: provenance.key,
        INPUT_SIGNATURE_KEY: provenance.signature,
        "focus_method": tech.METHOD_VERSION,
        "focus_modality": "raman",
        "focus_skipped_elements": inventory_json,
        "focus_tissue_fraction": f"{tissue_fraction:.4f}",
        "focus_display": display_json,
    }


def write(
    ordered: list[Prepared],
    placements: list[placement.Placement],
    settings: RamanRunSettings,
    provenance: Provenance,
    inventory_json: str,
    output_file: str,
    reporter: StepReporter,
) -> None:
    """Step 4: process and write every image in order, then complete the OME-XML."""
    reporter.step(4, STEPS, "Processing spectra and writing OME-TIFF")
    workdir = os.path.join(
        os.path.dirname(output_file),
        f".{os.getpid()}.stage.{os.path.basename(output_file)}",
    )
    os.makedirs(workdir, exist_ok=True)
    built: list[images.BuiltImage] = []
    digest = hashlib.sha256()
    display_settings: dict[str, object] = {}
    contours: list[np.ndarray] = []
    tissue_fraction = 0.0
    try:
        with atomic_path(output_file) as tmp:
            with ome_write.ImagesWriter(tmp, tech.CODEC) as writer:
                for index, (p, place) in enumerate(zip(ordered, placements)):
                    image = images.build(
                        p, place, settings, workdir, f"i{index}_", reporter
                    )
                    ome_write.update_digest(digest, image.staged.levels[0], False)
                    rank = "" if image.rank is None else f", spectral rank {image.rank}"
                    rows, cols = p.shape
                    reporter.detail(
                        f"{image.name}: {cols} x {rows} px, {len(image.channels)} channel"
                        f"{'s' if len(image.channels) != 1 else ''}{rank}"
                    )
                    description = None
                    if index == 0:
                        display_settings, contours, tissue_fraction = (
                            output.display_and_tissue(
                                image, [float(v) for v in p.stack.wavenumbers]
                            )
                        )
                        values = _file_annotations(
                            provenance,
                            inventory_json,
                            json.dumps(display_settings, separators=(",", ":")),
                            tissue_fraction,
                        )
                        pre = [
                            _preliminary(q, pl, settings)
                            for q, pl in zip(ordered, placements)
                        ]
                        pre[0] = output.ome_images([image], contours, display_settings)[
                            0
                        ]
                        description = ome_write.build_ome_xml(pre, values)
                    writer.write(image.staged, description)
                    built.append(image)
                    for name in os.listdir(workdir):
                        if name.startswith(f"i{index}_"):
                            ome_write.remove_workdir(os.path.join(workdir, name))
            values = _file_annotations(
                provenance,
                inventory_json,
                json.dumps(display_settings, separators=(",", ":")),
                tissue_fraction,
            )
            values[PIXEL_DIGEST_KEY] = digest.hexdigest()
            _supersession(values, provenance, reporter)
            final = ome_write.build_ome_xml(
                output.ome_images(built, contours, display_settings), values
            )
            ome_write.replace_description(tmp, final)
    finally:
        ome_write.remove_workdir(workdir)


def _supersession(
    values: dict[str, object], provenance: Provenance, reporter: StepReporter
) -> None:
    """Record the replaced file when the new pixels equal it; warn when they differ."""
    if provenance.previous is None:
        return
    previous_digest, superseded = provenance.previous
    if previous_digest == values[PIXEL_DIGEST_KEY]:
        if superseded:
            values[SUPERSEDED_SIGNATURE_KEY] = json.dumps(superseded)
        reporter.detail("Pixels identical to the previous output")
    else:
        reporter.warning(
            "The regenerated image differs from the previous one: alignments made on it must be redone"
        )


def process_lif(
    path: str,
    settings: RamanRunSettings,
    reporter: StepReporter,
    output_file: str,
    provenance: Provenance,
) -> None:
    """Preprocess one LIF session into one multi-image OME-TIFF."""
    lif = LifFile(path)
    prepared, inventory_json = inventory(lif, path, reporter)
    stitch(prepared, reporter)
    ordered, placements = place_all(prepared, reporter)
    write(
        ordered, placements, settings, provenance, inventory_json, output_file, reporter
    )
