"""DualPolarityPairing behavioral scenarios."""

import os
import tempfile

import numpy as np

from focus.preprocessing.msi.sample import MsiSample
from tests.msi.data import write_imzml


class TestDualPolarityPairing:
    """NEG covers 8 more rows than POS, sits 5 um away and runs its pixel axes in reverse, as on a
    real dual-polarity acquisition. Every spectrum carries its true row and column as two m/z values."""

    @staticmethod
    def write(root: str, with_stage: bool) -> None:
        """Write paired positive/negative acquisitions with the controlled coordinate convention."""
        neg_rows = range(-8, 20) if with_stage else range(0, 20)
        for mode, rows, reverse, offset in (
            ("pos", range(0, 20), False, 0.0),
            ("neg", neg_rows, with_stage, 5.0),
        ):
            mz_list, intensity_list, coords, stage = [], [], [], []
            for r in rows:
                for c in range(25):
                    mz_list.append(np.array([300.0 + r + 10.0, 700.0 + c]))
                    intensity_list.append(np.array([1e4, 1e4], dtype=np.float32))
                    coords.append((19 - r, 24 - c) if reverse else (r, c))
                    stage.append((1000.0 + 20 * c + offset, 2000.0 + 20 * r + offset))
            write_imzml(
                os.path.join(root, "S", "MSI", mode),
                mz_list,
                intensity_list,
                np.array(coords),
                stage=stage if with_stage else None,
            )

    @staticmethod
    def decoded(root: str) -> dict[str, np.ndarray]:
        """Load paired MSI payloads and decode the embedded pixel identifiers from each polarity."""
        sample = MsiSample(root, "S", "MSI", double_ion_mode=True)
        sample.initialize_sample()
        mz, _ = sample.load_payload()
        return {
            mode: np.array(
                [(round(m[0] - 310.0), round(m[1] - 700.0)) for m in mz[mode]]
            )
            for mode in mz
        }

    def test_spectra_are_paired_by_stage_position(self) -> None:
        """Verify spectra are paired by stage position."""
        with tempfile.TemporaryDirectory() as root:
            self.write(root, with_stage=True)
            located = self.decoded(root)
            assert len(located["pos"]) == 20 * 25
            np.testing.assert_array_equal(located["pos"], located["neg"])

    def test_without_stage_positions_equal_rasters_pair_by_pixel(self) -> None:
        """Verify without stage positions equal rasters pair by pixel."""
        with tempfile.TemporaryDirectory() as root:
            self.write(root, with_stage=False)
            located = self.decoded(root)
            np.testing.assert_array_equal(located["pos"], located["neg"])
