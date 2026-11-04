"""Public API for ``focus.preprocessing``.

Re-exports are resolved lazily (PEP 562 ``__getattr__``) so that importing this
package never eagerly pulls in the heavy modality submodules. This keeps the
package free of import cycles and robust to any import order, while
``from focus.preprocessing import preprocess_modality`` (and friends) keeps
working exactly as before. Modality implementations are available under ``msi``, ``microscopy``, ``raman``,
and ``transcriptomics``; these packages have no compatibility aliases.
"""

from focus.runtime.imports import resolve_export
from typing import TYPE_CHECKING

# Public name -> the submodule that defines it. Order mirrors the original __all__.
_EXPORTS = {
    "preprocess_modality": "focus.preprocessing.dispatcher",
    "SampleSource": "focus.preprocessing.base",
    "DatasetState": "focus.preprocessing.base",
    "MicroscopyImage": "focus.preprocessing.microscopy.sample",
    "MicroscopyImageDataset": "focus.preprocessing.microscopy.dataset",
    "MsiSample": "focus.preprocessing.msi.sample",
    "MsiDataset": "focus.preprocessing.msi.dataset",
    "RamanImage": "focus.preprocessing.raman.sample",
    "RamanDataset": "focus.preprocessing.raman.dataset",
    "SpatialTranscriptomic": "focus.preprocessing.transcriptomics.sample",
    "SpatialTranscriptomicDataset": "focus.preprocessing.transcriptomics.dataset",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> object:
    """Resolve a declared public export lazily; raise AttributeError for unknown names."""
    return resolve_export(__name__, _EXPORTS, name)


def __dir__() -> list[str]:
    """Return the sorted public names without loading optional dependencies."""
    return sorted(__all__)


if (
    TYPE_CHECKING
):  # eager imports for type checkers / IDEs only (not executed at runtime)
    from focus.preprocessing.dispatcher import (
        preprocess_modality as preprocess_modality,
    )
    from focus.preprocessing.base import (
        SampleSource as SampleSource,
        DatasetState as DatasetState,
    )
    from focus.preprocessing.microscopy.sample import MicroscopyImage as MicroscopyImage
    from focus.preprocessing.microscopy.dataset import (
        MicroscopyImageDataset as MicroscopyImageDataset,
    )
    from focus.preprocessing.msi.sample import MsiSample as MsiSample
    from focus.preprocessing.msi.dataset import MsiDataset as MsiDataset
    from focus.preprocessing.raman.sample import RamanImage as RamanImage
    from focus.preprocessing.raman.dataset import RamanDataset as RamanDataset
    from focus.preprocessing.transcriptomics.sample import (
        SpatialTranscriptomic as SpatialTranscriptomic,
    )
    from focus.preprocessing.transcriptomics.dataset import (
        SpatialTranscriptomicDataset as SpatialTranscriptomicDataset,
    )
