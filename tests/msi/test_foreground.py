"""Foreground detection on recalibrated spectra, one scenario per sample type."""

import numpy as np

from focus.config.options import MsiSampleType
from focus.preprocessing.msi.microgrid import detect_microgrid_spots
from focus.preprocessing.msi.technology import sample_profile
from focus.preprocessing.msi.tissue import detect_foreground, spectra_context
from tests.msi.data import CALIBRANTS, KIND_IONS, MATRIX

SIDE = 40


def raster(
    is_tissue: np.ndarray, tissue_noise: int, background_noise: int, seed: int = 0
) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """
    Spectra on a SIDE x SIDE raster: tissue pixels hold the KIND ions and the matrix, background
    pixels the matrix only; every pixel holds the given number of noise centroids (about 50 counts).
    Tissue ion intensity rises along x, a gradient.
    """
    rng = np.random.default_rng(seed)
    coords = np.array(
        [(y, x) for y in range(SIDE) for x in range(SIDE)], dtype=np.int32
    )
    mz_list, intensity_list = [], []
    for (y, x), tissue in zip(coords, is_tissue.ravel()):
        n_noise = tissue_noise if tissue else background_noise
        parts = [
            rng.uniform(300, 1200, n_noise),
            MATRIX * (1 + rng.normal(0, 1, 3) * 1e-6),
        ]
        values = [rng.exponential(50, n_noise), rng.lognormal(np.log(3000), 0.2, 3)]
        if tissue:
            present = rng.random(KIND_IONS.size) < 0.6
            parts.append(
                KIND_IONS[present] * (1 + rng.normal(0, 1, present.sum()) * 1e-6)
            )
            values.append(1000 * (1 + x / SIDE) * rng.lognormal(0, 0.3, present.sum()))
        mz, intensity = np.concatenate(parts), np.concatenate(values)
        order = np.argsort(mz)
        mz_list.append(mz[order])
        intensity_list.append(intensity[order].astype(np.float32))
    return mz_list, intensity_list, coords


def detect(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    coords: np.ndarray,
    sample_type: str,
) -> tuple[np.ndarray, bool]:
    """Foreground mask on the raster and whether a background was separated."""
    result = detect_foreground(
        mz_list,
        intensity_list,
        coords,
        spectra_context(mz_list, 10),
        sample_profile(sample_type),
    )
    return result.mask.reshape(SIDE, SIDE), result.background


class TestForeground:
    """Behavioral contracts of foreground detection."""

    def test_tissue_without_background_keeps_every_spectrum(self) -> None:
        """A section covering the raster on a clean background: one score mode, all foreground."""
        mz_list, intensity_list, coords = raster(np.ones((SIDE, SIDE), bool), 0, 0)
        mask, background = detect(mz_list, intensity_list, coords, MsiSampleType.TISSUE)
        assert mask.all()
        assert not background

    def test_noisy_background_is_separated_from_tissue(self) -> None:
        """Background spectra hold more peaks than tissue (noise), yet score below it."""
        y, x = np.mgrid[:SIDE, :SIDE]
        disc = (y - 20) ** 2 + (x - 20) ** 2 <= 12**2
        mz_list, intensity_list, coords = raster(disc, 100, 1500)
        sizes = np.array([mz.size for mz in mz_list]).reshape(SIDE, SIDE)
        assert sizes[~disc].mean() > sizes[disc].mean()
        mask, background = detect(
            mz_list, intensity_list, coords, MsiSampleType.TISSUE_NOISY
        )
        assert background
        assert (mask & disc).sum() / (mask | disc).sum() > 0.97

    def test_culture_keeps_gaps_that_a_section_fills(self) -> None:
        """A gap inside a culture stays background; the section preset fills enclosed holes."""
        culture = np.ones((SIDE, SIDE), bool)
        culture[15:21, 15:21] = False  # a gap between cells
        culture[5, 30] = False  # single-pixel gaps are noise of the classification
        culture[30, 8] = False
        mz_list, intensity_list, coords = raster(culture, 50, 50)
        mask, background = detect(
            mz_list, intensity_list, coords, MsiSampleType.CELL_CULTURE
        )
        assert background
        assert not mask[15:21, 15:21][1:-1, 1:-1].any()
        assert mask[5, 30] and mask[30, 8]
        section, _ = detect(mz_list, intensity_list, coords, MsiSampleType.TISSUE)
        assert section[15:21, 15:21].all()

    def test_microgrid_cells_are_single_pixels_in_matrix(self) -> None:
        """One-pixel cells among background ions present on the whole slide."""
        rng = np.random.default_rng(0)
        side = 60
        coords = np.array([(y, x) for y in range(side) for x in range(side)], np.int32)
        cells = {(10, 10), (30, 42), (50, 20), (45, 50)}
        mz_list, intensity_list = [], []
        for y, x in coords:
            mz = list(CALIBRANTS * (1 + rng.normal(0, 1, CALIBRANTS.size) * 1e-6))
            intensity = list(rng.lognormal(np.log(1e4), 0.1, CALIBRANTS.size))
            if (y, x) in cells:
                mz += [705.5, 750.6, 780.7]
                intensity += [5e4] * 3
            order = np.argsort(mz)
            mz_list.append(np.array(mz)[order])
            intensity_list.append(np.array(intensity)[order])
        result = detect_microgrid_spots(
            mz_list, intensity_list, coords, spectra_context(mz_list, 10)
        )
        found = {tuple(int(v) for v in coords[i]) for i in np.flatnonzero(result.mask)}
        assert found == cells
        assert result.background

    def test_tissue_regions_with_fewer_ions_are_not_background(self) -> None:
        """Two tissue regions differing in their number of ions are both foreground."""
        rng = np.random.default_rng(2)
        coords = np.array([(y, x) for y in range(SIDE) for x in range(SIDE)], np.int32)
        mz_list, intensity_list = [], []
        for y, x in coords:
            share = 0.75 if x < SIDE // 2 else 0.55  # e.g. grey and white matter
            present = rng.random(KIND_IONS.size) < share
            mz = KIND_IONS[present] * (1 + rng.normal(0, 1, present.sum()) * 1e-6)
            mz_list.append(mz)
            intensity_list.append(rng.lognormal(np.log(1e3), 0.3, mz.size))
        mask, background = detect(mz_list, intensity_list, coords, MsiSampleType.TISSUE)
        assert mask.all()
        assert not background
