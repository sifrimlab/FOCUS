"""Collect stage output paths without changing sample or modality ordering."""

from focus.pipeline.types import ModalityFiles, StageFiles


def collect_outputs(files: ModalityFiles) -> StageFiles | None:
    """Group merged and sample paths, omitting a stage with no output files.

    Parameters
    ----------
    files
        Modality names mapped to ordered sample paths, including optional ``merged``.

    Returns
    -------
    StageFiles or None
        Existing GUI output representation, or None when both collections are empty.
    """
    merged: list[str] = []
    per_modality: dict[str, list[str]] = {}
    for name, sample_files in files.items():
        for sample_id, path in sample_files.items():
            if sample_id == "merged":
                merged.append(path)
            else:
                per_modality.setdefault(name, []).append(path)
    return (
        {"merged": merged, "per_modality": per_modality}
        if merged or per_modality
        else None
    )
