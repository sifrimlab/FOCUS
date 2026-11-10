"""Profile spectra through the sample flow: streaming, scratch files and whole-dataset equivalence."""

from pathlib import Path
from typing import Any

import anndata as ad
import numpy as np
import pytest

import focus.preprocessing.msi.centroiding as centroiding
import focus.preprocessing.msi.workflow as workflow
import focus.runtime.resources as resources
from focus.config.keys import MsiMetadata
from focus.config.options import MsiIonMode
from focus.preprocessing.msi.centroiding import centroid_profile
from focus.preprocessing.msi.dataset import MsiDataset
from focus.preprocessing.msi.sample import MsiSample
from tests.msi.data import ANALYTE, CALIBRANTS, synthetic_sample
from tests.msi.profile_data import (
    COORDS,
    grid_for,
    profile_spectrum,
    sample_for,
    write_profile,
    write_profile_imzml,
)


def test_scratch_bytes_do_not_depend_on_the_number_of_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scratch IBD is written in spectrum order whatever the thread count and batch size."""
    sample = sample_for(tmp_path, write_profile(tmp_path / "in", trim=False))
    meta = sample.reader._parse_imzml(
        str(next((tmp_path / "S" / "MSI" / "pos").glob("*.imzML")))
    )
    ibd = str(next((tmp_path / "S" / "MSI" / "pos").glob("*.ibd")))
    outputs = []
    for workers, batch_points in ((1, 10**9), (4, 3000)):
        monkeypatch.setattr(resources, "available_cpus", lambda w=workers: w)
        monkeypatch.setattr(centroiding, "_PICK_BATCH_POINTS", batch_points)
        scratch = str(tmp_path / f"scratch_{workers}.ibd")
        result = centroid_profile(
            ibd,
            meta[MsiMetadata.MZ_DTYPE],
            meta[MsiMetadata.INTENSITIES_DTYPE],
            meta[MsiMetadata.MZ_BINARY_METADATA],
            meta[MsiMetadata.INTENSITIES_BINARY_METADATA],
            grid_for(),
            scratch,
            "test",
        )
        outputs.append((Path(scratch).read_bytes(), result.mz_meta.copy()))
    assert outputs[0][0] == outputs[1][0]
    np.testing.assert_array_equal(outputs[0][1], outputs[1][1])


def test_sample_reads_centroids_and_drops_unpicked_spectra(tmp_path: Path) -> None:
    """The sample swaps to the scratch IBD, reports non-finite spectra as corrupt and cleans up."""
    sample = sample_for(tmp_path, write_profile(tmp_path / "in", nan_in=4))
    sample.initialize_sample()
    scratch = sample._binary_files["pos"]
    assert Path(scratch).parent == Path(sample.output_path)
    assert Path(scratch).exists()
    meta = sample._metadata["pos"]
    assert meta[MsiMetadata.MZ_DTYPE] == np.float64
    assert meta[MsiMetadata.INTENSITIES_DTYPE] == np.float32
    assert meta[MsiMetadata.MZ_BINARY_METADATA].shape[0] == COORDS.shape[0] - 1
    mz_vectors, intensity_vectors = sample.load_payload()
    assert len(mz_vectors["pos"]) == COORDS.shape[0] - 1
    for mz, height in zip(mz_vectors["pos"], intensity_vectors["pos"]):
        assert mz.size == 4
        assert np.all(height > 0)
    expected = np.array([420.1234, 512.3456, 650.4321, 780.7654])
    assert np.all(np.abs(mz_vectors["pos"][0] - expected) / expected * 1e6 < 2.0)
    sample.release_scratch()
    assert not Path(scratch).exists()


def render_dataset(root: Path, as_profile: bool) -> None:
    """Two samples: the synthetic centroids, or the same peaks rendered as zero-trimmed profiles."""
    for seed, sample_id in enumerate(("A", "B")):
        mz_list, intensity_list, coords = synthetic_sample(
            line_drift=(0.0, 30.0 + 10 * seed), seed=seed
        )
        if as_profile:
            rendered = [
                profile_spectrum(
                    mz,
                    height.astype(np.float64),
                    6,
                    low=300.0,
                    high=1100.0,
                    baseline=0.0,
                    noise=0.0,
                    trim=True,
                )
                for mz, height in zip(mz_list, intensity_list)
            ]
            mz_list = [mz for mz, _ in rendered]
            intensity_list = [height for _, height in rendered]
        write_profile_imzml(
            root / sample_id / "MSI" / "pos",
            mz_list,
            intensity_list,
            coords,
            declared="profile" if as_profile else None,
        )


def process(root: Path) -> tuple[dict[str, str], list[MsiSample]]:
    """Run the dataset with the existing defaults."""
    samples = [
        MsiSample(str(root), sample_id, "MSI", ion_mode=MsiIonMode.POSITIVE)
        for sample_id in ("A", "B")
    ]
    return MsiDataset(str(root), samples).process_dataset(mass_tolerance=10), samples


def test_profile_dataset_gives_the_features_of_its_centroids(tmp_path: Path) -> None:
    """The same peaks stored as profile or as centroids produce the same backbone and intensities."""
    render_dataset(tmp_path / "centroid", as_profile=False)
    render_dataset(tmp_path / "profile", as_profile=True)
    centroid_out, _ = process(tmp_path / "centroid")
    profile_out, _ = process(tmp_path / "profile")
    reference = ad.read_h5ad(centroid_out["merged"])
    result = ad.read_h5ad(profile_out["merged"])
    species = np.concatenate([CALIBRANTS, [ANALYTE]])
    mz = result.var["mz"].to_numpy(dtype=np.float64)
    assert mz.size == reference.var["mz"].size == species.size
    np.testing.assert_allclose(
        mz, reference.var["mz"].to_numpy(dtype=np.float64), rtol=2e-6
    )
    a = reference.layers["raw"].toarray()
    b = result.layers["raw"].toarray()
    np.testing.assert_allclose(b.sum(axis=1), a.sum(axis=1), rtol=0.02)
    # No scratch file outlives the run.
    assert not list(tmp_path.rglob(".MSI_*.ibd"))


def test_scratch_is_removed_when_the_run_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after centroiding still deletes the scratch IBDs."""
    render_dataset(tmp_path, as_profile=True)

    def fail(*args: Any, **kwargs: Any) -> None:
        """Stop the run after the samples are loaded."""
        scratch = list(tmp_path.rglob(".MSI_*.ibd"))
        assert scratch
        raise RuntimeError("stop")

    monkeypatch.setattr(workflow, "select_references", fail)
    with pytest.raises(RuntimeError, match="stop"):
        process(tmp_path)
    assert not list(tmp_path.rglob(".MSI_*.ibd"))
    assert not list(tmp_path.rglob(".*.tmp.*"))
