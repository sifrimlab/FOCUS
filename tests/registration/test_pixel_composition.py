"""Pixel composition registration: unmixing, pixel clusters and the spot composition contract."""

from itertools import permutations
import json
import os
from pathlib import Path
from unittest.mock import patch

import anndata as ad
import numpy as np
import pytest
import scipy.sparse
import tifffile

from focus.registration.pixel_composition import PixelCompositionRegistration
from focus.spatial import pixel_clusters, unmixing
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.membership import pixel_footprint_membership, row_normalize
from tests.registration.data import hex_lattice, write_anchor, write_image_ome

SIZE = 320
WAVENUMBERS = np.linspace(2800, 3100, 12)
SPOT = np.array([55.0, 55.0])


def _endmembers() -> np.ndarray:
    """Three CH-window spectra (lipid, protein, a third band) on a flat background."""
    bands = ((2850, 3.0), (2930, 2.0), (3050, 1.5))
    return np.stack(
        [a * np.exp(-(((WAVENUMBERS - c) / 40) ** 2)) + 0.1 for c, a in bands]
    )


def _abundances() -> np.ndarray:
    """``(H, W, 3)`` smooth mixtures with pure corners."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE] / SIZE
    a = np.stack([(1 - xx) * (1 - yy), xx * (1 - yy), yy], -1) ** 3
    return a / a.sum(-1, keepdims=True)


def _spots() -> np.ndarray:
    """Visium-like spots (55 µm discs) on 1 µm pixels, anchor frame equal to the image frame."""
    return hex_lattice(n=5, pitch=65.0) - 500 + 35


def _truth(values: np.ndarray, measured: np.ndarray) -> np.ndarray:
    """Mean of ``(pixels, K)`` values over the measured pixel centres in every footprint."""
    spots = _spots()
    geom = AnchorGeometry.from_arrays(spots, spots, SPOT)
    weights = pixel_footprint_membership(geom, (0, 0, SIZE, SIZE), "ones")
    weights = weights @ scipy.sparse.diags(measured.astype(np.float32))
    return np.asarray(row_normalize(weights.tocsr()) @ values)


def _best_error(x: np.ndarray, truth: np.ndarray) -> float:
    """Largest absolute error over the column permutation that matches best."""
    k = truth.shape[1]
    return min(
        float(np.nanmax(np.abs(x[:, list(p)] - truth))) for p in permutations(range(k))
    )


def _raman(tmp: Path, extra: list | None = None) -> tuple[str, str, np.ndarray]:
    """Raman file, anchor file and the image (the right 20 columns are not measured)."""
    rng = np.random.default_rng(0)
    image = (_abundances() @ _endmembers()).transpose(2, 0, 1)
    image = (image + rng.normal(0, 0.02, image.shape)).astype(np.float32)
    image[:, :, 300:] = np.nan
    values = {"focus_wavenumbers_cm1": json.dumps(WAVENUMBERS.tolist())}
    names = [f"{w:g} cm-1" for w in WAVENUMBERS]
    raman = str(tmp / "raman.ome.tiff")
    write_image_ome(raman, [(image, values, names), *(extra or [])])
    anchor = str(tmp / "anchor.h5ad")
    write_anchor(anchor, _spots(), _spots(), "raman", SPOT)
    return raman, anchor, image


class TestModels:
    """Unmixing and pixel clustering on synthetic pixels."""

    def test_abundances_solve_the_constrained_least_squares(self) -> None:
        """The active-set abundances reach the simplex-constrained optimum."""
        from scipy.optimize import minimize

        rng = np.random.default_rng(1)
        e = np.abs(rng.normal(1, 0.5, (4, 10)))
        x = rng.dirichlet(np.ones(4) * 0.3, 50) @ e + rng.normal(0, 0.3, (50, 10))
        a = unmixing.fully_constrained_abundances(x, e)
        assert np.all(a >= 0) and np.allclose(a.sum(axis=1), 1, atol=1e-6)
        for i in range(len(x)):
            loss = lambda v: float(((x[i] - v @ e) ** 2).sum())  # noqa: E731
            ref = minimize(
                loss,
                np.full(4, 0.25),
                method="SLSQP",
                bounds=[(0, 1)] * 4,
                constraints=[{"type": "eq", "fun": lambda v: v.sum() - 1}],
                options={"ftol": 1e-14, "maxiter": 500},
            )
            assert loss(a[i]) <= ref.fun + 1e-5

    def test_vca_finds_the_pure_spectra(self) -> None:
        """VCA returns the endmembers of mixtures that contain nearly pure pixels."""
        rng = np.random.default_rng(2)
        e = _endmembers()
        x = rng.dirichlet(np.full(3, 0.2), 30_000) @ e + rng.normal(
            0, 0.005, (30_000, 12)
        )
        found, _ = unmixing.vertex_component_analysis(x, 3)
        assert _best_error(found.T, e.T) < 0.05

    def test_pixel_clusters_recover_the_marker_phenotypes(self) -> None:
        """Pixie with the FlowSOM elbow finds the marker phenotypes of blurred stripes."""
        from sklearn.metrics import adjusted_rand_score

        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:SIZE, 0:SIZE]
        region = (xx // 80 + 4 * (yy // 160)) % 4
        profiles = np.ones((4, 6))
        profiles[np.arange(4), [0, 1, 2, 4]] = 20
        profiles[[1, 3], [3, 5]] = 10
        image = rng.poisson(profiles[region]).transpose(2, 0, 1).astype(np.float32)
        pixels = pixel_clusters.blur(image).reshape(6, -1).T
        model = pixel_clusters.fit_pixel_clusters(pixels)
        assert model.k == 4
        labels = pixel_clusters.assign_pixel_clusters(model, pixels)
        assert adjusted_rand_score(region.ravel(), labels) > 0.95

    def test_elbow_is_the_flowsom_criterion(self) -> None:
        """FlowSOM's elbow: two lines fitted to the smoothed curve; the split is the first ``k``
        of the second line, at the kink of the curve or one after it (the 3-point smoothing raises
        the kink point off the flat line)."""
        straight = [100, 80, 60, 40, 20, 19, 18, 17, 16, 15, 14]
        assert pixel_clusters.elbow(straight) in (5, 6)
        assert pixel_clusters.elbow([100, 60, 20, 18, 17, 16, 15, 14, 13, 12]) in (3, 4)
        assert pixel_clusters.elbow([10, 9]) == 2


class TestRegistration:
    """The registered AnnData of an image modality."""

    def test_raman_spots_get_the_mixed_composition(self, tmp_path: Path) -> None:
        """Endmember fractions, channel means and coverage follow the pixels in each footprint."""
        raman, anchor, image = _raman(tmp_path)
        files = PixelCompositionRegistration(str(tmp_path)).register_dataset(
            {"s1": anchor}, {"s1": raman}, "st", "raman"
        )
        out = ad.read_h5ad(files["merged"])
        assert list(out.var["phenotype"]) == [f"endmember {k}" for k in (1, 2, 3)]
        book = next(iter(out.uns["pixel_phenotypes"].values()))
        assert book["model"] == "unmixing" and book["criterion"]["spectral_rank"] == 2
        measured = np.isfinite(image).all(axis=0).ravel()
        hit = np.isfinite(out.X).all(axis=1)
        np.testing.assert_allclose(out.X[hit].sum(axis=1), 1.0, atol=1e-5)
        truth = _truth(_abundances().reshape(-1, 3), measured)
        assert _best_error(out.X[hit], truth[hit]) < 0.03
        pixels = np.where(measured[:, None], image.reshape(12, -1).T, 0)
        mean = _truth(pixels, measured)
        np.testing.assert_allclose(
            out.obsm["channel_mean"].to_numpy()[hit], mean[hit], atol=1e-4
        )
        right = _spots()[:, 0] > 300
        assert (out.obs["coverage"][right] < 0.9).all()
        assert (out.obs["coverage"][_spots()[:, 0] < 250] > 0.95).all()

    def test_label_map_overlays_the_image(self, tmp_path: Path) -> None:
        """The label map has the image's shape, 0 where nothing was measured or read."""
        raman, anchor, _ = _raman(tmp_path)
        PixelCompositionRegistration(str(tmp_path)).register_dataset(
            {"s1": anchor}, {"s1": raman}, "st", "raman"
        )
        path = tmp_path / "s1" / "registration" / "raman_s1_pixel_phenotypes.ome.tiff"
        labels = tifffile.imread(path)
        assert labels.shape == (SIZE, SIZE) and labels.dtype == np.uint8
        assert set(np.unique(labels)) <= {0, 1, 2, 3}
        assert (labels[:, 300:] == 0).all() and (labels[100:200, 100:200] > 0).all()

    def test_groups_without_composition_keep_their_mean(self, tmp_path: Path) -> None:
        """A two-channel image has no composition block, only channel means."""
        roi = np.full((2, 100, 100), 5.0, dtype=np.float32)
        extra = [(roi, {"focus_placement": "100,100,1,1"}, ["a", "b"])]
        raman, anchor, _ = _raman(tmp_path, extra)
        files = PixelCompositionRegistration(str(tmp_path)).register_dataset(
            {"s1": anchor}, {"s1": raman}, "st", "raman"
        )
        out = ad.read_h5ad(files["merged"])
        assert out.n_vars == 3
        columns = list(out.obsm["channel_mean"].columns)
        assert len(columns) == 14 and columns[-1] == "b (2 channels)"
        means = out.obsm["channel_mean"].to_numpy()[:, -2:]
        inside = (np.abs(_spots() - 150) < 20).all(axis=1)
        np.testing.assert_allclose(means[inside], 5.0)
        assert np.isnan(means[~(np.abs(_spots() - 150) < 80).all(axis=1)]).all()

    def test_codebook_is_reused_until_an_input_changes(self, tmp_path: Path) -> None:
        """A second run reads the stored codebook; a changed image refits it."""
        raman, anchor, image = _raman(tmp_path)
        engine = PixelCompositionRegistration(str(tmp_path))
        engine.register_dataset({"s1": anchor}, {"s1": raman}, "st", "raman")
        with patch("focus.registration.pixel_codebook.fit_group") as fit:
            engine.register_dataset({"s1": anchor}, {"s1": raman}, "st", "raman")
            fit.assert_not_called()
        values = {"focus_wavenumbers_cm1": json.dumps(WAVENUMBERS.tolist())}
        os.remove(raman)
        write_image_ome(raman, [(image * 2, values, [f"{w:g}" for w in WAVENUMBERS])])
        with patch(
            "focus.registration.pixel_codebook.fit_group", side_effect=RuntimeError
        ):
            with pytest.raises(RuntimeError):
                engine.register_dataset({"s1": anchor}, {"s1": raman}, "st", "raman")


