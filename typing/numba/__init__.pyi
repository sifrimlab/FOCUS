"""Typing-only identity signatures for FOCUS's Numba decorator usage.

The runtime decorator compiles a callable without changing its Python call
signature. Compilation and disk-cache behavior are exercised by regression tests.
"""

from collections.abc import Callable
from typing import Any, TypeVar, overload

FunctionT = TypeVar("FunctionT", bound=Callable[..., Any])

@overload
def njit(function: FunctionT, /) -> FunctionT: ...
@overload
def njit(
    *, cache: bool = ..., **options: object
) -> Callable[[FunctionT], FunctionT]: ...
