"""Coordinate sample alignment, cache reuse, browser waits and output persistence."""

from focus.alignment.validation import validate_inputs

from focus.alignment.result import parse_alignment_result
from typing import cast
from collections.abc import Callable
from typing import Unpack, TypedDict
from focus.runtime.reporting import StepReporter
from focus.alignment.types import AlignmentResult

from functools import cached_property

from focus.alignment.inputs import AlignmentInputs
from focus.alignment.cache import AlignmentCache
from focus.alignment.writer import AlignmentWriter
import os
import threading
import numpy as np

from focus.io.paths import merged_alignment_file
from focus.io.anndata import (
    concat_on_disk_compat,
    read_merged_sample_ids,
)
from focus.gui.alignment import DirectMappingAlignmentGUI
from focus.runtime.reporter_context import get_reporter
from focus.runtime.cancellation import PipelineCancelled
from focus.alignment.settings import (
    _IMAGE_MODALITIES,
    _SPOT_MODALITIES,
    _STEP_ALIGN,
    _STEP_MERGE,
    _STEP_SAVE,
    logger,
)
from focus.alignment.preview import _first


class AlignmentThreadOptions(TypedDict, total=False):
    """Optional recomputation flag and completion hook for the alignment worker."""

    force_recomputing: bool
    on_gui_done: Callable[[], None] | None


