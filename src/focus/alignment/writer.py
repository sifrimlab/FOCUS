"""Write aligned image/spot outputs and rebuild their modality merges."""

from __future__ import annotations
from focus.alignment.settings import _IMAGE_MODALITIES
from focus.alignment.preview import _first
from focus.alignment.settings import _H5AD_COMPRESSION
from focus.alignment.settings import _STEP_MERGE
from focus.alignment.settings import _STEP_SAVE
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.alignment.coordinator import DirectMappingAligner

import os
import tifffile
import numpy as np

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
        modality's name (see ``_aligned_output_path``).
        """
        for sample_id, aligned_coords in self.aligner._reporter.tqdm(
            list(self.aligner._aligned_coordinates.items()),
            *_STEP_SAVE,
            unit="sample",
            id_of=_first,
        ):
            reference_file = self.aligner._reference_modality[sample_id]

            min_x, max_x = (
                int(np.nanmin(aligned_coords[:, 0])),
                int(np.nanmax(aligned_coords[:, 0])),
            )
            min_y, max_y = (
                int(np.nanmin(aligned_coords[:, 1])),
                int(np.nanmax(aligned_coords[:, 1])),
            )

            with tifffile.TiffFile(reference_file) as tif:
                series = tif.series[0]
                img_shape = series.shape
                h, w = img_shape[-2], img_shape[-1]

                min_x, min_y = max(0, min_x), max(0, min_y)
                max_x, max_y = min(w, max_x), min(h, max_y)

                if min_x >= max_x or min_y >= max_y:
                    self.aligner._reporter.warning(
                        "Invalid crop: the aligned layers do not overlap; no output written"
                    )
                    continue

                slices = [slice(None)] * len(img_shape)
                slices[-2] = slice(min_y, max_y)
                slices[-1] = slice(min_x, max_x)
                crop_data = series.asarray()[tuple(slices)]

            alignment_folder = os.path.join(self.aligner._path, sample_id, "alignment")
            os.makedirs(alignment_folder, exist_ok=True)
            aligned_file = self.aligner.cache._aligned_output_path(sample_id)

            tifffile.imwrite(aligned_file, crop_data, compression="zlib")
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
                self.aligner._path, self.aligner._target_modality_name, "h5ad"
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
            self.aligner._path, self.aligner._target_modality_name, "h5ad"
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
