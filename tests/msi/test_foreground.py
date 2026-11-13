"""Foreground detection on recalibrated spectra, one scenario per sample type."""

import numpy as np
import pandas as pd

from focus.config.options import MsiSampleType
from focus.preprocessing.msi.foreground import _lipid_score
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


# --- Matrix-covered glass and the lipid fraction ---------------------------------------------

_MATRIX_RNG = np.random.default_rng(11)
MATRIX_CLUSTERS = np.sort(_MATRIX_RNG.uniform(300, 1200, 400))
# Keep clusters clear of the tissue ions (more than 50 ppm apart) and of each other.
MATRIX_CLUSTERS = MATRIX_CLUSTERS[
    np.min(np.abs(MATRIX_CLUSTERS[:, None] / KIND_IONS[None, :] - 1), axis=1) > 50e-6
]
MATRIX_CLUSTERS = MATRIX_CLUSTERS[
    np.concatenate(([True], np.diff(MATRIX_CLUSTERS) / MATRIX_CLUSTERS[1:] > 50e-6))
][:200]


def glass_raster(
    is_tissue: np.ndarray, lipid_level: np.ndarray | None = None, seed: int = 3
) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """
    Matrix-covered glass: every pixel holds recurring matrix clusters; tissue pixels also hold the
    KIND ions (the lipids, scaled by `lipid_level`) and fewer clusters (suppression). Glass and
    tissue hold about as many recurrent ions.
    """
    rng = np.random.default_rng(seed)
    coords = np.array([(y, x) for y in range(SIDE) for x in range(SIDE)], np.int32)
    level = np.ones((SIDE, SIDE)) if lipid_level is None else lipid_level
    mz_list, intensity_list = [], []
    for (y, x), tissue in zip(coords, is_tissue.ravel()):
        share = 0.35 if tissue else 0.75
        present = rng.random(MATRIX_CLUSTERS.size) < share
        parts = [
            MATRIX_CLUSTERS[present] * (1 + rng.normal(0, 1, present.sum()) * 1e-6)
        ]
        values = [rng.lognormal(np.log(800), 0.3, present.sum())]
        if tissue:
            lipids = rng.random(KIND_IONS.size) < 0.6
            parts.append(
                KIND_IONS[lipids] * (1 + rng.normal(0, 1, lipids.sum()) * 1e-6)
            )
            values.append(level[y, x] * rng.lognormal(np.log(2000), 0.3, lipids.sum()))
        mz, intensity = np.concatenate(parts), np.concatenate(values)
        order = np.argsort(mz)
        mz_list.append(mz[order])
        intensity_list.append(intensity[order].astype(np.float32))
    return mz_list, intensity_list, coords


def lipid_database(masses: np.ndarray) -> pd.DataFrame:
    """A database whose positive-mode entries are `masses`."""
    return pd.DataFrame(
        {
            "db_name": [f"L{i}" for i in range(masses.size)],
            "ionized_mass": masses,
            "ion_mode": "pos",
        }
    )


def detect_with_lipids(
    data: tuple[list[np.ndarray], list[np.ndarray], np.ndarray],
    database: pd.DataFrame | None,
    sample_type: str = MsiSampleType.TISSUE,
) -> tuple[np.ndarray, bool, str]:
    """Foreground with the lipid score of `database` (None: recurrent ions only)."""
    mz_list, intensity_list, coords = data
    context = spectra_context(mz_list, 10)
    score = _lipid_score(mz_list, intensity_list, context, database, "pos", 10)
    result = detect_foreground(
        mz_list, intensity_list, coords, context, sample_profile(sample_type), score
    )
    return result.mask.reshape(SIDE, SIDE), result.background, result.summary


class TestLipidFraction:
    """Matrix-covered glass holds as many recurrent ions as tissue but few lipids."""

    def setup_method(self) -> None:
        """A tissue disc on matrix-covered glass."""
        y, x = np.mgrid[:SIDE, :SIDE]
        self.disc = (y - 20) ** 2 + (x - 20) ** 2 <= 12**2
        self.data = glass_raster(self.disc)

    def test_recurrent_ions_alone_keep_the_glass(self) -> None:
        """The failure being fixed: the ion score does not separate matrix-covered glass."""
        mask, background, _ = detect_with_lipids(self.data, None)
        assert mask.all()
        assert not background

    def test_lipid_fraction_separates_the_glass(self) -> None:
        """With a lipid database the glass becomes background and the disc stays foreground."""
        mask, background, summary = detect_with_lipids(
            self.data, lipid_database(KIND_IONS)
        )
        assert background
        assert "lipid fraction" in summary
        assert mask[self.disc].mean() > 0.97
        assert not mask[~self.disc].any()

    def test_tissue_regions_differing_in_lipids_are_not_background(self) -> None:
        """Two tissue regions with different lipid content both stay foreground."""
        level = np.where(np.mgrid[:SIDE, :SIDE][1] < SIDE // 2, 1.0, 0.6)
        data = glass_raster(np.ones((SIDE, SIDE), bool), level)
        mask, background, _ = detect_with_lipids(data, lipid_database(KIND_IONS))
        assert mask.all()
        assert not background

    def test_annotation_at_chance_is_not_used(self) -> None:
        """A database matching only noise-level numbers of ions gives no lipid score."""
        mz_list, intensity_list, _ = self.data
        context = spectra_context(mz_list, 10)
        few = lipid_database(KIND_IONS[:5])
        assert _lipid_score(mz_list, intensity_list, context, few, "pos", 10) is None
        assert _lipid_score(mz_list, intensity_list, context, None, "pos", 10) is None
        dense = lipid_database(np.exp(np.arange(np.log(300), np.log(1200), 15e-6)))
        assert _lipid_score(mz_list, intensity_list, context, dense, "pos", 10) is None