class DirectMappingAligner:
    """
    Align two modalities using interactive coordinate mapping via a web GUI.

    Supports all modality combinations:
    - IMAGE ↔ IMAGE: crops reference to aligned bounding box (output: OME-TIFF)
    - IMAGE → SPOT: stores aligned coordinates in AnnData obsm (output: H5AD)
    - SPOT → SPOT: stores aligned coordinates in AnnData obsm (output: H5AD)

    For image-based modalities, the lowest pyramid level is displayed in the GUI,
    but aligned coordinates are scaled back to full resolution for the output.
    """

    _spot_basis: list[list[float]] | None
    _warned_offset: bool

    def __init__(
        self,
        path: str,
        reference_modality: dict[str, str],
        target_modality: dict[str, str],
        reference_modality_name: str,
        target_modality_name: str,
        reference_modality_type: str,
        target_modality_type: str,
        exact_spots: bool = False,
        reporter: StepReporter | None = None,
    ) -> None:
        """Validate modality inputs and create ordered alignment state and its GUI.

        Parameters
        ----------
        path : str
            Dataset root for aligned files.
        reference_modality, target_modality : dict[str, str]
            Sample-to-source-path maps, retained by reference; merged entries are excluded
            from the sorted sample intersection.
        reference_modality_name, target_modality_name : str
            Names used in payloads, paths and aligned coordinate keys.
        reference_modality_type, target_modality_type : str
            Supported modality types controlling payload and output representations.
        exact_spots : bool
            Include all native spots in the browser payload when requested.
        reporter : StepReporter, optional
            Existing reporter; otherwise use the current context reporter.

        Raises
        ------
        TypeError
            If roots/maps/names fail the original constructor checks.
        ValueError
            If a modality type is unsupported."""
        validate_inputs(
            path,
            reference_modality,
            target_modality,
            reference_modality_name,
            target_modality_name,
            reference_modality_type,
            target_modality_type,
        )

        self._path = path
        self._reference_modality = reference_modality
        self._target_modality = target_modality
        self._reference_modality_name = reference_modality_name
        self._target_modality_name = target_modality_name
        self._reference_modality_type = reference_modality_type
        self._target_modality_type = target_modality_type
        self._exact_spots = exact_spots
        # Steps: 1/3 Aligning samples, 2/3 Saving aligned coordinates, 3/3 Merging aligned samples.
        self._reporter = reporter or get_reporter()

        # Only align samples present in both modalities
        common = set(reference_modality.keys()) & set(target_modality.keys())
        common.discard("merged")
        self._common_samples = sorted(common)

        self._dataset_completed_event = threading.Event()
        self._aligned_coordinates: dict[str, np.ndarray] = {}
        self._gui_interface = DirectMappingAlignmentGUI(
            dataset_size=len(self._common_samples),
            dataset_completed_event=self._dataset_completed_event,
        )

    def _align_sample(self, sample_index: int, sample_id: str) -> None:
        """Prepare one GUI payload, await confirmation and store full-resolution coordinates.

        Parameters
        ----------
        sample_index : int
            Zero-based position; the GUI receives the original one-based display index.
        sample_id : str
            Shared modality identifier. Source arrays remain unmodified; the parsed
            coordinates are scaled in place and copied into this aligner's result map.
        """
        # Prepare data for both modalities
        ref_metadata, ref_payload, _, ref_scale_factors = (
            self.inputs._prepare_modality_data(
                self._reference_modality[sample_id],
                self._reference_modality_name,
                self._reference_modality_type,
            )
        )
        tgt_metadata, tgt_payload, tgt_full_coords, _ = (
            self.inputs._prepare_modality_data(
                self._target_modality[sample_id],
                self._target_modality_name,
                self._target_modality_type,
            )
        )

        # Launch GUI for this sample (blocks until user confirms).
        # Only the display payload is sent to the browser; full coordinates stay here.
        alignment_result = self._gui_interface.align_sample(
            sample_id=sample_id,
            sample_index=sample_index + 1,
            reference_metadata=ref_metadata,
            target_metadata=tgt_metadata,
            reference_payload=ref_payload,
            target_payload=tgt_payload,
        )

        # Parse alignment result; pass full coordinates so the transform is applied
        # to every spot in the original dataset, not just the displayed subset.
        aligned_coordinates = parse_alignment_result(
            cast(AlignmentResult, alignment_result),
            tgt_payload,
            tgt_full_coords,
        )

        if aligned_coordinates is not None:
            # Scale coordinates from display resolution to original resolution
            # aligned_coordinates: (N, 2) where col 0 = x, col 1 = y
            # ref_scale_factors: [y_scale, x_scale]
            aligned_coordinates[:, 0] *= ref_scale_factors[1]  # x
            aligned_coordinates[:, 1] *= ref_scale_factors[0]  # y
            self._aligned_coordinates[sample_id] = aligned_coordinates.copy()

    # --- Alignment Thread ---

    def _align_dataset_thread(self, **kwargs: Unpack[AlignmentThreadOptions]) -> None:
        """Prepare samples and coordinate browser confirmations in sample order."""
        try:
            force_recomputing = kwargs.get("force_recomputing", False)

            samples = self._reporter.tqdm(
                self._common_samples, *_STEP_ALIGN, unit="sample"
            )
            for sample_index, sample_id in enumerate(samples):
                if not force_recomputing and self.cache._cache_matches(sample_id):
                    self._reporter.cached()
                    continue

                self._align_sample(sample_index, sample_id)

        except PipelineCancelled:
            pass  # interrupted: the pipeline thread raises it itself once the GUI is closed
        except Exception as e:
            logger.debug("Alignment thread error", exc_info=True)
            self._reporter.warning(f"Alignment failed: {e}")
            self._gui_interface.set_error(str(e))
        finally:
            self._dataset_completed_event.set()

    # --- Public API ---

    def uniform_aligned_dataset(
        self, force_recomputing: bool = False
    ) -> dict[str, str]:
        """
        Produce an aligned dataset without GUI interaction (passthrough alignment).
        Used when the target modality's coordinates are already expressed in the
        reference modality's coordinate system (e.g. 10x Visium spots on H&E).

        For an image reference, the coordinates are in the source image's pixel frame: the
        preprocessed image's crop offset is subtracted so they land on the preprocessed pixels.

        The obsm key for this pair is added to the shared aligned file for the target
        modality, preserving any obsm keys added by previous alignment passes.

        Returns
        -------
        dict[str, str]
                Maps sample IDs (and "merged") to aligned file paths.
        """
        aligned_samples: dict[str, str] = {}
        changed = False
        obsm_key = f"{self._reference_modality_name}_spatial"

        per_sample = [
            (sid, f) for sid, f in self._target_modality.items() if sid != "merged"
        ]
        for sample_id, processed_target_file in self._reporter.tqdm(
            per_sample, *_STEP_ALIGN, unit="sample", id_of=_first
        ):
            alignment_folder = os.path.join(self._path, sample_id, "alignment")
            os.makedirs(alignment_folder, exist_ok=True)
            aligned_file = self.cache._aligned_output_path(sample_id)

            needs_write = force_recomputing or not self.cache._cache_matches(
                sample_id, pre_aligned=True
            )
            if not needs_write:
                self._reporter.cached()
            else:
                self.writer._write_uniform_sample(sample_id, aligned_file, obsm_key)
                changed = True

        return self.writer._merge_uniform(aligned_samples, force_recomputing, changed)

    def is_alignment_needed(self, force_recomputing: bool = False) -> bool:
        """Return True if at least one sample still needs to be aligned."""
        if force_recomputing:
            return len(self._common_samples) > 0
        return any(
            not self.cache._cache_matches(sample_id)
            for sample_id in self._common_samples
        )

    def needs_merged_build(self) -> bool:
        """Return True if per-sample files exist but the merged file is absent."""
        is_target_image = self._target_modality_type in _IMAGE_MODALITIES
        if is_target_image:
            return False
        merged_file = merged_alignment_file(
            self._path, self._target_modality_name, "h5ad"
        )
        if os.path.exists(merged_file):
            return False
        for sample_id in self._common_samples:
            if os.path.exists(self.cache._aligned_output_path(sample_id)):
                return True
        return False

    def collect_aligned_files(self) -> dict[str, str]:
        """Return paths to already-aligned files without starting the GUI.

        Creates the merged file from per-sample files if it is missing.
        """
        is_target_image = self._target_modality_type in _IMAGE_MODALITIES
        aligned_samples: dict[str, str] = {}
        self._reporter.step(*_STEP_ALIGN, cached=True)
        self._reporter.step(*_STEP_SAVE, cached=True)
        aligned_files = []
        for sample_id in self._common_samples:
            aligned_file = self.cache._aligned_output_path(sample_id)
            if os.path.exists(aligned_file):
                aligned_samples[sample_id] = aligned_file
                if not is_target_image:
                    aligned_files.append(aligned_file)
        if aligned_files:
            merged_file = merged_alignment_file(
                self._path, self._target_modality_name, "h5ad"
            )
            active_ids = set(self._common_samples)
            merged_ids = (
                read_merged_sample_ids(merged_file)
                if os.path.exists(merged_file)
                else None
            )
            self._reporter.step(*_STEP_MERGE, cached=merged_ids == active_ids)
            if merged_ids != active_ids:
                alignment_folder = os.path.join(self._path, "merged", "alignment")
                os.makedirs(alignment_folder, exist_ok=True)
                concat_on_disk_compat(
                    aligned_files, merged_file, merge="same", uns_merge="same"
                )
            aligned_samples["merged"] = merged_file
        return aligned_samples

    def align_dataset(
        self,
        force_recomputing: bool = False,
        on_gui_done: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        """
        Align the target modality to the reference using the interactive GUI.

        Starts the alignment GUI, processes all samples, then saves results.

        Parameters
        ----------
        force_recomputing : bool
                If True, re-run alignment even if cached results exist.
        on_gui_done : callable, optional
                Called immediately after the alignment GUI server shuts down and
                before file saving begins.  Use this to emit a progress update so
                callers know the pipeline is still running while files are written.

        Returns
        -------
        dict[str, str]
                Maps sample IDs (and "merged") to aligned file paths.
        """
        aligned_samples: dict[str, str] = {}

        if not self._common_samples:
            return aligned_samples

        # Start alignment in background thread
        alignment_thread = threading.Thread(
            name="Align Dataset Thread",
            target=self._align_dataset_thread,
            kwargs={"force_recomputing": force_recomputing},
            daemon=True,
        )
        alignment_thread.start()

        # Block until GUI completes; an interrupt of the run closes the GUI at once
        self._reporter.on_cancel(self._gui_interface.cancel)
        try:
            self._gui_interface.enable_gui()
        finally:
            self._reporter.off_cancel(self._gui_interface.cancel)
        self._reporter.check_cancelled()

        # Notify caller that the GUI is done and file saving is about to start
        if on_gui_done is not None:
            on_gui_done()

        # Determine combination and save
        is_ref_image = self._reference_modality_type in _IMAGE_MODALITIES
        is_ref_spot = self._reference_modality_type in _SPOT_MODALITIES
        is_target_image = self._target_modality_type in _IMAGE_MODALITIES
        is_target_spot = self._target_modality_type in _SPOT_MODALITIES

        if is_ref_image and is_target_image:
            aligned_samples = self.writer._save_image_to_image(aligned_samples)

        elif (is_ref_image and is_target_spot) or (is_ref_spot and is_target_spot):
            aligned_samples = self.writer._save_spot_alignment(aligned_samples)

        elif is_ref_spot and is_target_image:
            # Unreachable through the CLI/GUI: config validation rejects an image-based
            # reference paired with a spot-based modality before the pipeline starts.
            self._reporter.warning(
                f"Aligning image reference '{self._target_modality_name}' to spot modality "
                f"'{self._reference_modality_name}' is not supported; no aligned output was written."
            )

        return aligned_samples

    @cached_property
    def inputs(self) -> AlignmentInputs:
        """Return the AlignmentInputs component bound to this aligner."""
        return AlignmentInputs(self)

    @cached_property
    def cache(self) -> AlignmentCache:
        """Return the AlignmentCache component bound to this aligner."""
        return AlignmentCache(self)

    @cached_property
    def writer(self) -> AlignmentWriter:
        """Return the AlignmentWriter component bound to this aligner."""
        return AlignmentWriter(self)
