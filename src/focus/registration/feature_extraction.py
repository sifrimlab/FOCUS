"""Coordinate per-anchor microscopy embeddings with lazy encoder construction."""

from focus.runtime.reporting import StepReporter


from focus.config.options import (
    MicroscopyImageType,
    RegistrationType,
    SegmentationBackgroundColor,
)
from focus.io.ome import read_image_type
from focus.registration.execution import register_samples
from focus.registration.merging import merge_registered_samples
from focus.runtime.reporter_context import get_reporter


class FeatureExtractorRegistration:
    """
    Register a microscopy image to the anchor with one image embedding per anchor spot.

    Each anchor spot's footprint (its ``spot_size`` in the anchor frame, shaped by the anchor
    lattice) is mapped into the image through the alignment transform. The image is read at
    the stored pyramid level closest to the encoder's 0.5 µm per pixel and encoded by
    Prov-GigaPath in windows; every spot gets the CLS embedding of a query that attends only
    to the window tokens whose centres lie inside its footprint (see
    ``MicroscopyImageFeatureExtractor.extract_spot_features``).

    Parameters
    ----------
    path : str
            The path to the dataset folder.
    hf_token : str, optional
            HuggingFace token for downloading pretrained models.
    """

    _REGISTRATION_TYPE = RegistrationType.FEATURE_EXTRACTION

    def __init__(self, path: str, hf_token: str | None = None) -> None:
        """Retain the dataset root and token without constructing or downloading an encoder.

        Parameters
        ----------
        path : str
            Dataset root for registered sample and merged files.
        hf_token : str, optional
            Token forwarded to the lazily constructed microscopy encoder."""
        self._path = path
        self._hf_token = hf_token

    def register_dataset(
        self,
        image_files: dict[str, str],
        anchor_files: dict[str, str],
        image_name: str,
        anchor_name: str,
        anchor_type: str | None = None,
        force_recomputing: bool = False,
        background_color: str | None = None,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Extract one embedding per anchor spot from the image modality.

        Parameters
        ----------
        image_files : dict[str, str]
                Preprocessed image files. {sample_id: ome_tiff_path}
        anchor_files : dict[str, str]
                Aligned anchor AnnData files containing obsm['spatial'] (anchor frame) and
                obsm['{image_name}_spatial'] (the same spots in level-0 image pixels).
                {sample_id: h5ad_path}
        image_name : str
                Name of the image modality.
        anchor_name : str
                Name of the anchor modality.
        anchor_type : str, optional
                Modality type of the anchor. With an MSI anchor (coordinates in µm) the image's pixel
                size can be derived from the alignment when the image carries none.
        force_recomputing : bool
                Recompute even when the stored fingerprint matches.
        background_color : SegmentationBackgroundColor, optional
                Fill color of non-tissue pixels (default white).
        step_reporter : StepReporter, optional
                If provided, reports per-sample and per-spot progress to the GUI.

        Returns
        -------
        dict[str, str]
                {sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
        """
        # Imported here so the spot and Raman engines (which share this module) never load torch.
        from focus.registration.spot_encoding import SpotEncoding

        common_samples = sorted(
            set(image_files.keys()) & set(anchor_files.keys()) - {"merged"}
        )
        _reject_fluorescence(image_files, common_samples, image_name)
        reporter = step_reporter or get_reporter()
        background_color = background_color or SegmentationBackgroundColor.WHITE
        encoding = SpotEncoding(
            self._path,
            self._hf_token,
            image_files,
            anchor_type,
            background_color,
            reporter,
        )

        registered_files = register_samples(
            encoding.job(
                anchor_files, anchor_name, image_name, common_samples, force_recomputing
            )
        )
        # Drop the pretrained model so its parameters become collectable; the caller
        # (orchestrator._run_registration) then empties the CUDA cache via release_memory.
        encoding.extractor = None

        reporter.step(3, 3, "Merging samples")
        return merge_registered_samples(
            self._path,
            registered_files,
            image_name,
            self._REGISTRATION_TYPE,
            force_recomputing=force_recomputing,
        )


def _reject_fluorescence(
    image_files: dict[str, str], samples: list[str], name: str
) -> None:
    """Raise when a preprocessed image is recorded as fluorescence (H&E encoder only)."""
    fluorescence = [
        s
        for s in samples
        if read_image_type(image_files[s]) == MicroscopyImageType.FLUORESCENCE
    ]
    if fluorescence:
        raise ValueError(
            f"Modality '{name}': feature_extraction uses an H&E-pretrained encoder and does not "
            f"apply to fluorescence images (samples {', '.join(fluorescence)}); use "
            "registration_type 'pixel_composition'."
        )
