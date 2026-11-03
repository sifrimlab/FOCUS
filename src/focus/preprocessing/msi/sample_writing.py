"""Process and serialize queued samples with the original write barrier."""

from __future__ import annotations
from concurrent.futures import Future
from focus.preprocessing.msi.sample_data import prepare_sample
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter
from focus.preprocessing.msi.run_state import ResumeState, FeatureState, MsiRunSettings

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset


import concurrent.futures
import gc

import focus.io.anndata as anndata_io
from focus.preprocessing.msi.settings import _step
from focus.preprocessing.msi.technology import SampleProfile


def write_samples(
    dataset: MsiDataset,
    resume: ResumeState,
    features: FeatureState,
    dataset_key: str,
    settings: MsiRunSettings,
    profile: SampleProfile,
    reporter: StepReporter,
) -> None:
    """Process and serialize queued samples with the original write barrier."""

    # STEP 7: Process one sample at a time: bin, normalize, build AnnData, write, free.
    # This keeps peak RAM proportional to a single sample rather than all samples simultaneously.
    # spot_sizes was initialized in the partial-resume block above and may already contain entries
    # from cached samples; this loop only appends entries for samples in resume.samples.
    write_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    write_futures: list[Future[None]] = []

    try:
        for sample in reporter.tqdm(resume.samples, *_step(7), unit="sample"):
            adata, output_file, stored_reference = prepare_sample(
                dataset,
                sample,
                resume,
                features,
                dataset_key,
                settings.mass_tolerance,
                settings.normalization,
                settings.resolution,
                profile,
            )
            for f in write_futures:
                f.result()
            write_futures.clear()
            write_futures.append(
                write_executor.submit(
                    anndata_io.write_h5ad_atomic,
                    adata,
                    output_file,
                    compression=dataset._H5AD_INTERMEDIATE_COMPRESSION,
                )
            )
            resume.files[sample.sample_id] = output_file
            resume.stored_references[sample.sample_id] = stored_reference
            resume.stored_pools[sample.sample_id] = dict(sample.recalibration_pool)

        for f in write_futures:
            f.result()
    finally:
        write_executor.shutdown(wait=True)
    gc.collect()
