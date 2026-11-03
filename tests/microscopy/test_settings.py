"""Microscopy settings validation, the registration guard and the alignment composite."""

import json
from pathlib import Path

import pytest

from tests.microscopy.data import NAMES, fluorescence, settings, write


class TestSettings:
    """Removed knobs fail validation with their reason; fluorescence cannot use the H&E encoder."""

    def _validate(
        self, settings_: dict[str, object], registration: str = "none"
    ) -> None:
        from focus.config.compatibility import validate_microscopy_settings

        validate_microscopy_settings("he", settings_, registration)

    def test_removed_settings_raise_with_reason(self) -> None:
        """Each removed knob fails validation with the reason it was removed."""
        with pytest.raises(
            ValueError, match=r"'gamma' \(contrast is display-only and automatic\)"
        ):
            self._validate({"gamma": 0.45})
        with pytest.raises(
            ValueError, match=r"'crop_margin' \(the crop margin is a fixed 100 µm\)"
        ):
            self._validate({"crop_margin": 250})

    def test_presets_and_pixel_size_are_checked(self) -> None:
        """Unknown presets and non-positive pixel sizes fail; valid values pass."""
        with pytest.raises(ValueError, match="image_type must be one of"):
            self._validate({"image_type": "confocal"})
        with pytest.raises(ValueError, match="pixel_size must be a positive number"):
            self._validate({"pixel_size": 0})
        self._validate(
            {
                "image_type": "brightfield",
                "z_projection": "best_focus",
                "pixel_size": 0.25,
            }
        )

    def test_fluorescence_cannot_use_feature_extraction(self) -> None:
        """A configured fluorescence image cannot use the H&E encoder."""
        with pytest.raises(ValueError, match="does not apply to fluorescence"):
            self._validate({"image_type": "fluorescence"}, "feature_extraction")

    def test_registration_rejects_a_fluorescence_output(self, tmp_path: Path) -> None:
        """Registration refuses an output recorded as fluorescence."""
        from focus.registration.feature_extraction import _reject_fluorescence

        sample = write(
            tmp_path,
            "if.ome.tif",
            fluorescence(2),
            photometric="minisblack",
            metadata={"axes": "CYX"},
        )
        out = sample.process_image(settings())
        with pytest.raises(ValueError, match="does not apply to fluorescence"):
            _reject_fluorescence({"s": out}, ["s"], "img")


def test_alignment_view_renders_the_stored_windows(tmp_path: Path) -> None:
    """The alignment payload is a coloured composite of the shown channels within their windows."""
    from focus.alignment.image_view import load_view

    sample = write(
        tmp_path,
        "if.ome.tif",
        fluorescence(4),
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": NAMES}},
    )
    view, _, _, low, full = load_view(sample.process_image(settings()))
    assert view is not None and low == full == (900, 1100)
    assert [c["name"] for c in view.metadata()] == NAMES
    dapi_only = view.render([0])
    assert dapi_only.shape == (900, 1100, 3)
    assert (
        dapi_only[..., :2].max() == 0 and dapi_only[450, 550, 2] > 200
    )  # blue, tissue bright
    assert view.render([]).max() == 0
    assert json.dumps(view.metadata())  # JSON-ready for the GUI