class TestConfiguration:
    """Which modalities accept pixel_composition."""

    def test_brightfield_and_spot_modalities_are_rejected(self) -> None:
        """Brightfield microscopy and spot modalities cannot use pixel_composition."""
        from focus.config.catalog import REGISTRATION_COMPATIBILITY
        from focus.config.compatibility import validate_microscopy_settings

        with pytest.raises(ValueError, match="feature_extraction' for brightfield"):
            validate_microscopy_settings(
                "he", {"image_type": "brightfield"}, "pixel_composition"
            )
        validate_microscopy_settings(
            "if", {"image_type": "fluorescence"}, "pixel_composition"
        )
        assert REGISTRATION_COMPATIBILITY["pixel_composition"] == [
            "raman",
            "microscopy_image",
        ]

    def test_a_brightfield_output_is_rejected_at_registration(
        self, tmp_path: Path
    ) -> None:
        """An image recorded as brightfield stops the registration with the reason."""
        from focus.registration.pixel_codebook import image_model

        path = str(tmp_path / "he.ome.tiff")
        image = np.zeros((3, 8, 8), dtype=np.uint8)
        write_image_ome(
            path, [(image, {}, ["r", "g", "b"])], {"focus_image_type": "brightfield"}
        )
        with pytest.raises(ValueError, match="use feature_extraction"):
            image_model(path)
