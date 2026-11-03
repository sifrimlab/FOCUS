"""Shared ordered sample execution, cache decisions and registration result writing."""

from focus.registration.types import RegistrationJob

from focus.registration.anchor import AnchorArrays
from focus.spatial.footprint import AnchorGeometry

from focus.runtime.reporting import StepReporter

from focus.runtime.reporter_context import get_reporter
from focus.registration.anchor import read_anchor_arrays
from focus.registration.provenance import (
    registration_cached,
    registration_fingerprint,
    stamp_registration,
)
from focus.registration.storage import write_registered
from focus.registration.merging import merge_registered_samples


def register_target(job: RegistrationJob, reporter: StepReporter) -> dict[str, str]:
    """Execute a two-step target strategy and merge its registered sample files.

    Parameters
    ----------
    job : RegistrationJob
        Ordered sample iterator, scientific callback and unchanged cache settings.
    reporter : StepReporter
        Receives the merge step after all sample callbacks have finished.

    Returns
    -------
    dict[str, str]
        Existing sample outputs and the merged output, when available.
    """
    registered = register_samples(job)
    reporter.step(2, 2, "Merging samples")
    return merge_registered_samples(
        job.path,
        registered,
        job.target_name,
        job.registration_type,
        force_recomputing=job.force_recomputing,
        extra_uns=job.merged_uns,
        finalize=job.finalize_merged,
    )


def register_samples(job: RegistrationJob) -> dict[str, str]:
    """
    The per-sample loop every registration engine shares.

    ``settings`` is a dict, or a callable ``settings(sample_id) -> dict``, of the method
    settings the output depends on. For each sample: read the aligned anchor, skip with a
    warning when it has no coordinates in the target frame, reuse the output when its
    fingerprint matches, otherwise build the
    footprint geometry, call ``compute(sample_id, anchor, geom) -> (AnnData | None, dict)``
    and write the result with its registration stamps. The dict returned by ``compute`` is
    merged into the geometry stamp.

    Returns
    -------
    dict[str, str]
        ``{sample_id: registered_file}`` for every sample that has an output.
    """
    from focus.io.paths import registration_file

    reporter = job.reporter or get_reporter()
    registered: dict[str, str] = {}
    for sample_id in job.samples:
        out = registration_file(job.path, sample_id, job.target_name, "h5ad")
        anchor = read_anchor_arrays(job.anchor_files[sample_id], job.target_name)
        if anchor is None:
            # Only the aligned key locates the anchor spots in the target's frame; obsm['spatial']
            # is the anchor's own frame and would place every footprint elsewhere.
            reporter.warning(
                f"Sample skipped: the anchor '{job.anchor_name}' has no obsm['{job.target_name}_spatial'] (was alignment performed?)"
            )
            continue
        sample_settings = (
            job.settings(sample_id) if callable(job.settings) else job.settings
        )
        key = registration_fingerprint(
            job.registration_type, anchor, job.target_files[sample_id], sample_settings
        )
        if not job.force_recomputing and registration_cached(out, key):
            reporter.cached()
            registered[sample_id] = out
            continue

        geom = sample_geometry(anchor, job.target_name, reporter)
        if geom is None:
            continue

        adata, extra = job.compute(sample_id, anchor, geom)
        if adata is None:
            continue
        stamp_registration(
            adata, job.registration_type, key, {**geom.describe(), **extra}
        )
        write_registered(adata, out)
        registered[sample_id] = out
    return registered


def sample_geometry(
    anchor: AnchorArrays, target_name: str, reporter: StepReporter
) -> AnchorGeometry | None:
    """Build anchor footprints and report missing sizes, invalid geometry, and unplaced rows."""
    if not anchor.spot_size_given:
        reporter.warning("No spot_size in the anchor: using the default [1.0, 1.0]")
    try:
        geom = AnchorGeometry.from_arrays(
            anchor.spatial, anchor.mapped, anchor.spot_size
        )
    except ValueError as e:
        reporter.warning(f"Sample skipped: {e}")
        return None
    n_unplaced = int((~geom.valid).sum())
    if n_unplaced:
        reporter.detail(
            f"{n_unplaced}/{anchor.n_obs} anchor spots cannot be placed in '{target_name}': zero rows"
        )

    return geom
