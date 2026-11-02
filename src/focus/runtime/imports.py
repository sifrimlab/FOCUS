"""Resolve declared package exports without eagerly loading scientific dependencies."""

from collections.abc import Mapping
from importlib import import_module


def resolve_export(package: str, exports: Mapping[str, str], name: str) -> object:
    """Load one declared symbol, or raise the package's standard missing-attribute error.

    Parameters
    ----------
    package : str
        Requesting package name, used only in the original AttributeError message.
    exports : Mapping[str, str]
        Symbol-to-module declaration; reading it does not import those modules.
    name : str
        Symbol requested by Python's module attribute lookup.

    Returns
    -------
    object
        The module's actual symbol; no proxy, wrapper or compatibility class is created.

    Raises
    ------
    AttributeError
        If the package has no declaration for the requested symbol.
    """
    try:
        module = exports[name]
    except KeyError:
        raise AttributeError(f"module {package!r} has no attribute {name!r}") from None
    return getattr(import_module(module), name)
