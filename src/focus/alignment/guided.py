"""
Guided alignment: align a modality to a guide instead of the reference, then reach it from the
reference through the guide.

With consecutive sections (e.g. Visium ST on one cut, MSI on the next) the operator matches the
MSI spots against the H&E of the ST section (the guide ``G``) far more easily than against the
ST spots. The reference ``R`` is already placed on ``G`` (``obsm['{G}_spatial']``, manual or
pre-aligned); the guided pair confirms a warp ``W`` from the guided modality ``T`` (moving, its
native spots) onto ``G`` (fixed). The reference spots in ``T``'s frame are then

    obsm['{T}_spatial'] = W^-1(obsm['{G}_spatial'])

computed with the exact inverse of the confirmed warp (:meth:`LandmarkWarp.inverse_full`).

The result is written into the reference's aligned file like any other pair. The confirmed
``T -> G`` warp is stored at ``uns['focus_alignment_warps'][T]`` together with the guide name and
the signatures of ``T`` and ``G``: while those still match, a changed ``R -> G`` alignment is
re-composed without reopening the alignment tool.
"""

from __future__ import annotations

import json
import os
from functools import cached_property
from typing import Any

import h5py
import numpy as np
from anndata.io import read_elem

from focus.alignment.cache import AlignmentCache
from focus.alignment.coordinator import DirectMappingAligner
from focus.alignment.settings import _H5AD_COMPRESSION, _STEP_ALIGN, _STEP_SAVE
from focus.alignment.warps import WARPS_KEY, read_stored_warp_dict
from focus.alignment.writer import AlignmentWriter
from focus.io.anndata import write_h5ad_compat
from focus.io.provenance import content_signature, fingerprint
from focus.runtime.reporting import StepReporter
from focus.spatial.warp import LandmarkWarp

_INPUTS_KEY = "focus_alignment_inputs"


class GuidedAligner(DirectMappingAligner):
    """Align ``moving`` onto ``guide`` in the GUI; store the reference's spots in ``moving``'s frame."""

    def __init__(
        self,
        path: str,
        guide_files: dict[str, str],
        moving_files: dict[str, str],
        guide_name: str,
        moving_name: str,
        guide_type: str,
        moving_type: str,
        anchor_name: str,
        anchor_files: dict[str, str],
        exact_spots: bool = False,
        reporter: StepReporter | None = None,
    ) -> None:
        """Bind the guide (fixed layer), the guided modality (moving layer) and the reference."""
        super().__init__(
            path=path,
            reference_modality=guide_files,
            target_modality=moving_files,
            reference_modality_name=guide_name,
            target_modality_name=moving_name,
            reference_modality_type=guide_type,
            target_modality_type=moving_type,
            exact_spots=exact_spots,
            reporter=reporter,
        )
        self._anchor_name = anchor_name
        self._anchor_files = anchor_files
        self._common_samples = sorted(set(self._common_samples) & set(anchor_files))
        self._gui_interface._dataset_size = len(self._common_samples)

    @property
    def _owner_name(self) -> str:
        """The reference owns the aligned file, as for every other pair."""
        return self._anchor_name

    @property
    def _owner_files(self) -> dict[str, str]:
        """Processed reference files, per sample."""
        return self._anchor_files

    @property
    def _warp_key(self) -> str:
        """The guided modality's key: the guide's key holds the reference -> guide warp."""
        return self._target_modality_name

    @cached_property
    def cache(self) -> GuidedCache:
        """Return the guided cache bound to this aligner."""
        return GuidedCache(self)

    @cached_property
    def writer(self) -> GuidedWriter:
        """Return the guided writer bound to this aligner."""
        return GuidedWriter(self)

    def _save_results(self, aligned_samples: dict[str, str]) -> dict[str, str]:
        """Compose every confirmed or stale sample into the reference's aligned file."""
        return self.writer.save(aligned_samples)

    def collect_aligned_files(self) -> dict[str, str]:
        """No sample needs the alignment tool: re-compose the stale ones, then merge."""
        self._reporter.step(*_STEP_ALIGN, cached=True)
        return self.writer.save({})


