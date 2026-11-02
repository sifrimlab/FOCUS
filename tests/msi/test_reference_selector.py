"""ReferenceSelector behavioral scenarios."""

import numpy as np

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.backbone import mass_scale_shifts
from focus.preprocessing.msi.references import _CalibrationReferenceSelector
from tests.msi.data import (
    CALIBRANTS,
    KIND_IONS,
    KIND_PRESENCE,
    kind_sample,
    selector_sample,
    dense_selector_sample,
)


class TestReferenceSelector:
    """Behavioral contracts for reference selector."""

    def select(
        self, number_of_references: int, required: dict[str, np.ndarray] | None = None
    ) -> np.ndarray:
        """Select the requested calibrant count from the synthetic positive-mode spectra, including required ions."""
        mz_list, intensity_list = selector_sample()
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample(
            {MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list}
        )
        return selector.select(number_of_references, required_mz=required)[
            MsiIonMode.POSITIVE
        ]

    @staticmethod
    def near(references: np.ndarray, mass: float, ppm: int = 100) -> np.ndarray:
        """Return supplied reference masses that fall within ppm of the requested mass."""
        return references[np.abs(references - mass) / mass * 1e6 <= ppm]

    def test_dense_noise_selects_every_calibrant_without_bias(self) -> None:
        """Verify dense noise selects every calibrant without bias."""
        mz_list, intensity_list = dense_selector_sample()
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample(
            {MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list}
        )
        references = selector.select(10)[MsiIonMode.POSITIVE]
        assert references.size == CALIBRANTS.size
        offsets = (references - CALIBRANTS) / CALIBRANTS * 1e6
        # Every reference is a calibrant at the dataset's mean drift (15 ppm), with no extra bias.
        np.testing.assert_allclose(offsets, 15.0, atol=1.0)

    def test_keeps_the_ion_that_always_wins_its_window(self) -> None:
        # 700.0 is always twice as intense as its neighbour 20 ppm away: matching always takes it, so it
        # can be a reference; the neighbour cannot.
        """Verify keeps the ion that always wins its window."""
        references = self.select(4)
        assert self.near(references, 700.0 * (1 + 20e-6), ppm=5).size == 0
        assert np.all(np.abs(self.near(references, 700.0) - 700.0) / 700.0 * 1e6 < 3)

    def test_rejects_ions_that_take_turns_in_their_window(self) -> None:
        # Two ions 20 ppm apart, each the more intense in about half of the spectra: matching would
        # alternate between them, so neither is a reference.
        """Verify rejects ions that take turns in their window."""
        rng = np.random.default_rng(5)
        mz_list, intensity_list = [], []
        for _ in range(400):
            mz = np.array([400.1, 500.0, 500.0 * (1 + 20e-6), 700.2, 900.3])
            intensity = np.concatenate(
                [[1e5], 1e5 * rng.lognormal(0, 1.0, 2), [1e5, 1e5]]
            )
            mz_list.append(mz)
            intensity_list.append(intensity.astype(np.float32))
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample(
            {MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list}
        )
        references = selector.select(4)[MsiIonMode.POSITIVE]
        assert self.near(references, 500.0, ppm=50).size == 0
        assert references.size == 3

    def test_smeared_calibrant_gives_one_reference_at_its_centre(self) -> None:
        """Verify smeared calibrant gives one reference at its centre."""
        references = self.select(6)
        smeared = self.near(references, 600.0)
        assert smeared.size == 1
        assert abs(smeared[0] - 600.0) / 600.0 * 1e6 < 2.0

    def test_references_spread_over_the_mass_range(self) -> None:
        # Two references: one in each half of the candidates' log m/z span (400.10 to 900.50).
        """Verify references spread over the mass range."""
        references = self.select(2)
        middle = np.sqrt(400.10 * 900.50)
        assert int((references < middle).sum()) == 1
        assert int((references > middle).sum()) == 1

    def test_dense_spectra_give_references_on_their_own_ions(self) -> None:
        # 3,000 ions over m/z 300 to 1,200, many with a neighbour 12 to 20 ppm away (inside the search
        # window, closer than one tolerance): each reference must sit on its own ion, at the sample's
        # mean drift (+2 ppm), not between it and a neighbour.
        """Verify dense spectra give references on their own ions."""
        rng = np.random.default_rng(11)
        ions = np.sort(np.exp(rng.uniform(np.log(300), np.log(1200), 3200)))
        ions = ions[np.concatenate(([True], np.diff(ions) / ions[1:] * 1e6 > 12))]
        presence, level = (
            rng.beta(0.6, 1.2, ions.size),
            rng.lognormal(0, 1.5, ions.size),
        )
        mz_list, intensity_list = [], []
        for k in range(600):
            present = rng.random(ions.size) < presence
            drift = 4.0 * k / 599 + rng.normal(0, 1.0, present.sum())
            mz_list.append(ions[present] * (1 + drift * 1e-6))
            intensity_list.append(
                (1000 * level[present] * rng.lognormal(0, 0.4, present.sum())).astype(
                    np.float32
                )
            )
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample(
            {MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list}
        )
        references = selector.select(10)[MsiIonMode.POSITIVE]
        assert references.size == 10
        nearest = np.abs(references[:, None] - ions[None, :]).argmin(axis=1)
        np.testing.assert_allclose(
            (references - ions[nearest]) / ions[nearest] * 1e6, 2.0, atol=0.5
        )
        assert presence[nearest].min() > 0.8

    def test_clean_spectra_give_the_most_present_ions(self) -> None:
        # No noise: every ion counts, so the references are the ions most spectra hold, not only the
        # most intense ones.
        """Verify clean spectra give the most present ions."""
        mz_list, intensity_list, _ = kind_sample("tissue")
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample(
            {MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list}
        )
        references = selector.select(10)[MsiIonMode.POSITIVE]
        assert references.size == 10
        nearest = np.abs(references[:, None] - KIND_IONS[None, :]).argmin(axis=1)
        assert KIND_PRESENCE[nearest].min() > 0.5

    def test_required_mass_is_always_selected(self) -> None:
        """Verify required mass is always selected."""
        references = self.select(2, required={MsiIonMode.POSITIVE: np.array([700.0])})
        assert self.near(references, 700.0, ppm=30).size == 1

    def test_mass_scale_shifts_put_samples_on_one_scale(self) -> None:
        # Three samples share the calibrants at -6, 0 and +12 ppm, each with ions of its own; a fourth
        # shares nothing.
        """Verify mass scale shifts put samples on one scale."""

        def references(offset: float, own: list[float]) -> np.ndarray:
            """Return sorted calibrants shifted by offset ppm, together with the sample-specific masses."""
            return np.sort(np.concatenate([CALIBRANTS[:6] * (1 + offset * 1e-6), own]))

        samples = [
            references(-6.0, [1200.0]),
            references(0.0, [1300.0, 1400.0]),
            references(12.0, []),
            np.array([1500.0, 1600.0, 1700.0]),
        ]
        shifts, connected = mass_scale_shifts(samples, 50.0)
        np.testing.assert_allclose(
            shifts[:3], [-8.0, -2.0, 10.0], atol=0.01
        )  # offsets about their mean (+2)
        assert shifts[3] == 0.0
        np.testing.assert_array_equal(connected, [True, True, True, False])

        # A sample already on the common scale anchors the others.
        shifts, _ = mass_scale_shifts(
            samples[:3], 50.0, fixed=np.array([False, True, False])
        )
        np.testing.assert_allclose(shifts, [-6.0, 0.0, 12.0], atol=0.01)
