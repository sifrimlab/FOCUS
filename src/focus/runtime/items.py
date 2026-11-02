"""Resolve sample identifiers for reporting iterators."""

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def sample_id(item: T, id_of: Callable[[T], str | None] | None = None) -> str | None:
    """Sample id of a loop item: ``id_of(item)``, the item itself if a str, or its ``sample_id``."""
    if id_of is not None:
        return id_of(item)
    if isinstance(item, str):
        return item
    sid = getattr(item, "sample_id", None)
    return sid if isinstance(sid, str) else None
