"""Quantize and write multichannel/RGB OME pyramids with unchanged provenance metadata."""

from __future__ import annotations
from typing import cast
from ome_types.model import Pixels_DimensionOrder, PixelType, Map
from typing import TYPE_CHECKING
import focus.preprocessing.microscopy.settings as _settings

if TYPE_CHECKING:
    from focus.preprocessing.microscopy.sample import MicroscopyImage

import os
import json
import numpy as np
import cv2
import tifffile
from ome_types.model import (
    OME,
    Image,
    Pixels,
    Channel,
    TiffData,
    Plane,
    MapAnnotation,
    StructuredAnnotations,
)

from focus.runtime.reporter_context import get_reporter
from focus.io.provenance import (
    FOCUS_OME_NAMESPACE,
    PIXEL_DIGEST_KEY,
    SUPERSEDED_SIGNATURE_KEY,
    planes_digest,
    read_ome_annotations,
    stat_signature,
)


class ImagePyramid:
    """Encode one microscopy sample as an OME pyramid with pixel and source provenance."""

    def __init__(self, sample: MicroscopyImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    @staticmethod
    def _compute_pyramid_levels(H: int, W: int) -> int:
        """Compute how many pyramid levels are needed so the smallest level fits within _MAX_PYRAMID_PIXELS."""
        import math

        total = H * W
        if total <= _settings._MAX_PYRAMID_PIXELS:
            return 1
        return math.ceil(math.log(total / _settings._MAX_PYRAMID_PIXELS, 4)) + 1

    @staticmethod
    def _resolve_storage_dtype(input_dtype: np.dtype) -> np.dtype:
        """Map the source image's dtype to the dtype used to store the output OME-TIFF."""
        input_dtype = np.dtype(input_dtype)
        if np.issubdtype(input_dtype, np.floating):
            return np.dtype(np.float32)
        if input_dtype in (np.dtype(np.uint8), np.dtype(np.uint16)):
            return input_dtype
        # Uncommon integer depths (int16, uint32, ...): fall back to a safe high-precision default.
        return np.dtype(np.uint16)

    @staticmethod
    def _quantize_to_dtype(img_float01: np.ndarray, dtype: np.dtype) -> np.ndarray:
        """Convert a float32 [0, 1] array to the given storage dtype."""
        if dtype == np.dtype(np.float32):
            return img_float01.astype(np.float32)
        max_val = np.iinfo(dtype).max
        return np.clip(np.round(img_float01 * max_val), 0, max_val).astype(dtype)

    @staticmethod
    def _read_level0_planes(path: str) -> list[np.ndarray]:
        """
        Level-0 planes of a FOCUS OME-TIFF in IFD order: one (H, W, 3) array for RGB, one (H, W)
        array per channel otherwise.

        Read as the leading IFDs that share the first page's size (every coarser level is smaller),
        which does not depend on how a reader groups the IFDs into series.
        """
        planes = []
        with tifffile.TiffFile(path) as tif:
            size = tif.pages[0].shape[:2]
            for page in tif.pages:
                if page.shape[:2] != size:
                    break
                planes.append(page.asarray())
        return planes

    def _previous_output(
        self, output_file: str
    ) -> tuple[str, list[str | int] | None] | None:
        """
        ``(pixel digest, superseded signature)`` of an existing output, or None when there is none.

        An output written before FOCUS stored provenance carries no digest: its level-0 pixels are
        digested here, and the file itself (its stat signature) is what a pixel-identical
        regeneration supersedes. An annotated output passes its own superseded signature on.
        """
        if not os.path.exists(output_file):
            return None
        annotations = read_ome_annotations(output_file) or {}
        if PIXEL_DIGEST_KEY in annotations:
            superseded = annotations.get(SUPERSEDED_SIGNATURE_KEY)
            return annotations[PIXEL_DIGEST_KEY], (
                json.loads(superseded) if superseded else None
            )
        try:
            return planes_digest(self._read_level0_planes(output_file)), stat_signature(
                output_file
            )
        except Exception:
            return None

    def _save_image_pyramid(
        self,
        img: np.ndarray,
        output_file: str,
        input_dtype: np.dtype,
        pixel_size_um: np.ndarray | None = None,
        annotations: dict[str, object] | None = None,
        previous: tuple[str, list[str | int] | None] | None = None,
    ) -> str:
        """
        Saves an RGB/multi-channel image as a multi-resolution OME-TIFF containing
        multiple independent images, one for each resolution level.
        Uses separate top-level IFDs (not SubIFDs) and zlib compression,
        following the reference implementation for maximum compatibility.

        The number of levels is computed automatically so the smallest level
        does not exceed _MAX_PYRAMID_PIXELS total pixels.

        Pixel data is generated and resampled in float32 for quality, then quantized
        to the resolved storage dtype (matching the source file's bit depth) only at
        write time, with the TIFF predictor matched to that dtype.

        The OME-XML records the pixel size of every level (when known) and a FOCUS
        MapAnnotation holding ``annotations`` plus the digest of the level-0 planes as
        written. The file is written to a temporary sibling and renamed into place.

        Parameters
        ----------
        img : np.ndarray
                The input image as a NumPy array of shape (H, W, C) or (H, W) and dtype float32.
        output_file : str
                The path to the output OME-TIFF file.
        input_dtype : np.dtype
                The dtype of the original source image, used to pick the output storage dtype.
        pixel_size_um : np.ndarray, optional
                Level-0 ``[x, y]`` pixel size in µm; coarser levels scale it by the measured size ratio.
        annotations : dict, optional
                FOCUS provenance key/values for the MapAnnotation.
        previous : tuple, optional
                ``(pixel digest, superseded signature)`` of the output being replaced. When the new
                level-0 pixels are identical, the superseded signature is recorded so alignments made
                on the replaced file stay valid.
        """
        assert img.dtype == np.float32, "Expecting float32 array"

        storage_dtype = self._resolve_storage_dtype(input_dtype)
        predictor = 3 if storage_dtype == np.dtype(np.float32) else 2
        ome_type = _settings._OME_PIXEL_TYPE[storage_dtype]

        # Normalize to [H,W,C] shape
        if img.ndim == 2:
            img = img[..., np.newaxis]  # Grayscale -> [H,W,1]
        H_base, W_base, C = img.shape
        levels = self._compute_pyramid_levels(H_base, W_base)
        is_rgb = C == 3

        # 1. Generate pyramid (resize preserves shape type)
        pyramid_data = []
        for i in range(levels):
            scale = 0.5**i
            h_scaled = max(1, int(H_base * scale))
            w_scaled = max(1, int(W_base * scale))
            resized = cv2.resize(
                img, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA
            )
            # Re-add channel axis if cv2.resize squeezed it for 1-channel images
            if resized.ndim == 2:
                resized = resized[..., np.newaxis]
            pyramid_data.append(resized)

        # Level-0 planes exactly as written (IFD order), digested for downstream caches.
        if is_rgb:
            planes0 = [self._quantize_to_dtype(pyramid_data[0], storage_dtype)]
        else:
            planes0 = [
                self._quantize_to_dtype(pyramid_data[0][:, :, c], storage_dtype)
                for c in range(C)
            ]
        digest = planes_digest(planes0)
        annotations = dict(annotations or {})
        annotations[PIXEL_DIGEST_KEY] = digest
        if previous is not None:
            reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            previous_digest, superseded = previous
            if previous_digest == digest:
                if superseded:
                    annotations[SUPERSEDED_SIGNATURE_KEY] = json.dumps(superseded)
                reporter.detail("Pixels identical to the previous output")
            else:
                reporter.warning(
                    "The regenerated image differs from the previous one: alignments made on it must be redone"
                )

        # 2. Build OME-XML
        ome = OME()
        ifd_counter = 0
        for i, level_img in enumerate(pyramid_data):
            H, W, CC = level_img.shape
            physical = {}
            if pixel_size_um is not None:
                physical = dict(
                    physical_size_x=float(pixel_size_um[0]) * W_base / W,
                    physical_size_x_unit="µm",
                    physical_size_y=float(pixel_size_um[1]) * H_base / H,
                    physical_size_y_unit="µm",
                )

            if is_rgb:
                # RGB interleaved
                image_block = Image(
                    id=f"Image:{i}",
                    name=f"ResolutionLevel_{i}",
                    pixels=Pixels(
                        id=f"Pixels:{i}",
                        dimension_order=cast(Pixels_DimensionOrder, "XYCZT"),
                        type=cast(PixelType, ome_type),
                        size_x=W,
                        size_y=H,
                        size_z=1,
                        size_c=1,
                        size_t=1,
                        interleaved=True,
                        channels=[
                            Channel(
                                id=f"Channel:{i}:0", name="RGB", samples_per_pixel=3
                            )
                        ],
                        planes=[Plane(the_c=0, the_z=0, the_t=0)],
                        tiff_data_blocks=[TiffData(ifd=ifd_counter, plane_count=1)],
                        **physical,
                    ),
                )
                ifd_counter += 1
            else:
                # Multi/single-channel separate planes
                image_block = Image(
                    id=f"Image:{i}",
                    name=f"ResolutionLevel_{i}",
                    pixels=Pixels(
                        id=f"Pixels:{i}",
                        dimension_order=cast(Pixels_DimensionOrder, "XYCZT"),
                        type=cast(PixelType, ome_type),
                        size_x=W,
                        size_y=H,
                        size_z=1,
                        size_c=CC,
                        size_t=1,
                        channels=[
                            Channel(id=f"Channel:{i}:{c}", name=f"Channel_{c}")
                            for c in range(CC)
                        ],
                        planes=[Plane(the_c=c, the_z=0, the_t=0) for c in range(CC)],
                        tiff_data_blocks=[
                            TiffData(ifd=ifd_counter + c, plane_count=1)
                            for c in range(CC)
                        ],
                        **physical,
                    ),
                )
                ifd_counter += CC
            ome.images.append(image_block)

        ome.structured_annotations = StructuredAnnotations(
            map_annotations=[
                MapAnnotation(
                    id="Annotation:0",
                    namespace=FOCUS_OME_NAMESPACE,
                    value=cast(
                        Map,
                        {
                            "ms": [
                                {"k": k, "value": str(v)}
                                for k, v in annotations.items()
                            ]
                        },
                    ),
                )
            ]
        )
        # TIFF strings must be 7-bit ASCII: non-ASCII characters (the "µ" of µm) become XML
        # character references, which every XML parser reads back unchanged.
        xml_metadata = ome.to_xml().encode("ascii", "xmlcharrefreplace").decode("ascii")

        # 3. Write TIFF to a temporary sibling, then rename into place: a crash mid-write never
        # leaves a truncated file behind.
        head, tail = os.path.split(output_file)
        tmp_file = os.path.join(head, f".{os.getpid()}.tmp.{tail}")
        try:
            with tifffile.TiffWriter(tmp_file, bigtiff=True) as tif:
                for c, level_img in enumerate(pyramid_data):
                    if is_rgb:
                        description = xml_metadata if c == 0 else None
                        data = (
                            planes0[0]
                            if c == 0
                            else self._quantize_to_dtype(level_img, storage_dtype)
                        )
                        tif.write(
                            data,
                            description=description,
                            photometric="rgb",
                            metadata={"axes": "YXC"},
                            compression="zlib",
                            predictor=predictor,
                        )
                    else:
                        # Write each channel slice separately as minisblack
                        for ch_idx in range(C):
                            data = (
                                planes0[ch_idx]
                                if c == 0
                                else self._quantize_to_dtype(
                                    level_img[:, :, ch_idx], storage_dtype
                                )
                            )
                            description = (
                                xml_metadata if c == 0 and ch_idx == 0 else None
                            )
                            tif.write(
                                data,
                                description=description,
                                photometric="minisblack",
                                metadata={"axes": "YX"},
                                compression="zlib",
                                predictor=predictor,
                            )
            os.replace(tmp_file, output_file)
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

        return output_file
