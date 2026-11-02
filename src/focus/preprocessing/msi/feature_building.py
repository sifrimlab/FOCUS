"""Build and annotate the global m/z backbone when needed."""

from __future__ import annotations
from focus.preprocessing.msi.lipid_database import annotate_masses
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset


import gc

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.settings import _step
from focus.preprocessing.msi.sample import MsiSample
from focus.preprocessing.msi.backbone import _BackboneBuilder


def build_features(
    dataset: MsiDataset,
    iter_samples: list[MsiSample],
    has_partial_cache: bool,
    mass_tolerance: int,
    frequency_threshold: float,
    reporter: StepReporter,
) -> None:
    """Build and annotate the global m/z backbone when needed."""

    if not has_partial_cache:
        # STEP 4: Find the features of every recalibrated sample (tissue spectra when background
        # detection ran) and keep the frequent ones; only their m/z and presence are kept.
        backbone = _BackboneBuilder(mass_tolerance, frequency_threshold)
        for sample in reporter.tqdm(iter_samples, *_step(4), unit="sample"):
            _, _, filtered_mz, _ = sample.load_payload(
                annotation_db=dataset.lipid_annotation_db, mass_tolerance=mass_tolerance
            )
            for mode, (found, kept) in backbone.add_sample(filtered_mz).items():
                reporter.detail(
                    f"m/z features ({str(mode).upper()}): {found} found, {kept} present in at least "
                    f"{frequency_threshold:g} times the spectra of the most present one"
                )
            del filtered_mz  # Free memory
            gc.collect()

        # STEP 5: Group the samples' features into the backbone (see _BackboneBuilder)
        reporter.step(*_step(5))
        dataset.reference_mz = backbone.build()
        del backbone  # Free memory
        gc.collect()

        reporter.detail(
            f"Reference backbone: {len(dataset.reference_mz.get(MsiIonMode.POSITIVE, []))} m/z values (POSITIVE), "
            f"{len(dataset.reference_mz.get(MsiIonMode.NEGATIVE, []))} m/z values (NEGATIVE)"
        )

        annotate_backbone(dataset, mass_tolerance, reporter)
    else:
        # The backbone and annotations come from the cached per-sample files.
        for index in (4, 5, 6):
            reporter.step(*_step(index), cached=True)


def annotate_backbone(
    dataset: MsiDataset, mass_tolerance: int, reporter: StepReporter
) -> None:
    """Reset and populate optional lipid annotations after the global backbone is finalized."""
    # STEP 5: For each ion mode, annotate the reference M/Z vector if a lipid annotation database is provided
    reporter.step(*_step(6))
    dataset.lipid_annotations = {}
    if dataset.lipid_annotation_db is not None:
        for mode in dataset.reference_mz.keys():
            dataset.lipid_annotations[mode] = annotate_masses(
                dataset.lipid_annotation_db,
                dataset.reference_mz[mode],
                mode,
                mass_tolerance=mass_tolerance,
            )
