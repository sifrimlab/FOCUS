"""Public API for ``focus.annotations``.

Re-exports are resolved lazily (PEP 562 ``__getattr__``) so importing this
package never eagerly pulls in its submodules. This keeps the package free of
import cycles and robust to any import order, while
``from focus.annotations import transfer_annotations`` keeps working.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> the submodule that defines it.
_EXPORTS = {
	"transfer_annotations": "focus.annotations.transfer",
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
	from focus.annotations.transfer import transfer_annotations
