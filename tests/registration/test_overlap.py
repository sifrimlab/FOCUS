"""Overlap-area registration: exact overlaps, both sampling directions, conservation, size regimes."""

import copy
import os
import tempfile
from pathlib import Path

import anndata as ad
import numpy as np
import pytest
import scipy.sparse as sp
from shapely import affinity
from shapely.geometry import box

from focus.config.types import PipelineConfig
from focus.config.validation import parse_config
from focus.registration.spot_interpolation import SpotInterpolationRegistration
from focus.runtime.reporting import StepReporter
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.footprint_shape import footprint_shape
from focus.spatial.overlap import (
    _anchor_fractions,
    _target_fractions,
    anchor_areas,
    footprint_area,
    footprint_overlap,
)
from tests.registration.data import square_lattice, write_anchor


def lattice(pitch: float, n: int) -> np.ndarray:
    """``n x n`` square lattice of centres at ``pitch``, the first tile starting at the origin."""
    return square_lattice(n, pitch, origin=(pitch / 2, pitch / 2))


def geometry(
    anchors: np.ndarray, size: float, rotation: float = 0.0, shift: float = 0.0
) -> AnchorGeometry:
    """Anchors mapped by a rotation about the origin plus a shift (a similarity transform)."""
    c, s = np.cos(rotation), np.sin(rotation)
    mapped = anchors @ np.array([[c, s], [-s, c]]) + shift
    return AnchorGeometry.from_arrays(anchors, mapped, np.array([size, size]))


def exact_overlap(
    anchors: np.ndarray,
    size: float,
    rotation: float,
    shift: float,
    target: np.ndarray,
    pitch: float,
    tile: float,
) -> np.ndarray:
    """Exact shapely overlap areas, ``(N_anchor, N_target)``, in the target frame."""
    out = np.zeros((len(anchors), len(target)))
    boxes = [
        box(x - tile / 2, y - tile / 2, x + tile / 2, y + tile / 2) for x, y in target
    ]
    for i, (x, y) in enumerate(anchors):
        footprint = box(x - size / 2, y - size / 2, x + size / 2, y + size / 2)
        # Anchor frame -> target frame: rotate about the origin (same sense as ``geometry``).
        footprint = affinity.rotate(footprint, np.degrees(rotation), origin=(0, 0))
        footprint = affinity.translate(footprint, shift, shift)
        for j, tile_box in enumerate(boxes):
            out[i, j] = footprint.intersection(tile_box).area
    return out


class TestExactOverlap:
    """Sampled overlap matches the exact polygon overlap in every size regime."""

    @pytest.mark.parametrize(
        ("size", "pitch", "rotation", "shift"),
        [
            (90.0, 30.0, 0.0, 7.0),  # anchor three times larger
            (30.0, 30.0, 0.0, 15.0),  # equal size, half-pixel shift
            (6.0, 30.0, 0.0, 3.3),  # anchor five times smaller
            (600.0, 30.0, 0.0, 11.0),  # anchor twenty times larger
            (40.0, 30.0, 0.4, 5.0),  # rotated map
        ],
    )
    def test_matches_shapely(
        self, size: float, pitch: float, rotation: float, shift: float
    ) -> None:
        """Mean relative L1 error of the overlap row is small."""
        rng = np.random.default_rng(0)
        n = max(30, int(1500 / pitch))
        target = lattice(pitch, n)
        anchors = rng.uniform(0.4, 0.6, (25, 2)) * n * pitch / 1.5
        geom = geometry(anchors, size, rotation, shift)
        tile_shape = footprint_shape(target, np.array([pitch, pitch]))
        overlap = footprint_overlap(geom, target, tile_shape)
        expected = exact_overlap(anchors, size, rotation, shift, target, pitch, pitch)
        errors = []
        for i in range(len(anchors)):
            row = overlap.area.getrow(i).toarray().ravel()
            errors.append(np.abs(row - expected[i]).sum() / expected[i].sum())
        assert np.mean(errors) < 0.08

    def test_equal_size_half_shift_gives_the_four_neighbours_a_quarter(self) -> None:
        """An anchor offset by half a pixel in both axes overlaps four tiles by a quarter each."""
        target = lattice(30.0, 10)
        anchors = target[[44, 45, 54]] + 15.0
        geom = geometry(anchors, 30.0)
        overlap = footprint_overlap(
            geom, target, footprint_shape(target, np.array([30.0, 30.0]))
        )
        row = overlap.area.getrow(0).toarray().ravel()
        np.testing.assert_allclose(np.sort(row)[-4:], 225.0, rtol=1e-4)
        assert overlap.coverage()[0] == pytest.approx(1.0)

    def test_the_two_sampling_directions_agree(self) -> None:
        """Sampling anchors or targets estimates the same overlap area."""
        rng = np.random.default_rng(1)
        target = lattice(30.0, 40)
        anchors = rng.uniform(300, 900, (40, 2))
        geom = geometry(anchors, 50.0, rotation=0.2, shift=3.0)
        tile = footprint_shape(target, np.array([30.0, 30.0]))
        from_anchor = (
            sp.diags(anchor_areas(geom)) @ _anchor_fractions(geom, target, tile, 32)
        ).toarray()
        from_target = (
            _target_fractions(geom, target, tile, 32) * footprint_area(tile)
        ).toarray()
        np.testing.assert_allclose(from_anchor, from_target, atol=0.02 * 900)


