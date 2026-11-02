"""Coordinate MSI cache discovery, scientific processing, and merged persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from focus.preprocessing.msi.cache import cached_files
from focus.preprocessing.msi.feature_building import build_features
from focus.preprocessing.msi.feature_metadata import feature_metadata
from focus.preprocessing.msi.initialization import initialize_samples
from focus.preprocessing.msi.merging import merge_samples
from focus.preprocessing.msi.provenance import settings_fingerprint
from focus.preprocessing.msi.resume import resume_samples
from focus.preprocessing.msi.run_state import MsiRunSettings, ResumeState
from focus.preprocessing.msi.sample_writing import write_samples
from focus.preprocessing.msi.selection import select_references
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset


def execute_dataset(
    dataset: MsiDataset, settings: MsiRunSettings, reporter: StepReporter
) -> dict[str, str]:
    """Reuse a complete cache or process queued samples before rebuilding the merge."""
    processed: dict[str, str] = {}
    key = settings_fingerprint(dataset, settings)
    cached = cached_files(dataset, settings.force, key, processed, reporter)
    if cached is not None:
        return cached
    resume = resume_samples(dataset, settings.force, key, reporter)
    process_samples(dataset, settings, resume, key, reporter)
    merge_samples(
        dataset,
        resume.files,
        resume.spot_sizes,
        key,
        resume.stored_references,
        reporter,
    )
    return resume.files


def process_samples(
    dataset: MsiDataset,
    settings: MsiRunSettings,
    resume: ResumeState,
    key: str,
    reporter: StepReporter,
) -> None:
    """Execute the ordered numerical stages, preserving resume state and write barriers."""
    initialize_samples(resume.samples, settings.sample_type, reporter)
    select_references(
        dataset,
        resume.samples,
        resume.stored_references,
        settings.mass_tolerance,
        settings.detect_background,
        settings.references,
        settings.standards,
        reporter,
    )
    build_features(
        dataset,
        resume.samples,
        resume.partial,
        settings.mass_tolerance,
        settings.frequency_threshold,
        reporter,
    )
    features = feature_metadata(
        dataset, settings.normalization, settings.standards, settings.mass_tolerance
    )
    write_samples(dataset, resume, features, key, settings, reporter)
