#!/usr/bin/env python3
"""Reproducible ST diagnostic; outputs stay outside the input dataset.

PYTHONPATH=src python scripts/benchmark_st.py MEL.h5ad --output /tmp/focus-st-benchmark
Runs the production entry point (spot_cluster_labels) with the --capture, --unit and
--normalization settings, so units, normalization and labels match preprocessing.
"""

from collections.abc import Sequence
from focus.spatial.types import ClusterInfo

from typing import cast
from collections.abc import Callable
from typing import Literal, TypedDict, TypeVar

import argparse
import json
import threading
import time
from pathlib import Path

import anndata as ad
import numpy as np
import psutil
import scipy.sparse as sp

from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from focus.config.options import StCapture, StNormalization, StUnit
from focus.preprocessing.transcriptomics.technology import technology


ResultT = TypeVar("ResultT")


class BenchmarkResult(TypedDict, total=False):
    """Serializable clustering diagnostics and measured time and peak RSS."""

    input: str
    shape: list[int]
    clustering: dict[str, object]
    expression_unchanged: bool
    descriptive_marker_enrichment: dict[str, list[dict[str, str | float]]]


def main() -> None:
    """Parse command-line arguments and execute the requested workflow."""
    args = parse_arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    a = ad.read_h5ad(args.input)
    X = sp.csr_matrix(a.X)
    original = X.copy()
    coords = np.asarray(a.obsm["spatial"], np.float32)
    counts = np.asarray(X.sum(axis=1)).ravel()
    result: BenchmarkResult = {
        "input": str(args.input.resolve()),
        "shape": list(a.shape),
    }
    labels = {}
    process = psutil.Process()
    tech = technology(args.capture, args.unit)

    L, eligible, info = measured(
        "clustering",
        lambda: spot_cluster_labels(X, a.var_names, coords, tech, args.normalization),
        result,
        process,
    )
    labels["clustering"] = L
    result["clustering"].update(info)
    result["clustering"]["median_counts_by_cluster"] = {
        label: float(np.median(counts[L == label])) for label in np.unique(L)
    }
    np.save(args.output / "labels.npy", L.astype(str), allow_pickle=False)
    result["expression_unchanged"] = cast(sp.csr_matrix, (X != original)).nnz == 0
    result["descriptive_marker_enrichment"] = marker_enrichment(
        X, a.var_names, L, eligible
    )
    (args.output / "metrics.json").write_text(
        json.dumps(result, indent=2, default=convert)
    )
    save_plot(coords, counts, labels, args.output)
    print_metrics(result, info, args.output)


def parse_arguments() -> argparse.Namespace:
    """Parse the input, output and ST technology and normalization command options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--capture", choices=StCapture.list(), default=StCapture.SEQUENCING
    )
    parser.add_argument("--unit", choices=StUnit.list(), default=StUnit.SPOT)
    parser.add_argument(
        "--normalization", choices=StNormalization.list(), default=StNormalization.AUTO
    )
    return parser.parse_args()


def measured(
    name: Literal["clustering"],
    run: Callable[[], ResultT],
    result: BenchmarkResult,
    process: psutil.Process,
) -> ResultT:
    """Run a diagnostic operation while recording elapsed time and peak RSS."""
    stop = threading.Event()
    peak = [process.memory_info().rss]

    def monitor() -> None:
        """Sample process RSS until the diagnostic operation signals completion."""
        while not stop.wait(0.05):
            peak[0] = max(peak[0], process.memory_info().rss)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    start = time.perf_counter()
    try:
        value = run()
    finally:
        stop.set()
        thread.join()
    result.setdefault(name, {}).update(
        seconds=time.perf_counter() - start, peak_process_rss_mb=peak[0] / 1e6
    )
    return value


def marker_enrichment(
    X: sp.csr_matrix,
    variables: Sequence[str],
    L: np.ndarray,
    eligible: np.ndarray,
) -> dict[str, list[dict[str, str | float]]]:
    """Describe the ten largest composition enrichments per eligible cluster; do not mutate X."""
    # Descriptive marker enrichment, not cell type annotation or differential-expression inference.
    totals = np.asarray(X.sum(axis=0)).ravel()
    global_composition = totals / max(totals.sum(), 1)
    enrichment: dict[str, list[dict[str, str | float]]] = {}
    for label in np.unique(L[eligible]):
        sums = np.asarray(X[L == label].sum(axis=0)).ravel()
        composition = sums / max(sums.sum(), 1)
        score = np.log2((composition + 1e-6) / (global_composition + 1e-6))
        score[sums < 20] = -np.inf
        idx = np.argsort(score)[-10:][::-1]
        enrichment[label] = [
            {
                "gene": str(variables[i]),
                "log2_composition_enrichment": float(score[i]),
            }
            for i in idx
            if np.isfinite(score[i])
        ]
    return enrichment


def convert(value: object) -> object:
    """Convert NumPy diagnostic values for JSON encoding; reject unknown objects."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def save_plot(
    coords: np.ndarray,
    counts: np.ndarray,
    labels: dict[str, np.ndarray],
    output: Path,
) -> None:
    """Save counts and categorical labels in native spatial coordinates to comparison.png."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        1, 1 + len(labels), figsize=(6 * (1 + len(labels)), 6), layout="constrained"
    )
    axes[0].scatter(
        *coords.T, c=np.log1p(counts), s=0.3, cmap="viridis", rasterized=True
    )
    axes[0].set_title("Input log1p UMI counts")
    for ax, (name, lab) in zip(axes[1:], labels.items()):
        from focus.spot_colors import cluster_colors

        colors = cluster_colors(lab)
        ax.scatter(
            *coords.T, c=[colors[label] for label in lab], s=0.3, rasterized=True
        )
        ax.set_title(name + " clusters (colors local to each run)")
    for ax in axes:
        ax.set_aspect("equal")
        ax.invert_yaxis()
        ax.set_xlabel("x")
        ax.set_ylabel("y")
    fig.savefig(output / "comparison.png", dpi=180)


def print_metrics(result: BenchmarkResult, info: ClusterInfo, output: Path) -> None:
    """Print the original JSON diagnostic subset, count correlation and metrics file path."""
    print(
        json.dumps(
            {
                key: result["clustering"].get(key)
                for key in [
                    "resolution",
                    "units",
                    "n_spots",
                    "n_eligible_spots",
                    "n_selected_features",
                    "n_clusters",
                    "label_coherence",
                    "seconds",
                    "peak_process_rss_mb",
                ]
            },
            indent=2,
            default=str,
        )
    )
    print("PC1-UMI rho:", info.get("pc_log_counts_spearman", [None])[0])
    print(output / "metrics.json")


if __name__ == "__main__":
    main()
