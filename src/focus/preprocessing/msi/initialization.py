"""Load and pair the source spectra of queued samples."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    pass


from focus.preprocessing.msi.settings import _step
from focus.preprocessing.msi.sample import MsiSample
from focus.preprocessing.msi.technology import SampleProfile


def initialize_samples(
    iter_samples: list[MsiSample], reporter: StepReporter, profile: SampleProfile
) -> None:
    """Load and pair the source spectra of queued samples of one sample type."""

    # STEP 1: Initialize each sample to load the metadata
    for sample in reporter.tqdm(iter_samples, *_step(1), unit="sample"):
        sample._step_reporter = reporter
        sample.stage_pairing_fallback = profile.stage_pairing_fallback
        sample.initialize_sample()
