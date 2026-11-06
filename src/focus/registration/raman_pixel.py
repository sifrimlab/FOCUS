"""Gaussian footprint readout from the images of a Raman OME-TIFF."""

from functools import partial

from focus.registration.types import RegistrationJob

from focus.registration.anchor import AnchorArrays
from focus.spatial.footprint import AnchorGeometry
from anndata import AnnData
from focus.runtime.reporting import StepReporter
from collections import Counter
import logging

import anndata
import numpy as np
import pandas as pd
import scipy.sparse

from focus.config.options import RegistrationType
from focus.registration.execution import register_target
from focus.registration.pixel_readout import (
    RamanTarget,
    channel_count,
    footprint_crop,
    image_dims,
    image_geometry,
    load_crop,
    raman_targets,
    target_signature,
)
from focus.spatial.membership import pixel_footprint_membership, row_normalize
from focus.runtime.reporter_context import get_reporter

logger = logging.getLogger(__name__)


def feature_names(targets: list[RamanTarget], counts: list[int]) -> pd.DataFrame:
    """``var`` of the registered features: channel names, made unique with the acquisition name."""
    names: list[str] = []
    wavenumbers: list[float] = []
    acquisitions: list[str] = []
    for target, count in zip(targets, counts):
        channels = target.channels if len(target.channels) == count else []
        for i in range(count):
            names.append(channels[i] if channels else f"Channel_{len(names)}")
            wavenumbers.append(
                target.wavenumbers[i]
                if target.wavenumbers and len(target.wavenumbers) == count
                else float("nan")
            )
            acquisitions.append(target.name)
    seen = Counter(names)
    unique = [f"{n} ({a})" if seen[n] > 1 else n for n, a in zip(names, acquisitions)]
    return pd.DataFrame(
        {"wavenumber": wavenumbers, "acquisition": acquisitions}, index=unique
    )


