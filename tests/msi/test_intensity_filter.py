"""S/N filter behavioral scenarios: noise measured on the background, skipped without one."""

import numpy as np
import pandas as pd

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.feature_building import select_sample_features
from focus.preprocessing.msi.tissue import spectra_context

STRONG, WEAK, CHEMICAL, ANNOTATED = 500.0, 600.0, 700.0, 800.0


def half_tissue(
    n_spectra: int = 1600, background_noise: int = 300, seed: int = 0
) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """
    Every other spectrum is tissue. Tissue holds a strong ion, a weak ion at about S/N 6, and two
    recurrent ions at the noise level (one in the database); every spectrum holds noise centroids of
    about 50 counts, `background_noise` of them in a background spectrum.
    """
    rng = np.random.default_rng(seed)
    mz_list, intensity_list, tissue = [], [], []
    for k in range(n_spectra):
        is_tissue = k % 2 == 0
        n_noise = 300 if is_tissue else background_noise
        mz = [rng.uniform(300, 1200, n_noise)]
        intensity = [rng.exponential(50, n_noise)]
        if is_tissue:
            ions = np.array([STRONG, WEAK, CHEMICAL, ANNOTATED])
            mz.append(ions * (1 + rng.normal(0, 1.0, ions.size) * 1e-6))
            intensity.append(
                np.array(
                    [
                        1e5 * rng.lognormal(0, 0.2),
                        260 * rng.lognormal(0, 0.1),
                        rng.exponential(50),
                        rng.exponential(50),
                    ]
                )
            )
        else:
            matrix = np.array([450.0, 950.0])
            mz.append(matrix * (1 + rng.normal(0, 1.0, 2) * 1e-6))
            intensity.append(np.full(2, 5e3))
        values, order = np.concatenate(mz), None
        order = np.argsort(values)
        mz_list.append(values[order])
        intensity_list.append(np.concatenate(intensity)[order].astype(np.float32))
        tissue.append(is_tissue)
    return mz_list, intensity_list, np.array(tissue)


def select(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    foreground: np.ndarray,
    background_found: bool,
) -> np.ndarray:
    """Kept feature m/z of the sample, with ANNOTATED in the lipid database."""
    database = pd.DataFrame(
        {"db_name": ["PC test"], "ionized_mass": [ANNOTATED], "ion_mode": ["pos"]}
    )
    return select_sample_features(
        mz_list,
        intensity_list,
        foreground,
        background_found,
        spectra_context(mz_list, 10),
        10,
        database,
        MsiIonMode.POSITIVE,
    ).mz


def held(features: np.ndarray, mass: float) -> bool:
    """Whether a feature lies within 5 ppm of `mass`."""
    return bool(np.any(np.abs(features - mass) / mass * 1e6 <= 5))


class TestSnrFilter:
    """Behavioral contracts of the S/N filter."""

    def test_noise_level_ion_is_removed_and_weak_ions_kept(self) -> None:
        """A recurrent ion at the background noise level goes; S/N 6 and annotated ions stay."""
        mz_list, intensity_list, tissue = half_tissue()
        features = select(mz_list, intensity_list, tissue, True)
        assert held(features, STRONG)
        assert held(features, WEAK)
        assert held(features, ANNOTATED)
        assert not held(features, CHEMICAL)

    def test_without_a_background_every_feature_is_kept(self) -> None:
        """A section filling the raster: no independent noise estimate, so nothing is filtered."""
        mz_list, intensity_list, tissue = half_tissue()
        features = select(mz_list, intensity_list, tissue, False)
        for mass in (STRONG, WEAK, CHEMICAL, ANNOTATED):
            assert held(features, mass), mass

    def test_clean_background_without_noise_skips_the_filter(self) -> None:
        """A background holding only matrix ions gives no noise profile: nothing is filtered."""
        mz_list, intensity_list, tissue = half_tissue(background_noise=0)
        features = select(mz_list, intensity_list, tissue, True)
        for mass in (STRONG, WEAK, CHEMICAL, ANNOTATED):
            assert held(features, mass), mass

    def test_clean_tissue_keeps_its_rare_real_ions(self) -> None:
        """
        Very clean tissue: the few non-recurrent peaks of a spectrum are weak real ions, not noise.
        A per-spectrum MAD would measure them as noise; with no background nothing is filtered.
        """
        rng = np.random.default_rng(1)
        ions = 300 + np.arange(40) * 20.0
        mz_list, intensity_list = [], []
        for _ in range(1200):
            present = rng.random(ions.size) < 0.6
            rare = rng.uniform(300, 1100, 12)  # rare ions, below the recurrence test
            mz = np.concatenate([ions[present], rare])
            intensity = np.concatenate(
                [
                    rng.lognormal(np.log(400), 0.5, present.sum()),
                    rng.lognormal(np.log(800), 0.3, 12),
                ]
            )
            order = np.argsort(mz)
            mz_list.append(mz[order])
            intensity_list.append(intensity[order].astype(np.float32))
        features = select(
            mz_list, intensity_list, np.ones(len(mz_list), dtype=bool), False
        )
        assert all(held(features, mass) for mass in ions)
