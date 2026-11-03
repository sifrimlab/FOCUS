"""Backbone behavioral scenarios: per-sample features and their merge."""

import numpy as np

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.backbone import (
    SampleFeatures,
    _BackboneBuilder,
    merge_features,
    split_groups,
)
from focus.preprocessing.msi.binning import bin_spectrum
from focus.preprocessing.msi.feature_building import select_sample_features
from focus.preprocessing.msi.tissue import spectra_context
from tests.msi.data import (
    backbone_sample,
    KIND_IONS,
    KIND_PRESENCE,
    MATRIX,
    kind_sample,
)


def sample_features(
    mz_list: list[np.ndarray], intensity_list: list[np.ndarray] | None
) -> SampleFeatures:
    """The kept features of one sample whose every spectrum is foreground (no background, no S/N)."""
    if intensity_list is None:
        intensity_list = [np.full(mz.size, 1e5, dtype=np.float32) for mz in mz_list]
    return select_sample_features(
        mz_list,
        intensity_list,
        np.ones(len(mz_list), dtype=bool),
        False,
        spectra_context(mz_list, 10),
        10,
        None,
        MsiIonMode.POSITIVE,
    )


class TestBackbone:
    """Behavioral contracts for backbone."""

    @staticmethod
    def build(*samples: tuple[list[np.ndarray], list[np.ndarray] | None]) -> np.ndarray:
        """Build and return the positive-mode m/z backbone from the supplied sample spectra."""
        builder = _BackboneBuilder(mass_tolerance=10)
        for mz_list, intensity_list in samples:
            builder.add(MsiIonMode.POSITIVE, sample_features(mz_list, intensity_list))
        return builder.build()[MsiIonMode.POSITIVE].mz

    @staticmethod
    def found(
        features: np.ndarray, masses: np.ndarray, ppm: int = 10
    ) -> tuple[np.ndarray, int]:
        """Per mass, whether a feature lies within `ppm`; and the number of features near no mass."""
        if features.size == 0:
            return np.zeros(masses.size, dtype=bool), 0
        near = (
            np.abs(features[:, None] - masses[None, :]) / masses[None, :] * 1e6 <= ppm
        )
        return near.any(axis=0), int((~near.any(axis=1)).sum())

    @staticmethod
    def near(features: np.ndarray, mass: float, ppm: int = 50) -> np.ndarray:
        """Return supplied reference masses that fall within ppm of the requested mass."""
        return features[np.abs(features - mass) / mass * 1e6 <= ppm]

    def test_broad_skewed_ion_gives_one_feature(self) -> None:
        # 15% of the peaks scatter 4 to 22 ppm above the ion: a window cut at the tolerance used to
        # turn that tail into a second feature that never co-occurs with the first.
        """Verify broad skewed ion gives one feature."""
        mz_list, intensity_list = backbone_sample(
            [(700.0, 1.0, 2.0, (0.15, 4.0, 22.0)), (500.0, 1.0, 1.0, None)]
        )
        backbone = _BackboneBuilder(10)
        backbone.add(MsiIonMode.POSITIVE, sample_features(mz_list, intensity_list))
        built = backbone.build()[MsiIonMode.POSITIVE]
        ion = self.near(built.mz, 700.0)
        assert ion.size == 1
        assert abs(ion[0] - 700.0) / 700.0 * 1e6 < 3.0
        # The ion's peaks bin into that one feature, nearly every spectrum holding it.
        binned = np.array(
            [
                bin_spectrum(mz, i, built.mz, built.sigma, 10)
                for mz, i in zip(mz_list, intensity_list)
            ]
        )
        column = int(np.flatnonzero(np.isin(built.mz, ion))[0])
        assert (binned[:, column] > 0).mean() > 0.8

    def test_ions_separated_by_a_dip_stay_two_features(self) -> None:
        """Verify ions separated by a dip stay two features."""
        features = self.build(
            backbone_sample(
                [(700.0, 1.0, 2.0, None), (700.0 * (1 + 30e-6), 1.0, 2.0, None)]
            )
        )
        assert self.near(features, 700.0, ppm=60).size == 2

    def test_noise_peaks_make_no_feature(self) -> None:
        """Verify noise peaks make no feature."""
        masses = (412.3, 538.2, 671.5, 803.6, 944.7)
        features = self.build(
            backbone_sample(
                [(mass, 1.0, 1.0, None) for mass in masses],
                n_spectra=3000,
                n_noise=3000,
            )
        )
        assert features.size == len(masses)
        for mass in masses:
            assert self.near(features, mass, ppm=3).size == 1

    def test_every_recurring_ion_is_a_feature_whatever_its_intensity(self) -> None:
        # High-quality tissue: no noise, ion intensities spread over two orders of magnitude.
        """Verify every recurring ion is a feature whatever its intensity."""
        mz_list, intensity_list, _ = kind_sample("tissue")
        found, false = self.found(self.build((mz_list, intensity_list)), KIND_IONS)
        assert found[KIND_PRESENCE >= 0.05].all()
        assert false == 0

    def test_noise_makes_no_feature_in_any_kind_of_sample(self) -> None:
        # The foreground (cell) spectra feed the backbone; pure noise gives nothing.
        """Verify noise makes no feature in any kind of sample."""
        for kind, n_spectra, minimum in (
            ("culture", 1500, 0.05),
            ("mixed", 1500, 0.05),
            ("microgrid", 6000, 0.3),
        ):
            mz_list, intensity_list, holds = kind_sample(kind, n_spectra)
            spectra = [mz for mz, cell in zip(mz_list, holds) if cell]
            found, false = self.found(
                self.build((spectra, None)), np.concatenate([KIND_IONS, MATRIX])
            )
            assert found[: KIND_IONS.size][KIND_PRESENCE >= minimum].all(), kind
            assert false == 0, kind
        rng = np.random.default_rng(3)
        assert (
            self.build(
                ([np.sort(rng.uniform(300, 1200, 1000)) for _ in range(1500)], None)
            ).size
            == 0
        )

    def test_frequency_is_judged_per_sample(self) -> None:
        """Verify frequency is judged per sample."""
        common, rare, local = 500.0, 700.0, 900.0
        first = backbone_sample(
            [
                (common, 1.0, 1.0, None),
                (rare, 0.005, 1.0, None),
                (local, 0.005, 1.0, None),
            ],
            seed=1,
        )
        second = backbone_sample(
            [(common, 1.0, 1.0, None), (rare, 0.5, 1.0, None)], seed=2
        )
        features = self.build(first, second)
        assert self.near(features, common).size == 1
        assert self.near(features, rare).size == 1  # frequent in the second sample
        assert (
            self.near(features, local).size == 0
        )  # rare in the only sample holding it

    def test_weak_feature_on_a_strong_flank_is_preserved(self) -> None:
        """A weak feature just beyond the tolerance of a strong one stays a feature of its own."""
        strong = SampleFeatures(
            np.array([700.0]), np.array([1.0]), np.array([1.0]), np.array([False])
        )
        weak = SampleFeatures(
            np.array([700.0 * (1 + 11e-6)]),
            np.array([0.02]),
            np.array([1.0]),
            np.array([False]),
        )
        merged = merge_features([strong, weak], 10)
        assert merged.mz.size == 2
        np.testing.assert_allclose(merged.mz, [700.0, 700.0 * (1 + 11e-6)])

    def test_features_closer_than_the_tolerance_merge(self) -> None:
        """One ion seen 4 ppm apart in two samples is one feature at the presence-weighted mean."""
        first = SampleFeatures(
            np.array([700.0]), np.array([0.75]), np.array([1.0]), np.array([True])
        )
        second = SampleFeatures(
            np.array([700.0 * (1 + 4e-6)]),
            np.array([0.25]),
            np.array([1.0]),
            np.array([False]),
        )
        merged = merge_features([first, second], 10)
        assert merged.mz.size == 1
        np.testing.assert_allclose(merged.mz, 700.0 * (1 + 1e-6), rtol=1e-9)
        assert merged.annotated[0]
        # Width: the members' own spread plus their offsets from the merged m/z.
        np.testing.assert_allclose(
            merged.sigma, np.sqrt(0.75 * (1 + 1) + 0.25 * (1 + 9)), rtol=1e-3
        )

    def test_every_group_spans_at_most_the_tolerance(self) -> None:
        """Largest-gap splitting assigns every feature to one group of span <= tolerance."""
        rng = np.random.default_rng(0)
        mz = np.sort(700.0 * (1 + rng.uniform(0, 200, 400) * 1e-6))
        groups = split_groups(mz, 10)
        assert sum(stop - start for start, stop in groups) == mz.size
        assert all(
            (mz[stop - 1] - mz[start]) / mz[start] * 1e6 <= 10 for start, stop in groups
        )
        assert [g[0] for g in groups][1:] == [g[1] for g in groups][:-1]

    def test_feature_width_follows_the_peak_spread(self) -> None:
        """The data-driven width recovers the ppm scatter of an ion's peaks."""
        for spread in (2.0, 3.0):
            mz_list, intensity_list = backbone_sample(
                [(700.0, 1.0, spread, None)], n_spectra=4000
            )
            features = sample_features(mz_list, intensity_list)
            assert features.mz.size == 1
            assert abs(features.sigma[0] - spread) / spread < 0.2, spread
