"""Coordinate stage contexts, output collection, and downstream invalidation."""

from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.options import ModalityType
from focus.config.types import PipelineConfig
from focus.io.paths import merged_preprocessed_file
from focus.io.provenance import cached_fingerprint
from focus.pipeline.alignment import _run_alignment
from focus.pipeline.annotations import _run_annotation_transfer
from focus.pipeline.compilation import compile_dataset
from focus.pipeline.context import _has_spot_modalities, logger
from focus.pipeline.invalidation import _compute_effective_force_flags
from focus.pipeline.outputs import collect_outputs
from focus.pipeline.registration import _run_registration
from focus.pipeline.types import ModalityFiles, PipelineFiles
from focus.preprocessing import preprocess_modality
from focus.runtime.reporting import StepReporter
from focus.runtime.resources import release_memory


class PipelineStages:
    """Run the configured stages in order with their original progress boundaries."""

    def __init__(self, config: PipelineConfig, reporter: StepReporter) -> None:
        """Bind validated settings and reporting; allocate only transient path maps."""
        self.dataset_path = config[ConfigParameters.DATASET_PATH]
        self.modalities = config[ConfigParameters.MODALITIES]
        self.outputs: PipelineFiles = {}
        self.annotations_enabled = (
            config.get(ConfigParameters.SPATIAL_ANNOTATIONS) is not None
        )
        self.total = 5 if self.annotations_enabled else 4
        self.config = config
        self.reporter = reporter
        self.preprocessed: ModalityFiles = {}
        self.changed: set[str] = set()
        self.aligned: ModalityFiles = {}
        self.annotated: dict[str, str] = {}
        self.registered: ModalityFiles = {}

    def run(self) -> PipelineFiles:
        """Execute stages and return their ordered file maps; propagate cancellation."""
        self.preprocess()
        alignment_force, registration_force = _compute_effective_force_flags(
            self.config, self.changed
        )
        self.align(alignment_force)
        self.annotate()
        self.register(registration_force)
        self.compile()
        logger.info("FOCUS pipeline completed successfully.")
        return self.outputs

    def record(self, stage: str, files: ModalityFiles) -> None:
        """Add nonempty stage outputs using the existing GUI representation."""
        collected = collect_outputs(files)
        if collected is not None:
            self.outputs[stage] = collected

    def preprocess(self) -> None:
        """Run modalities in order and track changed numerical preprocessing keys."""
        with self.reporter.stage("preprocessing", 1, self.total):
            for index, modality in enumerate(self.modalities, 1):
                name = modality[ModalityParameters.NAME]
                kind = modality[ModalityParameters.TYPE]
                merged = merged_preprocessed_file(self.dataset_path, name, "h5ad")
                fingerprinted = kind in (ModalityType.ST, ModalityType.MSI)
                before = cached_fingerprint(merged) if fingerprinted else None
                with self.reporter.modality(name, index, len(self.modalities)):
                    self.preprocessed[name] = preprocess_modality(
                        path=self.dataset_path,
                        modality_name=name,
                        modality_type=kind,
                        preprocessing_settings=modality[
                            ModalityParameters.PROCESSING_SETTINGS
                        ],
                        step_reporter=self.reporter,
                        ignore_samples=self.config.get(
                            ConfigParameters.IGNORE_SAMPLES, []
                        ),
                    )
                if fingerprinted and before != cached_fingerprint(merged):
                    self.changed.add(name)
        self.record("preprocessing", self.preprocessed)
        release_memory(gpu=False)

    def align(self, force: dict[str, bool]) -> None:
        """Align enabled modality pairs with the established invalidation overrides."""
        if self.config[ConfigParameters.PERFORM_ALIGNMENT]:
            with self.reporter.stage("alignment", 2, self.total):
                self.aligned = _run_alignment(
                    self.config, self.preprocessed, self.reporter, force_overrides=force
                )
            self.record("alignment", self.aligned)
        else:
            logger.info("Skipping alignment (disabled in config).")

    def annotate(self) -> None:
        """Transfer optional annotations before registration starts."""
        if self.annotations_enabled:
            with self.reporter.stage("annotation_transfer", 3, self.total):
                self.annotated = _run_annotation_transfer(
                    self.config, self.preprocessed, self.aligned, self.reporter
                )
            self.record("annotations", {"reference": self.annotated})

    def register(self, force: dict[str, bool]) -> None:
        """Register enabled targets with the established downstream force flags."""
        if self.config[ConfigParameters.PERFORM_REGISTRATION]:
            with self.reporter.stage(
                "registration", 4 if self.annotations_enabled else 3, self.total
            ):
                self.registered = _run_registration(
                    self.config,
                    self.preprocessed,
                    self.aligned,
                    self.reporter,
                    force_overrides=force,
                )
            self.record("registration", self.registered)
        else:
            logger.info("Skipping registration (disabled in config).")

    def compile(self) -> None:
        """Compile paired spot outputs and release memory, including early exits."""
        if self.config[ConfigParameters.PERFORM_REGISTRATION] and _has_spot_modalities(
            self.config
        ):
            with self.reporter.stage(
                "compiling", 5 if self.annotations_enabled else 4, self.total
            ):
                path = compile_dataset(
                    self.config,
                    self.preprocessed,
                    self.registered,
                    self.annotated,
                    self.reporter,
                )
            if path:
                self.outputs["multimodal"] = {"merged": [path], "per_modality": {}}
            release_memory(gpu=False)
