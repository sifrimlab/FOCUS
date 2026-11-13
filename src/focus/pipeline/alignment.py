"""Coordinate manual and pre-aligned modality pairs and their output paths."""

from focus.config.types import ModalityConfig

from focus.pipeline.types import ModalityFiles

from focus.config.types import PipelineConfig


from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.options import AlignmentStrategy
from focus.runtime.resources import release_memory
from focus.runtime.reporting import StepReporter
from focus.alignment.coordinator import DirectMappingAligner
from focus.alignment.guided import GuidedAligner
from focus.pipeline.context import _get_reference_modality, _is_microgrid_experiment


def _run_alignment(
    config: PipelineConfig,
    modality_files: ModalityFiles,
    reporter: StepReporter,
    force_overrides: dict[str, bool] | None = None,
) -> ModalityFiles:
    """
    Align the reference modality into each non-reference modality's coordinate system.

    The reference modality is the "moving target" and each non-reference modality is the
    "fixed frame". After alignment, the reference modality's AnnData accumulates one
    obsm key per non-reference modality: obsm['{mod_name}_spatial'] contains the reference
    spots expressed in that modality's coordinate system.

    A modality with an ``alignment_guide`` is instead aligned (as the moving layer) onto its
    guide, and the reference reaches it through its own alignment to the guide; guides are
    therefore aligned first.

    Returns
    -------
    dict
            {non_ref_modality_name: {sample_id: aligned_ref_file_path}}
            Each value points to the shared aligned reference file for that sample, which
            contains obsm['{mod_name}_spatial'] needed for registration.
    """

    return AlignmentStage(config, modality_files, reporter, force_overrides).run()


class AlignmentStage:
    """Capture pair metadata and coordinate one aligner at a time."""

    def __init__(
        self,
        config: PipelineConfig,
        files: ModalityFiles,
        reporter: StepReporter,
        force: dict[str, bool] | None,
    ) -> None:
        """Read reference settings before emitting any alignment progress callbacks."""
        self.path = config[ConfigParameters.DATASET_PATH]
        self.modalities = config[ConfigParameters.MODALITIES]
        self.reference = config[ConfigParameters.REFERENCE_MODALITY]
        reference_modality = _get_reference_modality(config)
        self.reference_type = reference_modality[ModalityParameters.TYPE]
        self.results: ModalityFiles = {}
        self.exact = _is_microgrid_experiment(config)
        self.files = files
        self.reporter = reporter
        self.force = force

    def run(self) -> ModalityFiles:
        """Align ordered targets and release each pair before leaving its progress context."""
        if self.exact:
            self.reporter.detail(
                "Microgrid MSI modality found: alignment shows only foreground spots, without cluster colors"
            )
        targets = [
            modality
            for modality in self.modalities
            if modality[ModalityParameters.NAME] != self.reference
        ]
        # Guides first: a guided pair composes with the reference -> guide coordinates.
        targets.sort(key=lambda m: bool(m.get(ModalityParameters.ALIGNMENT_GUIDE)))
        for index, modality in enumerate(targets, 1):
            name = modality[ModalityParameters.NAME]
            kind = modality[ModalityParameters.TYPE]
            force = (self.force or {}).get(
                name,
                modality.get(ModalityParameters.ALIGNMENT_FORCE_RECOMPUTING, False),
            )
            with self.reporter.modality(name, index, len(targets)):
                guide = modality.get(ModalityParameters.ALIGNMENT_GUIDE)
                aligner = (
                    self.create_guided_aligner(name, kind, guide)
                    if guide
                    else self.create_aligner(name, kind)
                )
                self.results[name] = self.align_pair(aligner, modality, name, force)
                del aligner
                release_memory(gpu=False)
        return self.results

    def create_aligner(self, name: str, kind: str) -> DirectMappingAligner:
        """Bind the fixed target frame and moving reference using captured pair settings."""
        return DirectMappingAligner(
            path=self.path,
            reference_modality=self.files[name],
            target_modality=self.files[self.reference],
            reference_modality_name=name,
            target_modality_name=self.reference,
            reference_modality_type=kind,
            target_modality_type=self.reference_type,
            exact_spots=self.exact,
            reporter=self.reporter,
        )

    def create_guided_aligner(self, name: str, kind: str, guide: str) -> GuidedAligner:
        """Bind the guide as the fixed frame and the guided modality as the moving layer."""
        guide_type = next(
            m[ModalityParameters.TYPE]
            for m in self.modalities
            if m[ModalityParameters.NAME] == guide
        )
        return GuidedAligner(
            path=self.path,
            guide_files=self.files[guide],
            moving_files=self.files[name],
            guide_name=guide,
            moving_name=name,
            guide_type=guide_type,
            moving_type=kind,
            anchor_name=self.reference,
            anchor_files=self.files[self.reference],
            exact_spots=self.exact,
            reporter=self.reporter,
        )

    def align_pair(
        self,
        aligner: DirectMappingAligner,
        modality: ModalityConfig,
        name: str,
        force: bool,
    ) -> dict[str, str]:
        """Use stored coordinates, browser confirmation, or cache according to the existing policy."""
        strategy = modality.get(
            ModalityParameters.ALIGNMENT_STRATEGY, AlignmentStrategy.MANUAL
        )
        if strategy == AlignmentStrategy.PRE_ALIGNED:
            self.reporter.detail(
                f"Pre-aligned: '{self.reference}' coordinates are used as they are, without the alignment tool"
            )
            return aligner.uniform_aligned_dataset(force_recomputing=force)
        if aligner.is_alignment_needed(force_recomputing=force):
            self.reporter.set_state("alignment_waiting")
            guide = modality.get(ModalityParameters.ALIGNMENT_GUIDE)
            self.reporter.detail(
                f"Waiting for the alignment tool: align '{name}' onto its guide '{guide}' "
                f"('{self.reference}' reaches '{name}' through '{guide}')"
                if guide
                else f"Waiting for the alignment tool: align '{self.reference}' into '{name}' space"
            )
            return aligner.align_dataset(
                force_recomputing=force,
                on_gui_done=lambda: self.reporter.set_state("running"),
            )
        return aligner.collect_aligned_files()
