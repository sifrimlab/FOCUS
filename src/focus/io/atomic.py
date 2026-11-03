"""Write files through a temporary sibling so a crash never leaves a truncated output."""

from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike
import os


@contextmanager
def atomic_path(path: str | PathLike[str]) -> Iterator[str]:
    """Yield a temporary sibling of ``path``; rename it over ``path`` when the block succeeds.

    The temporary file is removed when the block raises, so a later run never mistakes a partial
    write for a cache.
    """
    head, tail = os.path.split(os.fspath(path))
    tmp = os.path.join(head, f".{os.getpid()}.tmp.{tail}")
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
