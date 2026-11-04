"""Discover readable dataset folders, sample identifiers and complete MSI file pairs."""

import os


from focus.io.paths import FocusOutputDirectories, is_reserved_directory_name


def validate_path_readable(path: str) -> None:
    """Validate that a path exists and is readable.

    Raises FileNotFoundError if the path does not exist,
    PermissionError if the path is not readable.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"The specified path does not exist: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"The specified path is not readable: {path}")


def discover_sample_ids(
    path: str, ignore_samples: list[str] | None = None
) -> list[str]:
    """Discover sample IDs from subdirectories under the given path.

    Returns a sorted list of directory names, excluding FOCUS system directories (matched
    case-insensitively, including the raw-data folder `raw`) and any sample IDs listed in ignore_samples.
    """
    ignored = set(ignore_samples or [])
    sample_ids = sorted(
        d
        for d in os.listdir(path)
        if os.path.isdir(os.path.join(path, d))
        and not is_reserved_directory_name(d)
        and d not in ignored
    )
    return sample_ids


_IMZML_EXTENSION = ".imzML"

_IBD_EXTENSION = ".ibd"


def find_imzml_pair(directory: str) -> tuple[str, str] | None:
    """Resolve one MSI ion mode directory to its complete imzML/IBD file pair.

    This is the single source of truth for "was this ion mode acquired?". Directory existence is
    NOT evidence: the GUI (SampleManager) scaffolds both pos/ and neg/ for every MSI modality, so
    an empty ion mode directory must read as "this polarity was not acquired" rather than as
    "a second polarity is present".

    Returns the absolute (imzML, IBD) paths, or None when the directory is missing or holds
    neither an imzML nor an IBD file — i.e. this ion mode was not acquired.

    Raises FileNotFoundError when the directory holds imzML and/or IBD files but no pair sharing
    a base name. A file of either kind means the user meant to use this ion mode, so a partial
    pair is a broken or half-transferred acquisition and must never be silently skipped.
    """
    if not os.path.isdir(directory):
        return None

    # Files only: a *directory* named "data.ibd" must not satisfy the pair.
    names = sorted(
        f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))
    )
    imzml_files = [f for f in names if f.endswith(_IMZML_EXTENSION)]
    ibd_files = [f for f in names if f.endswith(_IBD_EXTENSION)]

    # Neither kind of file: this ion mode was not acquired. This is what lets the GUI scaffold
    # both pos/ and neg/ unconditionally without the user having to delete the unused one.
    if not imzml_files and not ibd_files:
        return None

    # Something is here, so this ion mode was intended: it must resolve to a complete pair.
    # Sorted listing -> deterministic choice when a directory holds several acquisitions
    # (os.listdir order is filesystem-dependent).
    ibd_stems = {f[: -len(_IBD_EXTENSION)] for f in ibd_files}
    for name in imzml_files:
        stem = name[: -len(_IMZML_EXTENSION)]
        if stem in ibd_stems:
            return os.path.join(directory, name), os.path.join(
                directory, stem + _IBD_EXTENSION
            )

    raise FileNotFoundError(
        f"Incomplete MSI acquisition in '{directory}': found {_IMZML_EXTENSION} files "
        f"{imzml_files or '[]'} and {_IBD_EXTENSION} files {ibd_files or '[]'}, but no pair "
        f"sharing a base name. Each ion mode directory must hold a complete "
        f"{_IMZML_EXTENSION} + {_IBD_EXTENSION} pair, or be left empty if that ion mode was "
        f"not acquired."
    )


def create_output_directories(
    path: str, sample_ids: list[str], modality_name: str
) -> None:
    """Create per-sample and merged preprocessing output directories."""
    for sample_id in sample_ids:
        os.makedirs(
            os.path.join(
                path, sample_id, FocusOutputDirectories.PREPROCESSING, modality_name
            ),
            exist_ok=True,
        )
    os.makedirs(
        os.path.join(
            path, FocusOutputDirectories.MERGED, FocusOutputDirectories.PREPROCESSING
        ),
        exist_ok=True,
    )
