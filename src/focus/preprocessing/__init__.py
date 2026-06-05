"""Public API for ``focus.preprocessing``.

Re-exports are resolved lazily (PEP 562 ``__getattr__``) so that importing this
package never eagerly pulls in the heavy modality submodules. This keeps the
package free of import cycles and robust to any import order, while
``from focus.preprocessing import preprocess_modality`` (and friends) keeps
working exactly as before. Importing a submodule directly
(``from focus.preprocessing import transcriptomic``) also works — the import
machinery falls back to a submodule import when the name is not an attribute.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> the submodule that defines it. Order mirrors the original __all__.
_EXPORTS = {
	"preprocess_modality": "focus.preprocessing.preprocessing",
	"BaseSample": "focus.preprocessing.base",
	"BaseDataset": "focus.preprocessing.base",
	"MicroscopyImage": "focus.preprocessing.microscopy_image",
	"MicroscopyImageDataset": "focus.preprocessing.microscopy_image",
	"MsiSample": "focus.preprocessing.lipidomics",
	"MsiDataset": "focus.preprocessing.lipidomics",
	"RamanImage": "focus.preprocessing.raman",
	"RamanMetadata": "focus.preprocessing.raman",
	"RamanDataset": "focus.preprocessing.raman",
	"SpatialTranscriptomic": "focus.preprocessing.transcriptomic",
	"SpatialTranscriptomicDataset": "focus.preprocessing.transcriptomic",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
	try:
		module = _EXPORTS[name]
	except KeyError:
		raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
	return getattr(import_module(module), name)


def __dir__():
	return sorted(__all__)


if TYPE_CHECKING:  # eager imports for type checkers / IDEs only (not executed at runtime)
	from focus.preprocessing.preprocessing import preprocess_modality
	from focus.preprocessing.base import BaseSample, BaseDataset
	from focus.preprocessing.microscopy_image import MicroscopyImage, MicroscopyImageDataset
	from focus.preprocessing.lipidomics import MsiSample, MsiDataset
	from focus.preprocessing.raman import RamanImage, RamanMetadata, RamanDataset
	from focus.preprocessing.transcriptomic import SpatialTranscriptomic, SpatialTranscriptomicDataset