class TestSizeRegimes:
    """Constant fields stay constant, counts are conserved, nothing is wasted."""

    @pytest.mark.parametrize("size", [3.0, 15.0, 30.0, 120.0, 900.0])
    def test_a_constant_intensity_stays_constant(self, size: float) -> None:
        """Area-weighted means of a constant field give that constant, whatever the anchor size."""
        target = lattice(30.0, 40)
        anchors = np.array([[500.0, 500.0], [610.3, 702.9], [130.0, 877.0]])
        geom = geometry(anchors, size)
        overlap = footprint_overlap(
            geom, target, footprint_shape(target, np.array([30.0, 30.0]))
        )
        result = overlap.mean_weights() @ np.full((len(target), 1), 2.5)
        covered = np.asarray(overlap.area.sum(axis=1)).ravel() > 0
        np.testing.assert_allclose(result.ravel()[covered], 2.5, rtol=1e-5)

    def test_gaps_between_target_spots_lower_the_coverage_only(self) -> None:
        """Target spots smaller than their pitch cover part of a large anchor, the mean is unchanged."""
        target = lattice(30.0, 40)
        anchors = np.array([[600.0, 600.0], [400.0, 300.0], [300.0, 700.0]])
        geom = geometry(anchors, 300.0)
        overlap = footprint_overlap(
            geom, target, footprint_shape(target, np.array([18.0, 18.0]))
        )
        np.testing.assert_allclose(overlap.coverage(), 0.36, atol=0.02)
        result = overlap.mean_weights() @ np.full((len(target), 1), 4.0)
        np.testing.assert_allclose(result.ravel(), 4.0, rtol=1e-5)

    def test_small_weak_spots_on_a_large_anchor_lose_no_counts(self) -> None:
        """Every target spot inside a tiling of large anchors is counted exactly once."""
        target = lattice(2.0, 100)  # 2 um bins, 200 um section
        counts = np.random.default_rng(2).poisson(0.3, len(target)).astype(np.float64)
        anchors = lattice(50.0, 4)  # 50 um anchors tiling the same section
        geom = geometry(anchors, 50.0)
        overlap = footprint_overlap(
            geom, target, footprint_shape(target, np.array([2.0, 2.0]))
        )
        registered = overlap.share_weights() @ counts
        assert registered.sum() == pytest.approx(counts.sum(), rel=1e-5)
        assert overlap.sampled == "target"

    def test_a_large_target_spot_is_shared_among_small_anchors_by_area(self) -> None:
        """Anchors tiling one large target spot receive its counts in proportion to their area."""
        target = np.array([[50.0, 50.0]])
        anchors = lattice(10.0, 10)  # 100 um section tiled by 10 um anchors
        geom = geometry(anchors, 10.0)
        overlap = footprint_overlap(
            geom, target, footprint_shape(target, np.array([100.0, 100.0]))
        )
        registered = overlap.share_weights() @ np.array([200.0])
        np.testing.assert_allclose(registered, 2.0, rtol=1e-4)
        assert registered.sum() == pytest.approx(200.0, rel=1e-4)
        assert overlap.sampled == "anchor"

    def test_a_warped_map_still_covers_the_anchors(self) -> None:
        """A map that is not one matrix (local affine) places anchors over the tiles they cover."""
        target = lattice(30.0, 40)
        anchors = lattice(40.0, 20) + 100.0
        mapped = anchors + 4.0 * np.sin(anchors / 90.0)
        geom = AnchorGeometry.from_arrays(anchors, mapped, np.array([40.0, 40.0]))
        assert geom.transform.kind == "local_affine"
        overlap = footprint_overlap(
            geom, target, footprint_shape(target, np.array([30.0, 30.0]))
        )
        assert overlap.coverage().min() > 0.95


