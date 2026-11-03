"""Decide alignment reuse from input provenance, geometry and existing pair keys."""

from __future__ import annotations
from focus.alignment.settings import _IMAGE_MODALITIES
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.alignment.coordinator import DirectMappingAligner

import os
import json
import h5py
import anndata
import numpy as np

from focus.io.paths import alignment_file
from focus.config.options import ModalityType
from focus.io.provenance import (
    cached_fingerprint,
    content_signature,
    fingerprint,
    parse_crop_offset,
    read_ome_annotations,
    PIXEL_DIGEST_KEY,
    SUPERSEDED_SIGNATURE_KEY,
)
from focus.runtime.reporter_context import get_reporter


class AlignmentCache:
    """Decide pair reuse and refresh aligned containers using the owning aligner's state."""

    def __init__(self, aligner: DirectMappingAligner) -> None:
        """Bind the component to its existing aligner state."""
        self.aligner = aligner

    # --- Output paths ---

    def _aligned_output_path(self, sample_id: str) -> str:
        """Path of this pair's aligned artefact.

        Single source of truth for the writers and for every cache check, so a lookup can
        never disagree with what was written.

        IMAGE reference + IMAGE target: the artefact is the fixed (class-internal
        ``reference_modality``, i.e. the pipeline's non-reference) image cropped to the
        overlap, so it carries THAT modality's name.

        SPOT reference: the artefact is the reference modality's own AnnData, which
        accumulates one ``obsm['{target}_spatial']`` key per target, so it carries the
        reference modality's name.
        """
        if (
            self.aligner._reference_modality_type in _IMAGE_MODALITIES
            and self.aligner._target_modality_type in _IMAGE_MODALITIES
        ):
            return alignment_file(
                self.aligner._path,
                sample_id,
                self.aligner._reference_modality_name,
                "ome.tiff",
            )
        return alignment_file(
            self.aligner._path, sample_id, self.aligner._target_modality_name, "h5ad"
        )

    def _fixed_annotations(self, sample_id: str) -> dict[str, str]:
        """FOCUS provenance of the fixed layer (empty unless it is an annotated FOCUS image)."""
        if self.aligner._reference_modality_type not in _IMAGE_MODALITIES:
            return {}
        return read_ome_annotations(self.aligner._reference_modality[sample_id]) or {}

    def _crop_offset(self, sample_id: str) -> np.ndarray:
        """
        Pixel offset ``(x, y)`` of the fixed image's origin in its source image.

        Preprocessing crops microscopy images to the tissue and records where the crop starts.
        A microscopy image without that record (written before it existed, reachable only when
        preprocessing is skipped) gets offset 0 with a warning.
        """
        offset = parse_crop_offset(self._fixed_annotations(sample_id))
        if offset is not None:
            return offset
        if (
            self.aligner._reference_modality_type == ModalityType.MICROSCOPY_IMAGE
            and not getattr(self.aligner, "_warned_offset", False)
        ):
            self.aligner._warned_offset = True
            (getattr(self.aligner, "_reporter", None) or get_reporter()).warning(
                f"'{self.aligner._reference_modality_name}' records no crop offset (preprocessed before it was stored): "
                f"pre-aligned coordinates are used without one"
            )
        return np.zeros(2, dtype=np.float32)

    def _input_key(self, sample_id: str, pre_aligned: bool = False) -> str:
        """
        Fingerprint of the inputs an alignment was made on.

        Each side is identified by ``content_signature``: the level-0 pixel digest of a FOCUS
        image, the preprocessing fingerprint of an AnnData, otherwise the file's stat. A
        pre-aligned image pair also depends on the image's crop offset.
        """
        payload: dict[str, object] = {
            "version": 1,
            "fixed": content_signature(self.aligner._reference_modality[sample_id]),
            "moving": content_signature(self.aligner._target_modality[sample_id]),
        }
        if pre_aligned and self.aligner._reference_modality_type in _IMAGE_MODALITIES:
            payload["offset"] = [float(v) for v in self._crop_offset(sample_id)]
        return fingerprint(payload)

    def _legacy_input_key(self, sample_id: str) -> str | None:
        """
        The key an alignment made on the file a regenerated image replaced would carry, or None.

        Preprocessing records the replaced file's stat signature only when the regenerated
        image's level-0 pixels are identical to it, so a match proves the alignment still holds.
        """
        superseded = self._fixed_annotations(sample_id).get(SUPERSEDED_SIGNATURE_KEY)
        if not superseded:
            return None
        return fingerprint(
            {
                "version": 1,
                "fixed": json.loads(superseded),
                "moving": content_signature(self.aligner._target_modality[sample_id]),
            }
        )

    def _restamp_input_key(self, path: str, key: str) -> None:
        """Replace this pair's stored input key in the aligned file, in place."""
        with h5py.File(path, "r+") as f:
            if "uns/focus_alignment_inputs" in f:
                anndata.io.write_elem(
                    f["uns/focus_alignment_inputs"],
                    self.aligner._reference_modality_name,
                    key,
                )
            else:
                anndata.io.write_elem(
                    f["uns"],
                    "focus_alignment_inputs",
                    {self.aligner._reference_modality_name: key},
                )

    def _cache_matches(self, sample_id: str, pre_aligned: bool = False) -> bool:
        """
        True when the stored alignment of ``sample_id`` still matches its inputs.

        The stored input key is compared for pre-aligned pairs, pairs involving ST, and pairs
        whose fixed layer is a FOCUS image with a pixel digest (against a fingerprinted anchor).
        A manual alignment made on an image that preprocessing later regenerated with identical
        pixels is accepted through the replaced file's signature, and its key is re-stamped.
        """
        path = self._aligned_output_path(sample_id)
        if not os.path.exists(path):
            return False
        if self.aligner._target_modality_type in _IMAGE_MODALITIES:
            return True
        name = self.aligner._reference_modality_name
        present, stored = self._stored_input_key(path, name)
        if not present:
            return False

        fixed_is_image = self.aligner._reference_modality_type in _IMAGE_MODALITIES
        annotations = self._fixed_annotations(sample_id)
        digested = PIXEL_DIGEST_KEY in annotations
        has_st = ModalityType.ST in (
            self.aligner._target_modality_type,
            self.aligner._reference_modality_type,
        )
        checked = self._requires_input_key(sample_id, pre_aligned, digested, has_st)
        if not checked:
            return True

        expected = self._input_key(sample_id, pre_aligned)
        if stored == expected:
            return True
        if not pre_aligned and fixed_is_image:
            legacy = self._legacy_input_key(sample_id)
            if legacy is not None and (
                stored == legacy or (stored is None and not has_st)
            ):
                self._restamp_input_key(path, expected)
                return True
        if digested and stored is not None and not pre_aligned:
            self._warn_changed(sample_id, name)
        return False

    def _requires_input_key(
        self,
        sample_id: str,
        pre_aligned: bool,
        digested: bool,
        has_st: bool,
    ) -> bool:
        """Evaluate input-key validation eligibility, retaining lazy target-fingerprint reads."""
        return (
            pre_aligned
            or has_st
            or (
                digested
                and cached_fingerprint(self.aligner._target_modality[sample_id])
                is not None
            )
        )

    @staticmethod
    def _stored_input_key(path: str, name: str) -> tuple[bool, str | None]:
        """Read a pair's coordinate presence and decoded key while closing the HDF5 handle."""
        with h5py.File(path, "r") as f:
            if f"obsm/{name}_spatial" not in f:
                return False, None
            key = f"uns/focus_alignment_inputs/{name}"
            stored = None
            if key in f:
                stored = f[key][()]
                stored = stored.decode() if isinstance(stored, bytes) else str(stored)

        return True, stored

    def _warn_changed(self, sample_id: str, name: str) -> None:
        """Warn once per changed sample using the shared aligner-owned warning set."""
        warned = self.aligner.__dict__.setdefault("_warned_changed", set())
        if sample_id not in warned:
            warned.add(sample_id)
            (getattr(self.aligner, "_reporter", None) or get_reporter()).warning(
                f"'{name}' changed since it was aligned: the alignment must be redone"
            )

    def _fresh_aligned_adata(self, sample_id: str) -> anndata.AnnData:
        # Always refresh counts/labels/metadata from preprocessing. Preserve other pair
        # coordinates only when observation identity and native geometry still agree.
        """Read the latest preprocessing data while retaining existing alignment coordinate keys."""
        adata = anndata.read_h5ad(self.aligner._target_modality[sample_id])
        path = self._aligned_output_path(sample_id)
        if os.path.exists(path):
            old = anndata.read_h5ad(path, backed="r")
            if (
                old.obs_names.equals(adata.obs_names)
                and "spatial" in old.obsm
                and np.array_equal(old.obsm["spatial"], adata.obsm["spatial"])
            ):
                for key, values in old.obsm.items():
                    if key.endswith("_spatial"):
                        adata.obsm[key] = values.copy()
                adata.uns["focus_alignment_inputs"] = dict(
                    old.uns.get("focus_alignment_inputs", {})
                )
            old.file.close()
        return adata
