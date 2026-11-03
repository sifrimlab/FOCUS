"""Owner state and reporting must remain shared across extracted components."""

from pathlib import Path
from unittest.mock import patch

import anndata as ad
import numpy as np

from focus.alignment.cache import AlignmentCache
from focus.alignment.coordinator import DirectMappingAligner
from focus.runtime.reporting import StepReporter


def test_alignment_warning_state_belongs_to_owner() -> None:
    """Warn once across repeated access and replacement cache components."""
    aligner = DirectMappingAligner.__new__(DirectMappingAligner)
    aligner._reference_modality_type = "microscopy_image"
    aligner._reference_modality_name = "he"
    aligner._reporter = StepReporter()
    assert aligner.cache is aligner.cache
    with (
        patch.object(AlignmentCache, "_fixed_annotations", return_value={}),
        patch.object(aligner._reporter, "warning") as warning,
    ):
        aligner.cache._crop_offset("s")
        AlignmentCache(aligner)._crop_offset("s")
        assert warning.call_count == 1
    assert aligner._warned_offset


def test_alignment_payload_uses_owner_basis(tmp_path: Path) -> None:
    """Transfer the lattice basis from the backed reader to browser metadata."""
    rows, columns = np.mgrid[:8, :8]
    data = ad.AnnData(np.zeros((rows.size, 1), dtype=np.float32))
    data.obsm["spatial"] = np.column_stack([rows.ravel(), columns.ravel()]) * 16
    data.uns["spot_size"] = [16, 16]
    path = tmp_path / "spots.h5ad"
    data.write_h5ad(path)
    aligner = DirectMappingAligner.__new__(DirectMappingAligner)
    aligner._exact_spots = False
    metadata, _, _, _ = aligner.inputs._prepare_spot_data(str(path), "st")
    assert aligner._spot_basis is not None
    assert metadata["spot_basis"] == aligner._spot_basis


def test_regeneration_reports_through_the_dataset_reporter(tmp_path: Path) -> None:
    """Route the supersession detail through the reporter supplied by dataset execution."""
    from focus.preprocessing.microscopy.output import supersession
    from focus.preprocessing.microscopy.workflow import Provenance

    reporter = StepReporter()
    values: dict[str, object] = {}
    previous = Provenance("key", "{}", ("digest", ["old", 1, 2]))
    with patch.object(reporter, "detail") as detail:
        supersession("digest", previous, values, reporter)
        detail.assert_called_once_with("Pixels identical to the previous output")
    assert values["focus_superseded_signature"] == '["old", 1, 2]'
    with patch.object(reporter, "warning") as warning:
        supersession("other", previous, {}, reporter)
        warning.assert_called_once()