class GuidedCache(AlignmentCache):
    """Reuse decisions of a guided pair."""

    aligner: GuidedAligner

    def _signatures(self, sample_id: str) -> dict[str, object]:
        """Current identity of the guided modality and of the guide."""
        a = self.aligner
        return {
            "moving": content_signature(a._target_modality[sample_id]),
            "fixed": content_signature(a._reference_modality[sample_id]),
        }

    def _input_key(self, sample_id: str, pre_aligned: bool = False) -> str:
        """Fingerprint of the guided pair plus the reference -> guide alignment it composes with."""
        a = self.aligner
        path = self._aligned_output_path(sample_id)
        anchor = _read_string(path, f"uns/{_INPUTS_KEY}/{a._reference_modality_name}")
        return fingerprint(
            {
                "version": 1,
                "guide": a._reference_modality_name,
                **self._signatures(sample_id),
                "anchor": anchor,
            }
        )

    def stored_warp(self, sample_id: str) -> dict[str, Any] | None:
        """The stored guided warp when it was made on the current guided modality and guide."""
        a = self.aligner
        data = read_stored_warp_dict(self._aligned_output_path(sample_id), a._warp_key)
        if data is None or data.get("guide") != a._reference_modality_name:
            return None
        current = json.loads(json.dumps(self._signatures(sample_id)))
        return data if data.get("inputs") == current else None

    def _cache_matches(self, sample_id: str, pre_aligned: bool = False) -> bool:
        """True when the alignment tool is not needed: a usable guided warp is stored."""
        return self.stored_warp(sample_id) is not None

    def composition_current(self, sample_id: str) -> bool:
        """True when the stored reference -> guided coordinates match the current inputs."""
        a = self.aligner
        path = self._aligned_output_path(sample_id)
        if not os.path.exists(path):
            return False
        with h5py.File(path, "r") as f:
            if f"obsm/{a._target_modality_name}_spatial" not in f:
                return False
        stored = _read_string(path, f"uns/{_INPUTS_KEY}/{a._target_modality_name}")
        return stored == self._input_key(sample_id)


class GuidedWriter(AlignmentWriter):
    """Compose the reference's spots into the guided modality's frame and write them."""

    aligner: GuidedAligner

    def save(self, aligned_samples: dict[str, str]) -> dict[str, str]:
        """Write newly confirmed samples and re-compose stale ones, then rebuild the merge."""
        a = self.aligner
        todo = [
            s
            for s in a._common_samples
            if s in a._warps or not a.cache.composition_current(s)
        ]
        for sample_id in a._reporter.tqdm(todo, *_STEP_SAVE, unit="sample", id_of=str):
            if sample_id in a._warps:
                data = json.loads(a._warps[sample_id])
            else:
                stored = a.cache.stored_warp(sample_id)
                if stored is None:
                    continue  # never aligned (or its inputs changed): nothing to compose
                data = stored
            coords = self._compose(sample_id, LandmarkWarp.from_dict(data))
            if coords is None:
                continue
            self._write(sample_id, coords, data)
            a._aligned_coordinates[sample_id] = coords
        return self._merge_manual(aligned_samples)

    def _compose(self, sample_id: str, warp: LandmarkWarp) -> np.ndarray | None:
        """Reference spots in the guided modality's frame: the inverse warp of their guide position."""
        a = self.aligner
        path = a.cache._aligned_output_path(sample_id)
        key = f"obsm/{a._reference_modality_name}_spatial"
        if not os.path.exists(path):
            return None
        with h5py.File(path, "r") as f:
            if key not in f:
                a._reporter.warning(
                    f"'{a._anchor_name}' is not aligned to the guide "
                    f"'{a._reference_modality_name}' in sample '{sample_id}': skipped"
                )
                return None
            in_guide = np.asarray(read_elem(f[key]), dtype=np.float64)
        return warp.inverse_full(in_guide).astype(np.float32)

    def _write(self, sample_id: str, coords: np.ndarray, data: dict[str, Any]) -> None:
        """Store the composed coordinates, the pair's input key and the guided warp."""
        a = self.aligner
        name = a._target_modality_name
        path = a.cache._aligned_output_path(sample_id)
        adata = a.cache._fresh_aligned_adata(sample_id)
        adata.obsm[f"{name}_spatial"] = coords
        adata.uns.setdefault(_INPUTS_KEY, {})[name] = a.cache._input_key(sample_id)
        stored = {
            **data,
            "guide": a._reference_modality_name,
            "inputs": a.cache._signatures(sample_id),
        }
        warps = dict(adata.uns.get(WARPS_KEY, {}))
        warps[name] = json.dumps(stored, separators=(",", ":"))
        adata.uns[WARPS_KEY] = warps
        write_h5ad_compat(adata, path, compression=_H5AD_COMPRESSION)


def _read_string(path: str, key: str) -> str | None:
    """A scalar string of an h5ad, or None when the file or the key is missing."""
    if not os.path.exists(path):
        return None
    with h5py.File(path, "r") as f:
        if key not in f:
            return None
        raw = f[key][()]
    return raw.decode() if isinstance(raw, bytes) else str(raw)
