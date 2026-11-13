"""Write aligned image/spot outputs and rebuild their modality merges."""

from __future__ import annotations
from focus.alignment.settings import _IMAGE_MODALITIES
from focus.alignment.preview import _first
from focus.alignment.settings import _H5AD_COMPRESSION
from focus.alignment.settings import _STEP_MERGE
from focus.alignment.settings import _STEP_SAVE
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from focus.alignment.coordinator import DirectMappingAligner

import os
import tifffile
import numpy as np
import zarr

from focus.io.ome import read_channel_names, read_ome_physical_size_um
from focus.alignment.warps import WARPS_KEY
from focus.io.paths import merged_alignment_file
from focus.io.anndata import write_h5ad_compat, concat_on_disk_compat


class AlignmentWriter:
    """Persist one aligner's image crops, mapped spot coordinates and modality merges."""

    def __init__(self, aligner: DirectMappingAligner) -> None:
        """Bind the component to its existing aligner state."""
        self.aligner = aligner

    # --- Save Results ---

    def _save_image_to_image(self, aligned_samples: dict[str, str]) -> dict[str, str]:
        """IMAGE → IMAGE: crop the fixed image to the aligned bounding box, save as OME-TIFF.

        The bounding box comes from the moving layer's mapped corners, so the crop keeps the
        region of the fixed image that the moving image covers. The output carries the fixed
        modality's name (see ``_aligned_output_path``), its axes, pixel size and channel names.
        """
        for sample_id, aligned_coords in self.aligner._reporter.tqdm(
            list(self.aligner._aligned_coordinates.items()),
            *_STEP_SAVE,
            unit="sample",
            id_of=_first,
        ):
            reference_file = self.aligner._reference_modality[sample_id]
            crop = _crop_image(reference_file, aligned_coords)
            if crop is None:
                self.aligner._reporter.warning(
                    "Invalid crop: the aligned layers do not overlap; no output written"
                )
                continue
            alignment_folder = os.path.join(self.aligner._path, sample_id, "alignment")
            os.makedirs(alignment_folder, exist_ok=True)
            aligned_file = self.aligner.cache._aligned_output_path(sample_id)
            _write_crop(aligned_file, reference_file, *crop)
            aligned_samples[sample_id] = aligned_file

        return aligned_samples

    def _save_spot_alignment(self, aligned_samples: dict[str, str]) -> dict[str, str]:
        """IMAGE→SPOT or SPOT→SPOT: store aligned coords in AnnData, save with compression.

        The aligned file accumulates obsm keys across multiple alignment passes (one per
        modality pair). If an aligned file already exists for this target, it is loaded
        and updated rather than recreated from scratch, so previously added obsm keys are
        preserved.
        """
        for sample_id, aligned_coords in self.aligner._reporter.tqdm(
            list(self.aligner._aligned_coordinates.items()),
            *_STEP_SAVE,
            unit="sample",
            id_of=_first,
        ):
            alignment_folder = os.path.join(self.aligner._path, sample_id, "alignment")
            os.makedirs(alignment_folder, exist_ok=True)
            aligned_file = self.aligner.cache._aligned_output_path(sample_id)

            adata = self.aligner.cache._fresh_aligned_adata(sample_id)

            adata.obsm[f"{self.aligner._reference_modality_name}_spatial"] = (
                aligned_coords.astype(np.float32)
            )
            adata.uns.setdefault("focus_alignment_inputs", {})[
                self.aligner._reference_modality_name
            ] = self.aligner.cache._input_key(sample_id)
            _store_warp(
                adata.uns,
                self.aligner._reference_modality_name,
                self.aligner._warps.get(sample_id),
            )
            write_h5ad_compat(adata, aligned_file, compression=_H5AD_COMPRESSION)
            del adata

            aligned_samples[sample_id] = aligned_file

        return self._merge_manual(aligned_samples)

    def _merge_manual(self, aligned_samples: dict[str, str]) -> dict[str, str]:
        """Merge available manually aligned files when newly confirmed coordinates require it."""
        # Generate merged aligned dataset
        aligned_files = []
        for sample_id in self.aligner._common_samples:
            aligned_file = self.aligner.cache._aligned_output_path(sample_id)
            if os.path.exists(aligned_file):
                aligned_files.append(aligned_file)
                aligned_samples[sample_id] = aligned_file

        if aligned_files:
            merged_file = merged_alignment_file(
                self.aligner._path, self.aligner._owner_name, "h5ad"
            )
            rebuild = not os.path.exists(merged_file) or bool(
                self.aligner._aligned_coordinates
            )
            self.aligner._reporter.step(*_STEP_MERGE, cached=not rebuild)
            if rebuild:
                alignment_folder = os.path.join(
                    self.aligner._path, "merged", "alignment"
                )
                os.makedirs(alignment_folder, exist_ok=True)
                concat_on_disk_compat(
                    aligned_files, merged_file, merge="same", uns_merge="same"
                )
            aligned_samples["merged"] = merged_file

        return aligned_samples

    def _write_uniform_sample(
        self, sample_id: str, aligned_file: str, obsm_key: str
    ) -> None:
        """Refresh a pre-aligned sample and stamp its pair key, adjusting source-pixel crop offset."""
        adata = self.aligner.cache._fresh_aligned_adata(sample_id)
        coords = adata.obsm["spatial"].copy()
        if self.aligner._reference_modality_type in _IMAGE_MODALITIES:
            # Pre-aligned coordinates are in the source image's pixel frame; the preprocessed
            # image starts where preprocessing cropped it.
            coords = (
                np.asarray(coords, dtype=np.float32)
                - self.aligner.cache._crop_offset(sample_id)
            ).astype(np.float32)
        adata.obsm[obsm_key] = coords
        adata.uns.setdefault("focus_alignment_inputs", {})[
            self.aligner._reference_modality_name
        ] = self.aligner.cache._input_key(sample_id, pre_aligned=True)
        _store_warp(adata.uns, self.aligner._reference_modality_name, None)
        write_h5ad_compat(adata, aligned_file, compression=_H5AD_COMPRESSION)
        del adata

    def _merge_uniform(
        self,
        aligned_samples: dict[str, str],
        force_recomputing: bool,
        changed: bool,
    ) -> dict[str, str]:
        """Collect existing pre-aligned outputs and rebuild their merge under the original cache rule."""
        # Build merged dataset
        aligned_files = []
        for sample_id in self.aligner._common_samples:
            aligned_file = self.aligner.cache._aligned_output_path(sample_id)
            if os.path.exists(aligned_file):
                aligned_files.append(aligned_file)
            aligned_samples[sample_id] = aligned_file

        merged_file = merged_alignment_file(
            self.aligner._path, self.aligner._owner_name, "h5ad"
        )
        aligned_samples["merged"] = merged_file

        rebuild = not os.path.exists(merged_file) or force_recomputing or changed
        self.aligner._reporter.step(*_STEP_MERGE, cached=not rebuild)
        if rebuild:
            alignment_folder = os.path.join(self.aligner._path, "merged", "alignment")
            os.makedirs(alignment_folder, exist_ok=True)
            concat_on_disk_compat(
                aligned_files, merged_file, merge="same", uns_merge="same"
            )

        return aligned_samples


