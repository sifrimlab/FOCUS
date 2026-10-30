import os
import logging
import xml.etree.ElementTree as ET

import anndata
import numpy as np
import pandas as pd
import tifffile

from focus.constants import RegistrationType
from focus.utils import hw_from_axes
from focus.registration._cache import register_samples, merge_registered_samples
from focus.registration._footprint import pixel_footprint_membership, row_normalize
from focus.reporting import get_reporter

logger = logging.getLogger(__name__)


class RamanPixelInterpolationRegistration:
    """
    Register a Raman OME-TIFF modality to the anchor by Gaussian-weighted interpolation.

    Treats each pixel of the ASHLAR-stitched Raman OME-TIFF as a "spot" located at its centre
    (pixel ``(col, row)`` is the point ``(col + 0.5, row + 0.5)``) whose feature vector is its
    spectral intensities across channels. For each anchor spot the Gaussian-weighted average
    of the pixels whose centres lie inside the spot's footprint is computed, producing one
    spectral vector per anchor spot.

    The footprint is evaluated in the anchor frame, where ``spot_size`` is defined: pixel
    centres are mapped back through the inverse of the alignment transform, so the anchor's
    spot size is honoured whatever the Raman pixel size.

    Coordinate convention:
    - ``anchor_files`` contains the aligned reference AnnData: ``obsm['spatial']`` (anchor
      frame) and ``obsm['{target_name}_spatial']`` (the same spots in Raman level-0 pixels).

    Parameters
    ----------
    path : str
        Path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.RAMAN_PIXEL_INTERPOLATION

    def __init__(self, path: str) -> None:
        self._path = path

    @staticmethod
    def _load_raman_crop(
        filename: str,
        crop: tuple[int, int, int, int],
    ) -> tuple[np.ndarray, list[str]]:
        """Load the level-0 pixels ``[c0, c1) x [r0, r1)`` of a Raman OME-TIFF.

        Parameters
        ----------
        filename : str
            Path to the ASHLAR-stitched Raman OME-TIFF.
        crop : (c0, r0, c1, r1)
            Half-open pixel box, already clamped to the image.

        Returns
        -------
        pixels : np.ndarray (C, r1 - r0, c1 - c0) float32
            Spectral intensities of the crop.
        channel_names : list[str]
            Name for each spectral channel (from OME metadata or 'Channel_N').
        """
        with tifffile.TiffFile(filename) as tif:
            series = tif.series[0]

            # Prefer full-resolution level of a pyramid; fall back to the series itself
            full_level = series.levels[0] if len(series.levels) > 1 else series
            axes = series.axes.upper()

            # Extract channel names from OME-XML (best-effort)
            channel_names = None
            if tif.is_ome and tif.ome_metadata:
                try:
                    root = ET.fromstring(tif.ome_metadata)
                    tag_prefix = root.tag.split('}')[0] + '}' if '}' in root.tag else ''
                    ch_elements = root.findall(f'.//{tag_prefix}Channel')
                    names = [c.get('Name', '') for c in ch_elements]
                    if any(n for n in names):
                        channel_names = [n if n else f'Channel_{i}' for i, n in enumerate(names)]
                except Exception:
                    pass

            image = full_level.asarray()

        # Squeeze leading singleton OME dims (T=1, Z=1 in TZCYX → CYX)
        axes_trimmed = axes[-len(image.shape):]
        while image.ndim > 3 and image.shape[0] == 1:
            image = image[0]
            axes_trimmed = axes_trimmed[1:]

        # Normalize to (C, Y, X)
        axes_3 = axes_trimmed[-3:] if len(axes_trimmed) >= 3 else axes_trimmed
        if len(set(axes_3) & {'C', 'Y', 'X'}) == 3:
            c_idx = axes_3.index('C')
            y_idx = axes_3.index('Y')
            x_idx = axes_3.index('X')
            image = np.transpose(image, (c_idx, y_idx, x_idx))
        elif image.ndim == 3:
            # Fallback: first dim is channels when it is the smallest, i.e., CHW layout
            if image.shape[0] < image.shape[1] and image.shape[0] < image.shape[2]:
                pass  # already (C, Y, X)
            elif image.shape[2] < image.shape[0] and image.shape[2] < image.shape[1]:
                image = np.transpose(image, (2, 0, 1))  # HWC → CHW
        elif image.ndim == 2:
            image = image[np.newaxis]

        if channel_names is None:
            channel_names = [f'Channel_{i}' for i in range(image.shape[0])]

        c0, r0, c1, r1 = crop
        return np.asarray(image[:, r0:r1, c0:c1], dtype=np.float32), channel_names

    @staticmethod
    def _image_dims(filename: str) -> tuple[int, int]:
        """Return (H, W) of the full-resolution OME-TIFF without loading pixel data."""
        with tifffile.TiffFile(filename) as tif:
            series = tif.series[0]
            if len(series.levels) > 1:
                shape = series.levels[0].shape
                axes = getattr(series.levels[0], "axes", "") or getattr(series, "axes", "")
            else:
                shape = series.shape
                axes = getattr(series, "axes", "")
        # Identify (Y, X) from OME axes metadata (shared with the alignment image loader).
        return hw_from_axes(shape, axes)

    def register_dataset(
        self,
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        anchor_name: str,
        target_name: str,
        force_recomputing: bool = False,
        step_reporter=None,
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
            ASHLAR-stitched Raman OME-TIFF files.
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
        common_samples = sorted(set(anchor_files.keys()) & set(target_files.keys()) - {"merged"})
        reporter = step_reporter or get_reporter()

        def compute(sample_id, anchor, geom):
            height, width = self._image_dims(target_files[sample_id])
            # Load only the pixels that some footprint can reach.
            boxes = geom.pixel_bboxes(height, width)
            reach = boxes[(boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])]
            if len(reach) == 0:
                # The anchor spots fall entirely outside the Raman image: there is no tissue
                # overlap, so every row is a zero spectral vector.
                reporter.warning(
                    f"All {anchor.n_obs} anchor spots fall outside the Raman image: "
                    f"zero spectral vectors assigned (no overlap)"
                )
                crop = (0, 0, 0, 0)
            else:
                crop = (int(reach[:, 0].min()), int(reach[:, 1].min()), int(reach[:, 2].max()), int(reach[:, 3].max()))
            pixels, channel_names = self._load_raman_crop(target_files[sample_id], crop)
            weights = row_normalize(pixel_footprint_membership(geom, crop, weights="gaussian"))
            features = pixels.reshape(pixels.shape[0], -1).T                 # (crop pixels, C)
            logger.debug(
                f"Anchor: {anchor.n_obs} spots, spot_size={anchor.spot_size}. "
                f"Raman crop {crop}: {features.shape[0]} pixels, {features.shape[1]} channels."
            )
            adata = anndata.AnnData(
                X=np.asarray(weights @ features, dtype=np.float32),
                obsm={'spatial': anchor.mapped.copy()},
                obs={'sample_id': [sample_id] * anchor.n_obs},
                var=pd.DataFrame(index=channel_names),
            )
            return adata, {}

        registered_files = register_samples(
            path=self._path,
            samples=reporter.tqdm(common_samples, 1, 2, "Interpolating Raman pixels onto anchor spots", unit="sample"),
            anchor_files=anchor_files, target_files=target_files,
            anchor_name=anchor_name, target_name=target_name,
            registration_type=self._REGISTRATION_TYPE, settings={}, compute=compute,
            force_recomputing=force_recomputing, reporter=reporter,
        )
        reporter.step(2, 2, "Merging samples")
        return merge_registered_samples(
            self._path, registered_files, target_name, self._REGISTRATION_TYPE, force_recomputing=force_recomputing,
        )