class RamanPixelInterpolationRegistration:
    """
    Register a Raman OME-TIFF modality to the anchor by Gaussian-weighted interpolation.

    Each pixel of every image of the Raman OME-TIFF is a "spot" located at its centre (pixel
    ``(col, row)`` is the point ``(col + 0.5, row + 0.5)``) whose feature vector is its spectral
    intensities. For each anchor spot, every image contributes the Gaussian-weighted average of its
    measured pixels whose centres lie inside the spot's footprint; images are concatenated along
    the features. A spot that an image does not measure gets NaN for that image's channels.

    The footprint is evaluated in the anchor frame, where ``spot_size`` is defined: pixel centres
    are mapped back through the inverse of the alignment transform, composed with the image's
    placement in image 0, so the anchor's spot size is honoured whatever the Raman pixel size.

    Coordinate convention:
    - ``anchor_files`` contains the aligned reference AnnData: ``obsm['spatial']`` (anchor frame)
      and ``obsm['{target_name}_spatial']`` (the same spots in level-0 pixels of Raman image 0).

    Parameters
    ----------
    path : str
        Path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.RAMAN_PIXEL_INTERPOLATION

    def __init__(self, path: str) -> None:
        """Retain the dataset root; registration reads source files when requested.

        Parameters
        ----------
        path : str
            Dataset root for sample and merged registration outputs."""
        self._path = path

    def _image_features(
        self,
        filename: str,
        target: RamanTarget,
        geom: AnchorGeometry,
        n_obs: int,
        n_channels: int,
    ) -> tuple[np.ndarray, int]:
        """``(N, C)`` features of one image and the number of spots it measures."""
        height, width = image_dims(filename, target.series)
        out = np.full((n_obs, n_channels), np.nan, dtype=np.float32)
        crop = footprint_crop(geom, height, width)
        if crop is None:
            return out, 0
        pixels = load_crop(filename, target.series, crop)
        features = pixels.reshape(pixels.shape[0], -1).T  # (crop pixels, C)
        measured = np.isfinite(features).all(axis=1)
        weights = pixel_footprint_membership(geom, crop, weights="gaussian")
        weights = (weights @ scipy.sparse.diags(measured.astype(np.float32))).tocsr()
        totals = np.asarray(weights.sum(axis=1)).ravel()
        values = row_normalize(weights) @ np.where(measured[:, None], features, 0.0)
        hit = totals > 0
        spectra = target.spot_spectra(np.asarray(values, dtype=np.float64)[hit])
        out[hit] = spectra.astype(np.float32)
        return out, int(hit.sum())

    def _compute_sample(
        self,
        target_files: dict[str, str],
        reporter: StepReporter,
        sample_id: str,
        anchor: AnchorArrays,
        geom: AnchorGeometry,
    ) -> tuple[AnnData | None, dict[str, object]]:
        """Compute one sample's registered features and geometry metadata."""
        filename = target_files[sample_id]
        targets = raman_targets(filename)
        blocks: list[np.ndarray] = []
        counts: list[int] = []
        for target in targets:
            image_geom = image_geometry(anchor, geom, target)
            n_channels = channel_count(filename, target.series)
            features, measured = self._image_features(
                filename, target, image_geom, anchor.n_obs, n_channels
            )
            blocks.append(features)
            counts.append(n_channels)
            label = f"'{target.name}'" if target.name else "Raman image"
            if measured == 0:
                reporter.warning(
                    f"All {anchor.n_obs} anchor spots fall outside {label}: its channels are NaN"
                )
            else:
                reporter.detail(
                    f"{label}: {measured}/{anchor.n_obs} spots measured, {n_channels} channels"
                )
        features = np.concatenate(blocks, axis=1)
        logger.debug(
            f"Anchor: {anchor.n_obs} spots, spot_size={anchor.spot_size}. "
            f"Raman: {len(targets)} image(s), {features.shape[1]} channels."
        )
        adata = anndata.AnnData(
            X=features,
            obsm={"spatial": anchor.mapped.copy()},
            obs={"sample_id": [sample_id] * anchor.n_obs},
            var=feature_names(targets, counts),
        )
        return adata, {"raman_images": len(targets)}

    def register_dataset(
        self,
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        anchor_name: str,
        target_name: str,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Register a Raman OME-TIFF to the anchor grid using Gaussian pixel interpolation.

        Parameters
        ----------
        anchor_files : dict[str, str]
            Aligned anchor (reference) modality files. Each AnnData must contain:
            - ``obsm['spatial']``: anchor spot positions in the anchor's own frame.
            - ``obsm['{target_name}_spatial']``: the same spots in Raman level-0 pixels
              (set during alignment).
            - ``uns['spot_size']``: anchor spot dimensions, in the units of ``obsm['spatial']``.
            {sample_id: h5ad_path}
        target_files : dict[str, str]
            Raman OME-TIFF files (one image per acquisition, or a single stitched image).
            {sample_id: ome_tiff_path}
        anchor_name : str
            Name of the anchor (reference) modality.
        target_name : str
            Name of the Raman modality being registered.
        force_recomputing : bool
            Recompute even when the stored fingerprint matches.
        step_reporter : StepReporter, optional
            Reports per-sample progress to the GUI.

        Returns
        -------
        dict[str, str]
            {sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
        """
        common_samples = sorted(
            set(anchor_files.keys()) & set(target_files.keys()) - {"merged"}
        )
        reporter = step_reporter or get_reporter()

        return register_target(
            RegistrationJob(
                path=self._path,
                samples=reporter.tqdm(
                    common_samples,
                    1,
                    2,
                    "Interpolating Raman pixels onto anchor spots",
                    unit="sample",
                ),
                anchor_files=anchor_files,
                target_files=target_files,
                anchor_name=anchor_name,
                target_name=target_name,
                registration_type=self._REGISTRATION_TYPE,
                settings=lambda sample: {
                    "images": target_signature(raman_targets(target_files[sample]))
                },
                compute=partial(self._compute_sample, target_files, reporter),
                force_recomputing=force_recomputing,
                reporter=reporter,
            ),
            reporter,
        )
