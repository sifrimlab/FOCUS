"""Microgrid behavioral scenarios."""

import numpy as np

from focus.preprocessing.msi.microgrid import detect_microgrid_spots
from focus.preprocessing.msi.tissue import spectra_context


class TestMicrogrid:
    """Behavioral contracts for microgrid."""

    def test_cells_are_found_against_the_background_ions(self) -> None:
        """Verify cells are found against the background ions."""
        rng = np.random.default_rng(0)
        coords = np.array(
            [(y, x) for y in range(60) for x in range(60)], dtype=np.int32
        )
        cells = {(10, 10), (30, 42), (50, 20)}
        mz_list, intensity_list = [], []
        for y, x in coords:
            mz = [400.1, 500.2, 600.3] + (
                [700.5, 750.6, 780.7] if (y, x) in cells else []
            )
            intensity = list(rng.lognormal(np.log(1e4), 0.1, 3)) + [5e4] * (len(mz) - 3)
            mz_list.append(np.array(mz))
            intensity_list.append(np.array(intensity))
        result = detect_microgrid_spots(
            mz_list, intensity_list, coords, spectra_context(mz_list, 10)
        )
        found = np.flatnonzero(result.mask)
        assert {tuple(int(v) for v in coords[i]) for i in found} == cells