def _crop_image(path: str, coords: np.ndarray) -> tuple[np.ndarray, str, bool] | None:
    """``(pixels, axes, rgb)`` of the bounding box of ``coords`` in the image, or None if empty.

    Y and X are located by the series axes (RGB is YXS, channels are CYX); only the crop is read.
    """
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        axes = series.axes.upper()
        yi, xi = axes.index("Y"), axes.index("X")
        h, w = series.shape[yi], series.shape[xi]
        min_x, max_x = (
            max(0, int(np.nanmin(coords[:, 0]))),
            min(w, int(np.nanmax(coords[:, 0]))),
        )
        min_y, max_y = (
            max(0, int(np.nanmin(coords[:, 1]))),
            min(h, int(np.nanmax(coords[:, 1]))),
        )
        if min_x >= max_x or min_y >= max_y:
            return None
        slices = [slice(None)] * len(series.shape)
        slices[yi] = slice(min_y, max_y)
        slices[xi] = slice(min_x, max_x)
        data = zarr.open_array(series.aszarr(level=0), mode="r")[tuple(slices)]
        rgb = (
            cast(tifffile.TiffPage, tif.pages[0]).photometric
            == tifffile.PHOTOMETRIC.RGB
        )
    return np.asarray(data), axes, rgb


def _write_crop(path: str, source: str, data: np.ndarray, axes: str, rgb: bool) -> None:
    """Tiled OME-TIFF of a crop with the source's axes, pixel size and channel names."""
    metadata: dict[str, object] = {"axes": axes}
    pixel_size = read_ome_physical_size_um(source)
    if pixel_size is not None:
        metadata.update(
            PhysicalSizeX=float(pixel_size[0]), PhysicalSizeY=float(pixel_size[1])
        )
    names = read_channel_names(source)
    if names and not rgb:
        metadata["Channel"] = {"Name": names}
    tifffile.imwrite(
        path,
        data,
        ome=True,
        metadata=metadata,
        photometric="rgb" if rgb else "minisblack",
        tile=(512, 512),
        compression="zlib",
    )


def _store_warp(uns: dict[str, Any], fixed_name: str, warp_json: str | None) -> None:
    """Record (or, for a pair without one, drop) the confirmed warp of ``fixed_name``."""
    warps = dict(uns.get(WARPS_KEY, {}))
    if warp_json is None:
        warps.pop(fixed_name, None)
    else:
        warps[fixed_name] = warp_json
    if warps:
        uns[WARPS_KEY] = warps
    else:
        uns.pop(WARPS_KEY, None)
