"""Public API for ``focus.registration``.

Re-exports are resolved lazily (PEP 562 ``__getattr__``) so importing this
package never eagerly pulls in its submodules. This keeps the package free of
import cycles and robust to any import order, while
``from focus.registration import FeatureExtractorRegistration`` keeps working.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> the submodule that defines it.
_EXPORTS = {
	"FeatureExtractorRegistration": "focus.registration.registration",
	"SpotInterpolationRegistration": "focus.registration.registration",
	"SpotAggregationRegistration": "focus.registration.spot_aggregation",
	"RamanPixelInterpolationRegistration": "focus.registration.raman_pixel",
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
	from focus.registration.registration import FeatureExtractorRegistration, SpotInterpolationRegistration
	from focus.registration.spot_aggregation import SpotAggregationRegistration
	from focus.registration.raman_pixel import RamanPixelInterpolationRegistration
