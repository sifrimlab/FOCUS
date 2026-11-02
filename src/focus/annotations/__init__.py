"""Public API for ``focus.annotations``.

Re-exports are resolved lazily (PEP 562 ``__getattr__``) so importing this
package never eagerly pulls in its submodules. This keeps the package free of
import cycles and robust to any import order, while
``from focus.annotations import transfer_annotations`` keeps working.
"""

from focus.runtime.imports import resolve_export
from typing import TYPE_CHECKING

# Public name -> the submodule that defines it.
_EXPORTS = {
    "transfer_annotations": "focus.annotations.transfer",
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
    from focus.annotations.transfer import transfer_annotations as transfer_annotations
