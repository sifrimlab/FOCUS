"""Dataset settings checks for transcriptomics."""

from focus.config.options import StNormalization
from focus.preprocessing.transcriptomics.run_settings import StRunSettings
from focus.preprocessing.transcriptomics.technology import technology


def validate_settings(settings: StRunSettings) -> None:
    """Raise ValueError for an unknown capture, unit or normalization."""
    technology(settings.capture, settings.unit)
    if settings.normalization not in StNormalization.list():
        raise ValueError(
            f"normalization must be one of {StNormalization.list()}, "
            f"got {settings.normalization!r}"
        )
