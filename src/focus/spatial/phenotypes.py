"""Pixel phenotype codebooks: the per-pixel composition vector of one channel group.

A codebook turns every measured pixel of a channel group into a composition vector that sums to 1,
so the mean over a footprint is the composition of that footprint:

- ``unmixing`` (hyperspectral Raman): the abundances of ``r + 1`` VCA endmembers, ``r`` the
  Gavish-Donoho rank of spectra averaged over cellular-scale bins
  (:mod:`focus.spatial.unmixing`). ``p`` endmembers span a ``p - 1`` dimensional affine
  subspace (Nascimento and Bioucas-Dias 2005).
- ``pixel_clusters`` (multiplexed fluorescence): the one-hot Pixie metacluster
  (:mod:`focus.spatial.pixel_clusters`), with a last ``no signal`` entry for pixels without signal.
- ``mean``: groups with fewer than ``MIN_CHANNELS`` channels have no composition.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from focus.spatial import pixel_clusters, spectral, unmixing

UNMIXING = "unmixing"
PIXEL_CLUSTERS = "pixel_clusters"
MEAN_ONLY = "mean"
MIN_CHANNELS = 3
NO_SIGNAL = "no signal"


@dataclass(frozen=True)
class PixelCodebook:
    """Dataset-level phenotype model of one channel group."""

    group: str
    channels: list[str]
    model: str
    endmembers: np.ndarray | None = None  # (K, C), unmixing
    clusters: pixel_clusters.PixelClusters | None = None
    prototypes: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    pixel_share: np.ndarray = field(default_factory=lambda: np.zeros(0))
    criterion: dict[str, object] = field(default_factory=dict)

    @property
    def blur(self) -> bool:
        """True when pixels are smoothed before they are encoded (Pixie)."""
        return self.model == PIXEL_CLUSTERS

    @property
    def names(self) -> list[str]:
        """Name of every composition entry."""
        if self.model == UNMIXING:
            return [f"endmember {k + 1}" for k in range(len(self.prototypes))]
        if self.model == PIXEL_CLUSTERS:
            return [f"phenotype {k + 1}" for k in range(len(self.prototypes))] + [
                NO_SIGNAL
            ]
        return []

    def encode(self, pixels: np.ndarray) -> np.ndarray:
        """``(N, K)`` composition vectors of ``(N, C)`` measured pixels (rows sum to 1)."""
        if self.model == UNMIXING:
            assert self.endmembers is not None
            return unmixing.fully_constrained_abundances(pixels, self.endmembers)
        if self.model == PIXEL_CLUSTERS:
            assert self.clusters is not None
            labels = pixel_clusters.assign_pixel_clusters(self.clusters, pixels)
            out = np.zeros((len(pixels), len(self.names)), dtype=np.float32)
            out[np.arange(len(pixels)), labels] = (
                1.0  # -1 is the last (no signal) entry
            )
            return out
        return np.zeros((len(pixels), 0), dtype=np.float32)

    def describe(self) -> dict[str, object]:
        """JSON-compatible summary for ``uns`` (prototypes in the original channel units)."""
        return {
            "model": self.model,
            "channels": list(self.channels),
            "phenotypes": self.names,
            "prototypes": np.asarray(self.prototypes, dtype=np.float64).tolist(),
            "pixel_share": np.asarray(self.pixel_share, dtype=np.float64).tolist(),
            "criterion": self.criterion,
        }


# Endmembers closer than this (RMS difference over the geometric mean of their norms) to one found
# before are dropped, so two that reach the same extreme do not split its share between
# near-identical twins (closest pair 0.08 on the P762 CH window).
DUPLICATE_DISTANCE = 0.15


# A component of the spot spectra counts towards the number of endmembers only when its singular
# value reaches this fraction of the leading one. The registration reads spectra that preprocessing
# already denoised, so the Gavish-Donoho noise floor is the quantisation step and tiny non-linear
# residues (0.3 % of the leading value in a synthetic linear mixture) would otherwise count.
MIN_COMPONENT_FRACTION = 0.02


def distinct_endmembers(endmembers: np.ndarray) -> np.ndarray:
    """``endmembers`` without those within ``DUPLICATE_DISTANCE`` of an earlier one."""
    kept: list[int] = []
    norms = np.linalg.norm(endmembers, axis=1)
    for i in range(len(endmembers)):
        if all(
            np.linalg.norm(endmembers[i] - endmembers[j])
            >= DUPLICATE_DISTANCE * np.sqrt(norms[i] * norms[j])
            for j in kept
        ):
            kept.append(i)
    return np.asarray(endmembers[kept])


def fit_unmixing(group: str, channels: list[str], spectra: np.ndarray) -> PixelCodebook:
    """Unmixing codebook from processed ``(N, C)`` spot-scale spectra.

    The Gavish-Donoho rank of the spectra (components above ``MIN_COMPONENT_FRACTION`` of the leading
    singular value) sets ``p = rank + 1`` endmembers: the archetypes of the
    spectra (``unmixing.archetypal_analysis``), near-duplicates merged. Spot-scale spectra are
    mixtures without pure samples; their archetypes enclose them, so a spot's composition spans the
    simplex instead of collapsing on a face of it.
    """
    finite = spectra[np.isfinite(spectra).all(axis=1)]
    rank, singular = spectral.signal_rank(finite)
    strong = int((singular >= MIN_COMPONENT_FRACTION * singular[0]).sum())
    rank = max(1, min(rank, strong))
    p = min(rank + 1, len(channels))
    found = unmixing.archetypal_analysis(finite, p)
    endmembers = distinct_endmembers(found).astype(np.float32)
    share = unmixing.fully_constrained_abundances(
        finite.astype(np.float32), endmembers
    ).mean(axis=0)
    return PixelCodebook(
        group=group,
        channels=channels,
        model=UNMIXING,
        endmembers=endmembers,
        prototypes=endmembers,
        pixel_share=share,
        criterion={
            "spectral_rank": rank,
            "singular_values": [float(v) for v in singular[: min(len(singular), 20)]],
            "samples": "spot-scale blocks",
            "merged_duplicates": int(len(found) - len(endmembers)),
        },
    )


def fit_clusters(group: str, channels: list[str], pixels: np.ndarray) -> PixelCodebook:
    """Pixie codebook from sampled ``(N, C)`` blurred pixel intensities."""
    model = pixel_clusters.fit_pixel_clusters(pixels)
    labels = pixel_clusters.assign_pixel_clusters(model, pixels)
    k = model.k
    prototypes = np.zeros((k, len(channels)), dtype=np.float64)
    for j in range(k):
        if (labels == j).any():
            prototypes[j] = pixels[labels == j].mean(axis=0)
    share = np.bincount(np.where(labels < 0, k, labels), minlength=k + 1) / len(labels)
    return PixelCodebook(
        group=group,
        channels=channels,
        model=PIXEL_CLUSTERS,
        clusters=model,
        prototypes=prototypes,
        pixel_share=share,
        criterion={"within_sse": model.sse, "k": k},
    )


def to_arrays(codebook: PixelCodebook, prefix: str) -> dict[str, np.ndarray]:
    """Arrays of a codebook for ``np.savez`` (keys start with ``prefix``)."""
    out = {
        f"{prefix}prototypes": np.asarray(codebook.prototypes),
        f"{prefix}pixel_share": np.asarray(codebook.pixel_share),
    }
    if codebook.endmembers is not None:
        out[f"{prefix}endmembers"] = codebook.endmembers
    if codebook.clusters is not None:
        out[f"{prefix}scale"] = codebook.clusters.scale
        out[f"{prefix}codes"] = codebook.clusters.codes
        out[f"{prefix}metacluster"] = codebook.clusters.metacluster
    return out


def from_arrays(
    meta: dict[str, object], arrays: dict[str, np.ndarray], prefix: str
) -> PixelCodebook:
    """Inverse of :func:`to_arrays` with the JSON part (``group``, ``channels``, ``model``, ...)."""
    criterion = dict(meta["criterion"])  # type: ignore[call-overload]
    clusters = None
    if f"{prefix}codes" in arrays:
        clusters = pixel_clusters.PixelClusters(
            arrays[f"{prefix}scale"],
            arrays[f"{prefix}codes"],
            arrays[f"{prefix}metacluster"],
            [float(v) for v in criterion.get("within_sse", [])],
        )
    return PixelCodebook(
        group=str(meta["group"]),
        channels=[str(c) for c in meta["channels"]],  # type: ignore[attr-defined]
        model=str(meta["model"]),
        endmembers=arrays.get(f"{prefix}endmembers"),
        clusters=clusters,
        prototypes=arrays[f"{prefix}prototypes"],
        pixel_share=arrays[f"{prefix}pixel_share"],
        criterion=criterion,
    )