class Recorder(StepReporter):
    """Reporter that keeps the warnings it receives."""

    def __init__(self) -> None:
        """Start without warnings."""
        super().__init__()
        self.warnings: list[str] = []

    def warning(self, msg: str) -> None:
        """Keep the warning."""
        self.warnings.append(msg)


class TestEngine:
    """The reported failure and the extensive and intensive reductions through the engine."""

    def register(
        self,
        root: str,
        anchors: np.ndarray,
        anchor_size: float,
        scale: float,
        target_xy: np.ndarray,
        target_size: float,
        reporter: StepReporter,
    ) -> ad.AnnData:
        """Register a constant-intensity target onto anchors whose coordinates are ``scale`` x the target's."""
        os.makedirs(os.path.join(root, "s"), exist_ok=True)
        anchor_path = os.path.join(root, "s", "anchor.h5ad")
        write_anchor(
            anchor_path, anchors / scale, anchors, "msi", [anchor_size, anchor_size]
        )
        target = ad.AnnData(
            X=sp.csr_matrix(np.ones((len(target_xy), 3), dtype=np.float32))
        )
        target.obsm["spatial"] = target_xy.astype(np.float32)
        target.uns["spot_size"] = np.array([target_size, target_size], dtype=np.float32)
        target_path = os.path.join(root, "s", "target.h5ad")
        target.write_h5ad(target_path)
        files = SpotInterpolationRegistration(root).register_dataset(
            {"s": anchor_path}, {"s": target_path}, "ref", "msi", step_reporter=reporter
        )
        return ad.read_h5ad(files["s"])

    def test_cells_over_a_coarser_grid_keep_their_values(self) -> None:
        """50 um cell footprints on a 30 um grid leave no empty row inside the grid (the reported case)."""
        rng = np.random.default_rng(4)
        cells = rng.uniform(100, 1100, (500, 2))
        grid = lattice(30.0, 40)
        with tempfile.TemporaryDirectory() as root:
            recorder = Recorder()
            out = self.register(root, cells, 50.0, 1.0, grid, 30.0, recorder)
        assert (np.asarray(out.X.sum(axis=1)).ravel() > 0).all()
        np.testing.assert_allclose(out.X.toarray(), 1.0, rtol=1e-5)
        assert not recorder.warnings

    def test_coordinates_in_pixels_raise_the_scale_warning(self) -> None:
        """An anchor in pixels (0.2125 um each) is flagged instead of silently shrinking the footprints."""
        rng = np.random.default_rng(5)
        cells = rng.uniform(100, 1100, (200, 2))
        grid = lattice(30.0, 40)
        with tempfile.TemporaryDirectory() as root:
            recorder = Recorder()
            self.register(root, cells, 50.0, 0.2125, grid, 30.0, recorder)
        assert any("pixels" in message for message in recorder.warnings)


def test_the_removed_aggregation_type_names_its_replacement() -> None:
    """A config that still asks for spot_aggregation fails and points to spot_interpolation."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "s1", "rna").mkdir(parents=True)
        config: PipelineConfig = {
            "dataset_path": root,
            "reference_modality": "rna",
            "perform_alignment": False,
            "perform_registration": False,
            "modalities": [
                {
                    "name": "rna",
                    "type": "st",
                    "alignment_strategy": "manual",
                    "registration_type": "spot_interpolation",
                    "processing_settings": {},
                    "registration_settings": {},
                }
            ],
        }
        parse_config(copy.deepcopy(config), require_raw_inputs=False)
        config["modalities"][0]["registration_type"] = "spot_aggregation"
        with pytest.raises(ValueError, match="use 'spot_interpolation'"):
            parse_config(copy.deepcopy(config), require_raw_inputs=False)
