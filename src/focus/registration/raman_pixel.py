import os
import logging
import xml.etree.ElementTree as ET

import anndata
import numpy as np
import pandas as pd
import tifffile

from focus.constants import MODALITY_REGISTRATION, MODALITY_REGISTRATION_MERGED, RegistrationType
from focus.utils import write_h5ad_compat, read_merged_sample_ids, registration_cache_valid
from focus.registration.registration import SpotInterpolationRegistration

logger = logging.getLogger(__name__)

_H5AD_COMPRESSION = "gzip"


class RamanPixelInterpolationRegistration:
    """
    Register a Raman OME-TIFF modality to the anchor by Gaussian-weighted interpolation.

    Treats each pixel of the ASHLAR-stitched Raman OME-TIFF as a "spot" whose
    position is its pixel coordinate (x=col, y=row) and whose feature vector is its
    spectral intensities across channels. For each anchor spot the Gaussian-weighted
    average of all pixels within the spot's footprint is computed, producing one
    spectral vector per anchor spot.

    Coordinate convention:
    - ``anchor_files`` contains the aligned reference AnnData.
      ``obsm['{target_name}_spatial']`` holds anchor spot positions in the Raman
      image's pixel coordinate system (set by the alignment GUI).
    - Raman pixel at grid position (col, row) has coordinate (col, row) = (x, y),
      matching the convention used by the alignment step.

    Parameters
    ----------
    path : str
        Path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.RAMAN_PIXEL_INTERPOLATION

    def __init__(self, path: str) -> None:
        self._path = path

    @staticmethod
    def _load_raman_pixels(
        filename: str,
        bbox: tuple[int, int, int, int] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """Load a Raman OME-TIFF and return pixel coordinates, spectral features, and channel names.

        Parameters
        ----------
        filename : str
            Path to the ASHLAR-stitched Raman OME-TIFF.
        bbox : (x_min, y_min, x_max, y_max) | None
            Pixel bounding box to load. Clamped to image bounds.
            If None, the full image is loaded.

        Returns
        -------
        coords : np.ndarray (N, 2) float32
            Pixel (x, y) coordinates for every loaded pixel, in the same
            pixel-space used by the alignment step.
        features : np.ndarray (N, C) float32
            Spectral intensities per pixel.
        channel_names : list[str]
            Name for each spectral channel (from OME metadata or 'Channel_N').
        """
        with tifffile.TiffFile(filename) as tif:
            series = tif.series[0]

            # Prefer full-resolution level of a pyramid; fall back to the series itself
            full_level = series.levels[0] if len(series.levels) > 1 else series

            orig_shape = full_level.shape
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

        C, H, W = image.shape

        if channel_names is None:
            channel_names = [f'Channel_{i}' for i in range(C)]

        # Clamp bounding box to image dims
        if bbox is not None:
            x_min, y_min, x_max, y_max = bbox
        else:
            x_min, y_min, x_max, y_max = 0, 0, W, H

        x_min = max(0, x_min)
        y_min = max(0, y_min)
        x_max = min(W, x_max)
        y_max = min(H, y_max)

        if x_min >= x_max or y_min >= y_max:
            raise ValueError(
                f"Bounding box {bbox} is empty or outside image bounds ({W}x{H}) for {filename}"
            )

        # Crop to bbox and build pixel grid
        crop = image[:, y_min:y_max, x_min:x_max]  # (C, crop_H, crop_W)

        cols, rows = np.meshgrid(
            np.arange(x_min, x_max, dtype=np.float32),
            np.arange(y_min, y_max, dtype=np.float32),
        )
        coords = np.stack([cols.ravel(), rows.ravel()], axis=1)  # (N, 2) as (x, y)
        features = crop.reshape(C, -1).T.astype(np.float32)      # (N, C)

        return coords, features, channel_names

    @staticmethod
    def _image_dims(filename: str) -> tuple[int, int]:
        """Return (H, W) of the full-resolution OME-TIFF without loading pixel data."""
        with tifffile.TiffFile(filename) as tif:
            series = tif.series[0]
            shape = series.levels[0].shape if len(series.levels) > 1 else series.shape
        # shape is (..., H, W); last two dims are always Y and X
        return int(shape[-2]), int(shape[-1])

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
            - ``obsm['{target_name}_spatial']``: anchor spot positions in the Raman
              image's pixel coordinate system (set during alignment).
            - ``uns['spot_size']``: anchor spot dimensions in pixels.
            {sample_id: h5ad_path}
        target_files : dict[str, str]
            ASHLAR-stitched Raman OME-TIFF files.
            {sample_id: ome_tiff_path}
        anchor_name : str
            Name of the anchor (reference) modality.
        target_name : str
            Name of the Raman modality being registered.
        force_recomputing : bool
            Recompute even if cached results exist.
        step_reporter : StepReporter, optional
            Reports per-sample progress to the GUI.

        Returns
        -------
        dict[str, str]
            {sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
        """
        common_samples = sorted(set(anchor_files.keys()) & set(target_files.keys()) - {"merged"})
        registered_files: dict[str, str] = {}
        total_samples = len(common_samples)
        all_cached = True

        for sample_idx, sample_id in enumerate(common_samples, 1):
            logger.info(f"Registering '{target_name}' (raman pixel) for sample '{sample_id}'")

            if step_reporter:
                step_reporter.set_sample(sample_id, sample_idx, total_samples)

            reg_dir = os.path.join(self._path, sample_id, "registration")
            os.makedirs(reg_dir, exist_ok=True)
            registered_file = MODALITY_REGISTRATION(self._path, sample_id, target_name, "h5ad")

            anchor_adata = anndata.read_h5ad(anchor_files[sample_id])

            # Cache check: validate obs count and registration mode against the anchor to
            # detect stale caches (wrong size, or a file left by a different registration mode).
            if os.path.exists(registered_file) and not force_recomputing:
                cached = anndata.read_h5ad(registered_file)
                if registration_cache_valid(cached, anchor_adata.n_obs, self._REGISTRATION_TYPE):
                    logger.info(f"Using cached raman pixel registration for sample '{sample_id}'")
                    registered_files[sample_id] = registered_file
                    continue
                logger.warning(
                    f"Cached registration for '{sample_id}' is stale "
                    f"(obs={cached.n_obs} vs anchor {anchor_adata.n_obs}, "
                    f"type={cached.uns.get('registration_type')} vs {self._REGISTRATION_TYPE}); recomputing."
                )

            all_cached = False

            if 'spot_size' in anchor_adata.uns:
                spot_size = np.asarray(anchor_adata.uns['spot_size'], dtype=np.float32).flatten()
                if spot_size.size == 1:
                    spot_size = np.array([float(spot_size[0]), float(spot_size[0])], dtype=np.float32)
            else:
                logger.warning(f"No spot_size in anchor for sample '{sample_id}', using default [1.0, 1.0]")
                spot_size = np.array([1.0, 1.0], dtype=np.float32)

            coord_key = f'{target_name}_spatial'
            if coord_key not in anchor_adata.obsm:
                logger.error(
                    f"Anchor '{anchor_name}' sample '{sample_id}' missing obsm['{coord_key}']. "
                    f"Ensure alignment was performed. Skipping."
                )
                continue
            anchor_coords = np.asarray(anchor_adata.obsm[coord_key], dtype=np.float32)

            # Compute bounding box around anchor spots (plus half-spot margin)
            # to avoid loading the entire image when the tissue occupies a sub-region.
            half_sx = float(spot_size[0]) / 2.0
            half_sy = float(spot_size[1]) / 2.0
            margin = 2  # extra pixels to avoid clipping spots at the boundary

            H, W = self._image_dims(target_files[sample_id])
            x_min = max(0, int(np.floor(anchor_coords[:, 0].min() - half_sx)) - margin)
            x_max = min(W, int(np.ceil(anchor_coords[:, 0].max() + half_sx)) + margin)
            y_min = max(0, int(np.floor(anchor_coords[:, 1].min() - half_sy)) - margin)
            y_max = min(H, int(np.ceil(anchor_coords[:, 1].max() + half_sy)) + margin)
            bbox = (x_min, y_min, x_max, y_max)

            pixel_coords, pixel_features, channel_names = self._load_raman_pixels(
                target_files[sample_id], bbox
            )

            logger.debug(
                f"Anchor: {anchor_coords.shape[0]} spots, spot_size={spot_size}. "
                f"Raman bbox {bbox}: {pixel_coords.shape[0]} pixels, {pixel_features.shape[1]} channels."
            )

            registered_features = SpotInterpolationRegistration._interpolate_features(
                anchor_coordinates=anchor_coords,
                anchor_spot_size=spot_size,
                target_coordinates=pixel_coords,
                target_features=pixel_features,
            )

            adata = anndata.AnnData(
                X=registered_features,
                obsm={'spatial': anchor_coords.copy()},
                obs={'sample_id': [sample_id] * anchor_coords.shape[0]},
                var=pd.DataFrame(index=channel_names),
            )
            adata.uns['registration_type'] = self._REGISTRATION_TYPE

            write_h5ad_compat(adata, registered_file, compression=_H5AD_COMPRESSION)
            registered_files[sample_id] = registered_file
            logger.debug(f"Saved raman pixel registration for '{sample_id}': {registered_features.shape}")

            del anchor_adata, pixel_coords, pixel_features

        registered_files = self._merge_samples(
            registered_files, target_name,
            force_recomputing=force_recomputing, all_per_sample_cached=all_cached,
        )
        return registered_files

    def _merge_samples(
        self,
        registered_files: dict[str, str],
        modality_name: str,
        force_recomputing: bool = False,
        all_per_sample_cached: bool = False,
    ) -> dict[str, str]:
        """Merge per-sample registration files into a single concatenated AnnData."""
        sample_files = {k: v for k, v in registered_files.items() if k != "merged"}
        if not sample_files:
            return registered_files

        merge_dir = os.path.join(self._path, "merged", "registration")
        os.makedirs(merge_dir, exist_ok=True)
        merged_file = MODALITY_REGISTRATION_MERGED(self._path, modality_name, "h5ad")

        if os.path.exists(merged_file) and not force_recomputing and all_per_sample_cached:
            active_ids = set(sample_files.keys())
            merged_ids = read_merged_sample_ids(merged_file)
            if merged_ids == active_ids:
                logger.info(f"Using cached merged raman pixel registration for '{modality_name}'")
                registered_files["merged"] = merged_file
                return registered_files

        logger.info(f"Merging raman pixel registration files for '{modality_name}'")
        adata_list = []
        for sample_id, filepath in sample_files.items():
            adata = anndata.read_h5ad(filepath)
            adata.obs_names = [f"{sample_id}_{i}" for i in range(adata.n_obs)]
            adata_list.append(adata)

        merged = anndata.concat(adata_list, merge='same')
        merged.uns['registration_type'] = self._REGISTRATION_TYPE
        write_h5ad_compat(merged, merged_file, compression=_H5AD_COMPRESSION)
        registered_files["merged"] = merged_file
        return registered_files
