"""Transfer sample annotation files onto the aligned reference modality."""

from collections.abc import Callable

from typing import cast
from focus.config.types import AnnotationConfig
from focus.pipeline.types import ModalityFiles

from focus.config.types import PipelineConfig

import os
import shutil
import anndata
import h5py
import numpy as np
import pandas as pd

from focus.config.keys import ConfigParameters, AnnotationsParameters
from focus.io.paths import annotation_file, merged_annotation_file
from anndata.io import read_elem, write_elem
from focus.io.anndata import (
    _compat_strings,
    concat_on_disk_compat,
    write_h5ad_compat,
)
from focus.runtime.resources import release_memory
from focus.runtime.reporting import StepReporter
from focus.runtime.reporter_context import get_reporter
from focus.pipeline.context import logger


def _run_annotation_transfer(
    config: PipelineConfig,
    modality_files: ModalityFiles,
    aligned_files: ModalityFiles,
    reporter: StepReporter | None = None,
) -> dict[str, str]:
    """
    Transfer spatial annotations from the annotation modality to the reference modality spots.

    Annotations are transferred per sample (using per-sample aligned coordinates as the
    reliable source), avoiding any dependency on the merged aligned file, which may be
    absent or incomplete if a previous run was interrupted before its merge step. The merged
    annotated file is the merged preprocessed reference with the per-sample labels added, so it
    keeps the cross-sample gene filter, the union panel, the merged normalization and
    ``uns['spot_size']``. Without a merged preprocessed file the annotated per-sample files are
    concatenated instead.

    Returns
    -------
    dict
            {sample_id: annotated_file_path, "merged": annotated_merged_path}
    """

    from focus.annotations import transfer_annotations

    return AnnotationStage(
        config, modality_files, aligned_files, reporter, transfer_annotations
    ).run()


class AnnotationStage:
    """Coordinate backed reads, per-sample annotation writes, and ordered merging."""

    def __init__(
        self,
        config: PipelineConfig,
        files: ModalityFiles,
        aligned: ModalityFiles,
        reporter: StepReporter | None,
        transfer: Callable[[np.ndarray, np.ndarray, dict[str, str]], np.ndarray],
    ) -> None:
        """Capture source metadata and reporter before discovering annotation paths."""
        self.path = config[ConfigParameters.DATASET_PATH]
        self.reference = config[ConfigParameters.REFERENCE_MODALITY]
        annotation = cast(
            AnnotationConfig, config[ConfigParameters.SPATIAL_ANNOTATIONS]
        )
        self.source = annotation[AnnotationsParameters.MODALITY_NAME]
        self.reporter = reporter or get_reporter()
        self.result: dict[str, str] = {}
        self.labels: dict[str, pd.Series] = {}
        self.samples = [
            sample for sample in files[self.reference] if sample != "merged"
        ]
        self.annotation_paths = discover_annotations(
            self.path, self.source, self.samples
        )
        self.files = files
        self.aligned = aligned
        self.transfer = transfer

    def run(self) -> dict[str, str]:
        """Transfer samples in discovery order, then write the merged reference."""
        outputs: list[str] = []
        for sample in self.reporter.tqdm(
            self.samples, 1, 2, "Transferring annotations", unit="sample"
        ):
            output = self.transfer_sample(sample)
            self.result[sample] = output
            outputs.append(output)
        if outputs:
            self.reporter.step(2, 2, "Merging annotated samples")
            merged = merged_annotation_file(self.path, self.reference, "h5ad")
            os.makedirs(os.path.dirname(merged), exist_ok=True)
            source = self.files[self.reference].get("merged")
            if source is not None and os.path.exists(source):
                self.label_merged(source, merged)
            else:
                self.reporter.warning(
                    f"No merged file for the reference '{self.reference}': "
                    "the annotated samples are concatenated (shared features only)"
                )
                concat_on_disk_compat(outputs, merged, merge="same", uns_merge="same")
            self.result["merged"] = merged
            logger.debug(f"Annotated merged reference saved to {merged}")
        release_memory(gpu=False)
        return self.result

    def transfer_sample(self, sample: str) -> str:
        """Label and write one reference sample, closing backed inputs on every exit."""
        path = self.files[self.reference][sample]
        reference = None
        annotation = None
        try:
            reference = anndata.read_h5ad(path, backed="r")
            if self.source == self.reference:
                coordinates = np.asarray(reference.obsm["spatial"])
            else:
                annotation = anndata.read_h5ad(
                    self.aligned[self.source][sample], backed="r"
                )
                coordinates = np.asarray(annotation.obsm[f"{self.source}_spatial"])
                annotation.file.close()
                annotation = None
            sample_ids = np.asarray(reference.obs["sample_id"])
            labels = self.transfer(coordinates, sample_ids, self.annotation_paths)
            count = int(np.sum(labels != None))  # noqa: E711
            self.reporter.detail(f"{count}/{len(coordinates)} spots annotated")
            reference.obs["spatial_annotation"] = pd.Categorical(labels)
            self.labels[sample] = pd.Series(
                labels, index=reference.obs_names.copy(), dtype=object
            )
            output = annotation_file(self.path, sample, self.reference, "h5ad")
            os.makedirs(os.path.dirname(output), exist_ok=True)
            write_h5ad_compat(reference, output)
            return output
        finally:
            close_backed(annotation, reference)

    def label_merged(self, source: str, output: str) -> None:
        """Copy the merged reference to ``output`` and add the per-sample labels to its ``obs``.

        Only ``obs`` is rewritten, so the matrices are never loaded. Rows are matched within each
        sample by observation name; merged rows of a sample without labels stay unlabelled.
        """
        temporary = os.path.join(
            os.path.dirname(output), f".{os.getpid()}.tmp.{os.path.basename(output)}"
        )
        try:
            shutil.copyfile(source, temporary)
            with h5py.File(temporary, "r+") as handle:
                obs = cast(pd.DataFrame, read_elem(handle["obs"]))
                values = np.full(obs.shape[0], None, dtype=object)
                sample_ids = obs["sample_id"].astype(str).to_numpy()
                for sample, labels in self.labels.items():
                    rows = np.flatnonzero(sample_ids == sample)
                    names = obs.index[rows]
                    if not labels.index.equals(names):
                        labels = labels.reindex(names)
                    values[rows] = labels.to_numpy()
                obs["spatial_annotation"] = pd.Categorical(values)
                del handle["obs"]
                anndata.settings.allow_write_nullable_strings = True
                write_elem(handle, "obs", _compat_strings(obs))
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def discover_annotations(path: str, source: str, samples: list[str]) -> dict[str, str]:
    """Use the first GeoJSON from each source folder, preserving filesystem ordering."""
    result: dict[str, str] = {}
    for sample in samples:
        directory = os.path.join(path, sample, source)
        filenames = [
            name for name in os.listdir(directory) if name.endswith(".geojson")
        ]
        result[sample] = os.path.join(directory, filenames[0])
    return result


def close_backed(*samples: anndata.AnnData | None) -> None:
    """Close open backed inputs in order, retaining the original cleanup error policy."""
    for sample in samples:
        if sample is not None:
            try:
                sample.file.close()
            except Exception:
                pass
