"""Public API for ``focus.registration``.

Re-exports are resolved lazily (PEP 562 ``__getattr__``) so importing this
package never eagerly pulls in its submodules. This keeps the package free of
import cycles and robust to any import order, while
``from focus.registration import FeatureExtractorRegistration`` keeps working.
"""

from focus.runtime.imports import resolve_export
from typing import TYPE_CHECKING

# Public name -> the submodule that defines it.
_EXPORTS = {
    "FeatureExtractorRegistration": "focus.registration.feature_extraction",
    "SpotInterpolationRegistration": "focus.registration.spot_interpolation",
    "RamanPixelInterpolationRegistration": "focus.registration.raman_pixel",
    "PixelCompositionRegistration": "focus.registration.pixel_composition",
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
    from focus.registration.feature_extraction import (
        FeatureExtractorRegistration as FeatureExtractorRegistration,
    )
    from focus.registration.spot_interpolation import (
        SpotInterpolationRegistration as SpotInterpolationRegistration,
    )
    from focus.registration.raman_pixel import (
        RamanPixelInterpolationRegistration as RamanPixelInterpolationRegistration,
    )
    from focus.registration.pixel_composition import (
        PixelCompositionRegistration as PixelCompositionRegistration,
    )
