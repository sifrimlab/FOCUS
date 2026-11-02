"""Compare complete decoded AnnData files for local refactoring verification."""

from __future__ import annotations
from focus.spatial.matrices import SparseStorage

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse


def compare_value(reference: object, candidate: object, location: str) -> None:
    """Assert exact values, dtypes, ordering, and sparse storage at ``location``.

    Parameters
    ----------
    reference, candidate
        Decoded scientific values to compare without modifying either value.
    location
        Container path included in assertion failures.

    Raises
    ------
    AssertionError
        If values, representations, or metadata differ.
    """
    assert type(reference) is type(candidate), location
    if sparse.issparse(reference):
        left = cast(SparseStorage, reference)
        right = cast(SparseStorage, candidate)
        assert left.format == right.format, location
        assert left.shape == right.shape, location
        assert left.dtype == right.dtype, location
        for name in ("data", "indices", "indptr", "row", "col", "offsets"):
            if hasattr(left, name):
                compare_value(
                    getattr(left, name), getattr(right, name), location + "/" + name
                )
    elif isinstance(reference, pd.DataFrame):
        pd.testing.assert_frame_equal(
            reference, cast(pd.DataFrame, candidate), check_exact=True
        )
    elif isinstance(reference, pd.Series):
        pd.testing.assert_series_equal(
            reference, cast(pd.Series, candidate), check_exact=True
        )
    elif isinstance(reference, pd.Index):
        pd.testing.assert_index_equal(reference, cast(pd.Index, candidate), exact=True)
    elif isinstance(reference, np.ndarray):
        np.testing.assert_array_equal(
            reference, candidate, strict=True, err_msg=location
        )
    elif isinstance(reference, Mapping):
        other = cast(Mapping[object, object], candidate)
        assert list(reference) == list(other), location
        for key in reference:
            compare_value(reference[key], other[key], f"{location}/{key}")
    elif isinstance(reference, (list, tuple)):
        other_sequence = cast(list[object] | tuple[object, ...], candidate)
        assert len(reference) == len(other_sequence), location
        for index, (left_item, right_item) in enumerate(zip(reference, other_sequence)):
            compare_value(left_item, right_item, f"{location}/{index}")
    else:
        np.testing.assert_equal(reference, candidate, err_msg=location)


def compare_files(reference: Path, candidate: Path) -> None:
    """Read two H5AD paths and assert complete decoded equality.

    Parameters
    ----------
    reference, candidate
        Existing H5AD files. Both are loaded in memory; inputs remain unchanged.

    Raises
    ------
    AssertionError
        If any decoded field differs.
    OSError
        If an input cannot be read.
    """
    left, right = ad.read_h5ad(reference), ad.read_h5ad(candidate)
    for name in ("X", "obs", "var", "uns", "obsm", "varm", "obsp", "varp", "layers"):
        compare_value(getattr(left, name), getattr(right, name), name)
    assert (left.raw is None) == (right.raw is None), "raw"
    if left.raw is not None:
        for name in ("X", "var", "varm", "obs_names"):
            compare_value(
                getattr(left.raw, name), getattr(right.raw, name), "raw/" + name
            )


def main() -> None:
    """Parse two file paths and fail the command if decoded contents differ."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    arguments = parser.parse_args()
    compare_files(arguments.reference, arguments.candidate)
    print("Complete decoded contents match exactly.")


if __name__ == "__main__":
    main()
