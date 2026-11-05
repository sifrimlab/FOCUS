"""Pixel composition registration: each anchor spot described by the phenotypes of its pixels.

An image target (hyperspectral Raman, multiplexed fluorescence) has many pixels per anchor spot.
Every measured pixel becomes a composition vector through a dataset-level codebook of its channel
group (:mod:`focus.registration.pixel_codebook`): the abundances of the unmixing endmembers, or
the one-hot Pixie phenotype. Each spot gets the mean vector of the pixels whose centres lie inside
its footprint, the fraction of the spot that each phenotype occupies:

- ``X``: the composition, one block per channel group, each block summing to 1 (NaN where the
  group measured no pixel of the spot).
- ``obsm['channel_mean']``: the mean of the pixel values in the same footprint (uniform weights).
- ``obs['coverage']``: the largest measured fraction of the footprint over the images.
- merged ``uns['pixel_phenotypes']``: per group, the model, the prototype (endmember spectrum or
  phenotype mean intensities), the pixel share and the criterion that set the number of entries.
- ``<modality>_<sample>_pixel_phenotypes.ome.tiff``: the label map of every image.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from typing import cast

import anndata
import numpy as np
import pandas as pd

from focus.config.options import RegistrationType
from focus.io.ome import read_ome_physical_size_um
from focus.io.paths import merged_registration_file
from focus.io.provenance import fingerprint
from focus.registration import pixel_codebook as cb
from focus.registration.anchor import AnchorArrays, read_anchor_arrays
from focus.registration.execution import register_samples, sample_geometry
from focus.registration.label_map import LabelMaps, label_map_file
from focus.registration.merging import merge_registered_samples
from focus.registration.pixel_readout import (
    RamanTarget,
    footprint_crop,
    image_dims,
    image_geometry,
    raman_targets,
    tiles,
)
from focus.registration.provenance import registration_fingerprint
from focus.registration.types import RegistrationJob
from focus.runtime.reporter_context import get_reporter
from focus.runtime.reporting import StepReporter
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.membership import pixel_footprint_membership
from focus.spatial.overlap import anchor_areas
from focus.spatial.phenotypes import PixelCodebook

COVERAGE_KEY = "coverage"
MEAN_KEY = "channel_mean"
UNS_KEY = "pixel_phenotypes"
Codebooks = dict[str, PixelCodebook]


@dataclass
class _Sums:
    """Footprint sums of one channel group: composition, values and measured pixels."""

    composition: np.ndarray  # (N, K)
    values: np.ndarray  # (N, C)
    count: np.ndarray  # (N,)

    @classmethod
    def zeros(cls, n: int, book: PixelCodebook) -> _Sums:
        """Empty sums of ``n`` spots for one codebook."""
        return cls(
            np.zeros((n, len(book.names))),
            np.zeros((n, len(book.channels))),
            np.zeros(n),
        )

    def means(self, total: np.ndarray) -> np.ndarray:
        """Row means of ``total`` over the measured pixels (NaN where none)."""
        out = np.full(total.shape, np.nan, dtype=np.float32)
        hit = self.count > 0
        out[hit] = total[hit] / self.count[hit, None]
        return out


def _summary(book: PixelCodebook) -> str:
    """One activity detail on a fitted codebook."""
    if book.model == cb.phenotypes.UNMIXING:
        rank = book.criterion["spectral_rank"]
        return f"'{book.group}': {len(book.names)} endmembers (spectral rank {rank})"
    if book.model == cb.phenotypes.PIXEL_CLUSTERS:
        k = len(book.names) - 1
        return f"'{book.group}': {k} pixel phenotypes (within-cluster SSE elbow)"
    return (
        f"'{book.group}': fewer than {cb.phenotypes.MIN_CHANNELS} channels, mean only"
    )


def _unique_labels(books: Codebooks) -> Codebooks:
    """Codebooks whose group names are unique (a repeated name gets the group index)."""
    names = [b.group for b in books.values()]
    out: Codebooks = {}
    for index, (key, book) in enumerate(books.items()):
        if names.count(book.group) > 1:
            book = replace(book, group=f"{book.group} [{index + 1}]")
        out[key] = book
    return out


class PixelCompositionRegistration:
    """Register a multichannel image modality to the anchor by the composition of its pixels.

    Parameters
    ----------
    path : str
        Path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.PIXEL_COMPOSITION

    def __init__(self, path: str) -> None:
        """Retain the dataset root; registration reads source files when requested.

        Parameters
        ----------
        path : str
            Dataset root for sample and merged registration outputs."""
        self._path = path

    def _codebook_key(
        self, anchors: dict[str, AnchorArrays], files: dict[str, str]
    ) -> str:
        """Fingerprint of everything the codebooks depend on."""
        settings = {"algorithm": cb.ALGORITHM}
        return fingerprint(
            {
                sample: registration_fingerprint(
                    self._REGISTRATION_TYPE, anchor, files[sample], settings
                )
                for sample, anchor in anchors.items()
            }
        )

    def _fit_codebooks(
        self,
        anchors: dict[str, AnchorArrays],
        target_files: dict[str, str],
        target_name: str,
        reporter: StepReporter,
    ) -> Codebooks:
        """Sample the pixels of every sample inside its footprints and fit one codebook per group."""
        groups: dict[str, cb.GroupSample] = {}
        rng = np.random.default_rng(0)
        budget = cb.SAMPLE_PIXELS // max(1, len(anchors))
        steps = reporter.tqdm(
            list(anchors), 1, 3, "Learning pixel phenotypes", unit="sample"
        )
        for sample in steps:
            geom = sample_geometry(anchors[sample], target_name, reporter)
            if geom is not None:
                filename = target_files[sample]
                targets = raman_targets(filename)
                cb.sample_file(
                    filename, targets, anchors[sample], geom, groups, budget, rng
                )
        books = _unique_labels({k: cb.fit_group(g, rng) for k, g in groups.items()})
        for book in books.values():
            reporter.detail(_summary(book))
        return books

    def codebooks(
        self,
        samples: list[str],
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        target_name: str,
        force_recomputing: bool,
        reporter: StepReporter,
    ) -> tuple[Codebooks, str]:
        """The dataset codebooks (stored, or learned and stored) and their fingerprint."""
        anchors = {
            s: a
            for s in samples
            if (a := read_anchor_arrays(anchor_files[s], target_name)) is not None
        }
        key = self._codebook_key(anchors, target_files)
        path = cb.codebook_file(
            merged_registration_file(self._path, target_name, "h5ad")
        )
        stored = None if force_recomputing else cb.load_codebooks(path, key)
        if stored is not None:
            reporter.step(1, 3, "Learning pixel phenotypes", cached=True)
            return stored, key
        books = self._fit_codebooks(anchors, target_files, target_name, reporter)
        cb.save_codebooks(path, key, books)
        return books, key

    @staticmethod
    def _read_image(
        filename: str,
        target: RamanTarget,
        geom: AnchorGeometry,
        book: PixelCodebook,
        sums: _Sums,
        maps: LabelMaps,
    ) -> np.ndarray:
        """Add one image's footprint sums to ``sums``; the measured fraction of every footprint."""
        shape = image_dims(filename, target.series)
        crop = footprint_crop(geom, *shape)
        count = np.zeros(len(sums.count))
        if crop is None:
            return count
        plane = (
            maps.plane(target, shape, book.names, book.group) if book.names else None
        )
        for tile in tiles(crop, cb.TILE_PX):
            values = cb.read_tile(filename, target, tile, shape, book.blur)
            measured = np.isfinite(values).all(axis=1)
            vectors = np.zeros((len(values), len(book.names)), dtype=np.float32)
            vectors[measured] = book.encode(values[measured])
            weights = pixel_footprint_membership(geom, tile, "ones")
            sums.composition += weights @ vectors
            sums.values += weights @ np.where(measured[:, None], values, 0.0)
            count += weights @ measured.astype(np.float32)
            if plane is not None:
                c0, r0, c1, r1 = tile
                labels = np.where(measured, vectors.argmax(axis=1) + 1, 0)
                plane[0, r0:r1, c0:c1] = labels.reshape(r1 - r0, c1 - c0).astype(
                    np.uint8
                )
        sums.count += count
        area = anchor_areas(geom)
        return cast(
            np.ndarray, np.divide(count, area, out=np.zeros_like(count), where=area > 0)
        )

    @staticmethod
    def _assemble(
        sample_id: str, anchor: AnchorArrays, books: Codebooks, sums: dict[str, _Sums]
    ) -> anndata.AnnData:
        """Registered AnnData from the footprint sums of every group."""
        many = len(books) > 1
        blocks, names, groups, entries, means, columns = [], [], [], [], [], []
        for key, book in books.items():
            blocks.append(sums[key].means(sums[key].composition))
            names += [f"{n} ({book.group})" if many else n for n in book.names]
            groups += [book.group] * len(book.names)
            entries += book.names
            means.append(sums[key].means(sums[key].values))
            columns += [f"{c} ({book.group})" if many else c for c in book.channels]
        mean = np.concatenate(means, axis=1)
        composition = np.concatenate(blocks, axis=1) if names else mean
        var = (
            pd.DataFrame({"group": groups, "phenotype": entries}, index=names)
            if names
            else pd.DataFrame(index=columns)
        )
        adata = anndata.AnnData(
            X=composition,
            obsm={"spatial": anchor.mapped.copy()},
            obs={"sample_id": [sample_id] * anchor.n_obs},
            var=var,
        )
        adata.obsm[MEAN_KEY] = pd.DataFrame(
            mean, index=adata.obs_names, columns=columns
        )
        return adata

    def _compute_sample(
        self,
        target_files: dict[str, str],
        target_name: str,
        books: Codebooks,
        reporter: StepReporter,
        sample_id: str,
        anchor: AnchorArrays,
        geom: AnchorGeometry,
    ) -> tuple[anndata.AnnData | None, dict[str, object]]:
        """Compute one sample's spot composition, channel means and label maps."""
        filename = target_files[sample_id]
        targets = raman_targets(filename)
        n = anchor.n_obs
        sums = {k: _Sums.zeros(n, b) for k, b in books.items()}
        maps = LabelMaps(label_map_file(self._path, sample_id, target_name))
        coverage = np.zeros(n)
        for target in targets:
            key = cb.group_key(target)
            if key not in books:
                reporter.warning(
                    f"'{target.name}': no phenotype codebook for its channels, skipped"
                )
                continue
            image_geom = image_geometry(anchor, geom, target)
            fraction = self._read_image(
                filename, target, image_geom, books[key], sums[key], maps
            )
            coverage = np.maximum(coverage, fraction)
            reporter.detail(
                f"'{target.name}': {int((fraction > 0).sum())}/{n} spots measured"
            )
        adata = self._assemble(sample_id, anchor, books, sums)
        adata.obs[COVERAGE_KEY] = np.clip(coverage, 0.0, 1.0).astype(np.float32)
        maps.write(read_ome_physical_size_um(filename))
        return adata, {"images": len(targets)}

    def register_dataset(
        self,
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        anchor_name: str,
        target_name: str,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """Learn the dataset codebooks, then register every sample to its anchor spots.

        Returns
        -------
        dict[str, str]
            {sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
        """
        samples = sorted(set(anchor_files) & set(target_files) - {"merged"})
        reporter = step_reporter or get_reporter()
        books, key = self.codebooks(
            samples,
            anchor_files,
            target_files,
            target_name,
            force_recomputing,
            reporter,
        )
        job = RegistrationJob(
            path=self._path,
            samples=reporter.tqdm(
                samples, 2, 3, "Reading spot composition", unit="sample"
            ),
            anchor_files=anchor_files,
            target_files=target_files,
            anchor_name=anchor_name,
            target_name=target_name,
            registration_type=self._REGISTRATION_TYPE,
            settings={"algorithm": cb.ALGORITHM, "codebook": key},
            compute=partial(
                self._compute_sample, target_files, target_name, books, reporter
            ),
            force_recomputing=force_recomputing,
            reporter=reporter,
        )
        registered = register_samples(job)
        reporter.step(3, 3, "Merging samples")
        return self._merge(registered, target_name, books, force_recomputing)

    def _merge(
        self,
        registered: dict[str, str],
        target_name: str,
        books: Codebooks,
        force: bool,
    ) -> dict[str, str]:
        """Merge the sample outputs; the codebook summaries go in the merged ``uns``."""
        return merge_registered_samples(
            self._path,
            registered,
            target_name,
            self._REGISTRATION_TYPE,
            force_recomputing=force,
            extra_uns={UNS_KEY: {b.group: b.describe() for b in books.values()}},
        )
