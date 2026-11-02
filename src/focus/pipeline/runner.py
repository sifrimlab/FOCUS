"""Run pipeline stages in their established order with progress and cancellation."""

from focus.pipeline.stages import PipelineStages

from focus.config.types import PipelineConfig
from focus.runtime.types import ProgressCallback
from focus.runtime.cancellation import CancelToken
from focus.pipeline.types import PipelineFiles


from focus.runtime.reporting import StepReporter
from focus.runtime.cancellation import PipelineCancelled
from focus.runtime.reporter_context import use_reporter


def run(
    config: PipelineConfig,
    progress_callback: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
) -> PipelineFiles:
    """
    Execute the full FOCUS pipeline: preprocessing, alignment, registration, and MuData compilation.

    Parameters
    ----------
    config : dict
            Validated configuration dictionary (output of focus.config.validation.parse_config).
    progress_callback : callable, optional
            Called at each stage/sample transition with a status dict.
            Signature: progress_callback(status_dict)
    cancel : focus.runtime.cancellation.CancelToken, optional
            When cancelled, the run stops at its next progress call and raises
            ``PipelineCancelled``.

    Returns
    -------
    dict
            Structured output files grouped by pipeline stage. Each present key maps to a dict
            with "merged" (list of merged output paths) and "per_modality" (sample paths grouped by modality).
            Keys: "preprocessing", "alignment", "annotations", "registration", "multimodal".
    """

    reporter = StepReporter(callback=progress_callback, cancel=cancel)
    # Code without a reporter handle (deep helpers) reports through get_reporter().
    use_reporter(reporter)
    try:
        output_files = _run_stages(config, reporter)
        reporter.finish()
        return output_files
    except PipelineCancelled:
        reporter.cancelled()
        raise
    except Exception as e:
        reporter.fail(e)
        raise
    finally:
        use_reporter(None)


def _run_stages(config: PipelineConfig, reporter: StepReporter) -> PipelineFiles:
    """Execute the configured stage contexts and return their output paths."""
    return PipelineStages(config, reporter).run()
