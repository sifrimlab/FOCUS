"""DualPolarityPairing behavioral scenarios."""

import os
import tempfile

import numpy as np
import pytest

from focus.preprocessing.msi.pairing import PolarityPairingError
from focus.preprocessing.msi.sample import MsiSample
from tests.msi.data import StructuredTissue, tissue_position, write_imzml

PITCH = 20.0
ROWS, COLS = 50, 60


def grid(rows: range, cols: range) -> np.ndarray:
    """Pixel (line, column) of every spectrum of a raster, in scan order."""
    return np.array([(r, c) for r in rows for c in cols])


def rotation(degrees: float) -> np.ndarray:
    """Counter-clockwise rotation matrix."""
    a = np.deg2rad(degrees)
    return np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])


class TestDualPolarityPairing:
    """Both polarities image one StructuredTissue; POSITIVE stage positions are the tissue frame."""

    @staticmethod
    def write(
        root: str,
        tissue: StructuredTissue,
        mode: str,
        pixels: np.ndarray,
        stage: np.ndarray,
        tissue_xy: np.ndarray,
        with_stage: bool = True,
    ) -> None:
        """Write one polarity: spectra of ``tissue`` at ``tissue_xy``, recorded at ``stage``."""
        mz_list, intensity_list = tissue.spectra(tissue_xy, mode)
        write_imzml(
            os.path.join(root, "S", "MSI", mode),
            mz_list,
            intensity_list,
            pixels,
            pixel_size=int(PITCH),
            stage=[tuple(p) for p in stage] if with_stage else None,
        )

    def positive(
        self, root: str, tissue: StructuredTissue, with_stage: bool = True
    ) -> np.ndarray:
        """Write the POSITIVE raster and return its tissue positions."""
        pixels = grid(range(ROWS), range(COLS))
        xy = pixels[:, ::-1] * PITCH
        self.write(root, tissue, "pos", pixels, xy, xy, with_stage)
        return xy

    @staticmethod
    def paired_positions(root: str) -> dict[str, np.ndarray]:
        """Tissue position carried by every paired spectrum, per polarity, in pair order."""
        sample = MsiSample(root, "S", "MSI", double_ion_mode=True)
        sample.initialize_sample()
        mz, _ = sample.load_payload()
        return {mode: np.array([tissue_position(m) for m in mz[mode]]) for mode in mz}

    def test_already_paired_acquisitions_pair_exactly(self) -> None:
        """Shared stage frame, NEG 5 um away, 8 more rows and reversed pixel axes: every POS pairs."""
        with tempfile.TemporaryDirectory() as root:
            tissue = StructuredTissue((COLS * PITCH, ROWS * PITCH))
            self.positive(root, tissue)
            pixels = grid(range(-8, ROWS), range(COLS))
            stage = pixels[:, ::-1] * PITCH + 5.0
            reversed_pixels = np.column_stack(
                [ROWS - 1 - pixels[:, 0], COLS - 1 - pixels[:, 1]]
            )
            self.write(root, tissue, "neg", reversed_pixels, stage, stage)
            located = self.paired_positions(root)
            assert len(located["pos"]) == ROWS * COLS
            assert np.abs(located["pos"] - located["neg"]).max() <= 5.0 + 1e-6

    def test_shifted_stage_frame_pairs_by_content(self) -> None:
        """Same raster and tissue pixels, NEG stage frame shifted by whole pixels (a reloaded slide)."""
        with tempfile.TemporaryDirectory() as root:
            tissue = StructuredTissue((COLS * PITCH, ROWS * PITCH))
            xy = self.positive(root, tissue)
            self.write(
                root,
                tissue,
                "neg",
                grid(range(ROWS), range(COLS)),
                xy + [340.0, -60.0],
                xy,
            )
            located = self.paired_positions(root)
            assert len(located["pos"]) == ROWS * COLS
            np.testing.assert_allclose(located["pos"], located["neg"], atol=1e-6)

    def test_rotated_partial_scan_pairs_nearest_spectra(self) -> None:
        """NEG scans an axis-aligned raster over 60% of a tissue rotated by 6 degrees and shifted."""
        with tempfile.TemporaryDirectory() as root:
            tissue = StructuredTissue((COLS * PITCH, ROWS * PITCH))
            self.positive(root, tissue)
            pixels = grid(range(ROWS), range(24, COLS + 10))
            stage = pixels[:, ::-1] * PITCH + [900.0, 400.0]
            center = np.array([COLS, ROWS]) * PITCH / 2
            tissue_xy = (stage - [900.0, 400.0] - center) @ rotation(6.0) + center
            self.write(root, tissue, "neg", pixels, stage, tissue_xy)
            located = self.paired_positions(root)
            distance = np.linalg.norm(located["pos"] - located["neg"], axis=1)
            assert len(located["pos"]) > 0.5 * ROWS * COLS
            assert np.median(distance) < 0.5 * PITCH
            assert distance.max() < PITCH

    def test_without_stage_positions_mirrored_axes_pair_exactly(self) -> None:
        """No stage positions and NEG pixel axes reversed: the mirrored pose is found."""
        with tempfile.TemporaryDirectory() as root:
            tissue = StructuredTissue((COLS * PITCH, ROWS * PITCH))
            xy = self.positive(root, tissue, with_stage=False)
            pixels = grid(range(ROWS), range(COLS))
            reversed_pixels = np.column_stack([pixels[:, 0], COLS - 1 - pixels[:, 1]])
            self.write(root, tissue, "neg", reversed_pixels, xy, xy, with_stage=False)
            located = self.paired_positions(root)
            assert len(located["pos"]) == ROWS * COLS
            np.testing.assert_allclose(located["pos"], located["neg"], atol=1e-6)

    def test_unrelated_acquisitions_stop_the_sample(self) -> None:
        """POS and NEG of different tissues cannot be registered: the sample stops, never mispairs."""
        with tempfile.TemporaryDirectory() as root:
            xy = self.positive(
                root, StructuredTissue((COLS * PITCH, ROWS * PITCH), seed=0)
            )
            other = StructuredTissue((COLS * PITCH, ROWS * PITCH), seed=7)
            self.write(root, other, "neg", grid(range(ROWS), range(COLS)), xy, xy)
            with pytest.raises(PolarityPairingError, match="could not be registered"):
                self.paired_positions(root)
