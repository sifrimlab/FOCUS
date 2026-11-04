"""Spectral denoising, offset, normalisation and quantisation."""

import numpy as np

from focus.preprocessing.raman import spectra
from focus.preprocessing.raman.parameters import RamanRunSettings


def _settings(**kwargs: object) -> RamanRunSettings:
    values: dict[str, object] = {
        "denoise": True,
        "remove_offset": False,
        "normalization": "none",
    }
    values.update(kwargs)
    return RamanRunSettings(**values)  # type: ignore[arg-type]


class TestSpectra:
    """Spectra keep what is real at the cellular scale and their units."""

    def test_rank_matches_the_number_of_components(self) -> None:
        """Three spectral components plus noise give rank 3."""
        rng = np.random.default_rng(0)
        basis = rng.random((3, 20))
        scores = rng.random((50_000, 3)) * [10.0, 3.0, 1.0]
        data = scores @ basis + 5 + rng.normal(0, 0.05, (50_000, 20))
        model = spectra.fit(data)
        assert model.rank == 3

    def test_pure_noise_keeps_the_mean_and_one_component(self) -> None:
        """At least one component is kept, so a spectrum is never reduced to the mean alone."""
        rng = np.random.default_rng(1)
        model = spectra.fit(rng.normal(10, 1, (20_000, 13)))
        assert model.rank == 1

    def test_projection_preserves_nan_and_quantises(self) -> None:
        """Unmeasured pixels stay NaN; stored values lie on the 1/4 count grid."""
        rng = np.random.default_rng(2)
        block = (rng.random((5, 4, 4)) * 50).astype(np.float32)
        block[:, 0, 0] = np.nan
        model = spectra.fit(block.reshape(5, -1).T)
        out = spectra.process_block(block, model, _settings())
        assert np.isnan(out[:, 0, 0]).all() and np.isfinite(out[:, 1:, :]).all()
        grid = out[np.isfinite(out)] * 4
        np.testing.assert_allclose(grid, np.rint(grid))

    def test_offset_and_area_normalisation(self) -> None:
        """The minimum becomes 0; area-normalised spectra average 1 over their channels."""
        block = np.array([[[2.0]], [[4.0]], [[6.0]]], dtype=np.float32)
        out = spectra.process_block(
            block, None, _settings(denoise=False, remove_offset=True)
        )
        np.testing.assert_allclose(out[:, 0, 0], [0.0, 2.0, 4.0])
        area = spectra.process_block(
            block,
            None,
            _settings(denoise=False, remove_offset=True, normalization="area"),
        )
        np.testing.assert_allclose(area[:, 0, 0], [0.0, 1.0, 2.0])

    def test_single_channel_is_only_quantised(self) -> None:
        """One-channel acquisitions are not denoised, offset or normalised."""
        block = np.array([[[3.07]]], dtype=np.float32)
        out = spectra.process_block(
            block, None, _settings(remove_offset=True, normalization="area")
        )
        assert out[0, 0, 0] == 3.0
