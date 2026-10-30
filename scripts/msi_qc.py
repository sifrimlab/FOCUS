#!/usr/bin/env python3
"""Quality report for preprocessed MSI data: scan-line stripes, drift and recalibration maps.

Reads a merged or per-sample MSI preprocessing h5ad and, per sample, writes:

  - {out_dir}/{sample_id}_qc.png : raster maps of the TIC of the binned raw intensities and, when
    present (FOCUS outputs since METHOD_VERSION "msi-2"), of obs['recalibration_ppm_<mode>'] and
    obs['recalibration_matches_<mode>'].
  - {out_dir}/msi_qc.csv         : one row per sample with the stripe index, the striped lines and
    the line trend, computed for lines of constant spatial y and of constant spatial x. The h5ad
    does not record the scan direction (and `.obsm['spatial']` swaps x and y when the imzML has no
    stage coordinates), so both are reported; stripes show up along the scan-line axis.

Line deviation: for each of the --top features with the highest mean intensity, the mean of .X over
the foreground spots of every scan line minus a running median over 5 lines, divided by the
feature's mean; a line's deviation is the median over the features, so it measures a shift shared
by the whole spectrum (a batch effect of that line), not one feature's biology. Stripe index: the
95th percentile of the line deviations; near 0 when no line stands out from its neighbours.
Striped lines: lines whose deviation exceeds the median line deviation by more than 5 robust
standard deviations (1.4826 x MAD). Line trend: Spearman correlation between the line index and the
mean TIC of the line, which flags a gradual intensity gradient down the acquisition. All are FOCUS
diagnostics, not published metrics.

Run it on outputs produced before and after a change, on the same data, and compare.

Usage
-----
    python scripts/msi_qc.py merged/preprocessing/MSI_merged_processed.h5ad qc_out
"""
import argparse
import csv
import os

import anndata as ad
import matplotlib
import numpy as np
import scipy.sparse as sp
from scipy.ndimage import median_filter
from scipy.stats import spearmanr

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

STRIPE_WINDOW = 5


def raster_indices(spatial: np.ndarray, spot_size) -> tuple[np.ndarray, np.ndarray]:
    """(row, col) raster indices from physical coordinates and the [x, y] spot size in µm."""
    size = np.asarray(spot_size, dtype=np.float64)
    col = np.rint((spatial[:, 0] - spatial[:, 0].min()) / size[0]).astype(np.int64)
    row = np.rint((spatial[:, 1] - spatial[:, 1].min()) / size[1]).astype(np.int64)
    return row, col


def as_dense_columns(matrix, columns) -> np.ndarray:
    block = matrix[:, columns]
    return block.toarray() if sp.issparse(block) else np.asarray(block)


def line_deviation(x: np.ndarray, line: np.ndarray) -> np.ndarray:
    """Per scan line: median over features of |line mean - running median of line means| / feature mean."""
    lines = np.unique(line)
    scale = x.mean(axis=0)
    keep = scale > 0
    if lines.size < STRIPE_WINDOW or not keep.any():
        return np.array([])
    position = np.searchsorted(lines, line)
    sums = np.zeros((lines.size, int(keep.sum())))
    np.add.at(sums, position, x[:, keep])
    means = sums / np.bincount(position, minlength=lines.size)[:, None]
    trend = median_filter(means, size=(STRIPE_WINDOW, 1), mode="nearest")
    return np.median(np.abs(means - trend) / scale[keep], axis=1)


def striped_lines(deviation: np.ndarray) -> int:
    if deviation.size == 0:
        return 0
    centre = np.median(deviation)
    spread = 1.4826 * np.median(np.abs(deviation - centre))
    return int(np.sum(deviation > centre + 5 * spread)) if spread > 0 else 0


def raster_image(row, col, values) -> np.ndarray:
    image = np.full((row.max() + 1, col.max() + 1), np.nan)
    image[row, col] = values
    return image


def line_statistics(x, tic, line) -> dict:
    deviation = line_deviation(x, line)
    lines = np.unique(line)
    line_tic = [tic[line == value].mean() for value in lines]
    return {"stripe_index": float(np.percentile(deviation, 95)) if deviation.size else float("nan"),
            "striped_lines": striped_lines(deviation),
            "line_trend": float(spearmanr(lines, line_tic).statistic) if lines.size > 2 else float("nan")}


def report_sample(adata, sample_id, spot_size, top, out_dir) -> dict:
    row, col = raster_indices(np.asarray(adata.obsm["spatial"], dtype=np.float64), spot_size)
    foreground = adata.obs["foreground"].astype(str).to_numpy() == "True" if "foreground" in adata.obs else np.ones(adata.n_obs, bool)

    raw = adata.layers["raw"] if "raw" in adata.layers else adata.X
    tic = np.asarray(raw.sum(axis=1)).ravel()
    mean_intensity = np.asarray(adata.X[foreground].mean(axis=0)).ravel()
    x = as_dense_columns(adata.X[foreground], np.argsort(mean_intensity)[::-1][:top])

    result = {"sample_id": sample_id, "spots": adata.n_obs, "foreground": int(foreground.sum())}
    for axis, line in (("y", row), ("x", col)):
        for key, value in line_statistics(x, tic[foreground], line[foreground]).items():
            result[f"{key}_{axis}"] = value

    panels = [("TIC (raw)", tic, "viridis")]
    for name in sorted(adata.obs.columns):
        if name.startswith("recalibration_ppm_"):
            panels.append((f"{name} (ppm)", adata.obs[name].to_numpy(dtype=np.float64), "coolwarm"))
        elif name.startswith("recalibration_matches_"):
            panels.append((name, adata.obs[name].to_numpy(dtype=np.float64), "magma"))
    figure, axes = plt.subplots(1, len(panels), figsize=(5 * len(panels), 4.5), squeeze=False)
    for axis, (title, values, cmap) in zip(axes[0], panels):
        image = axis.imshow(raster_image(row, col, values), cmap=cmap, interpolation="nearest")
        axis.set_title(title)
        axis.set_axis_off()
        figure.colorbar(image, ax=axis, fraction=0.046)
    figure.suptitle(f"{sample_id}: {summary(result)}")
    figure.tight_layout()
    figure.savefig(os.path.join(out_dir, f"{sample_id}_qc.png"), dpi=150)
    plt.close(figure)
    return result


def summary(result: dict) -> str:
    return "; ".join(f"lines of constant {axis}: stripe index {result[f'stripe_index_{axis}']:.4f}, "
                     f"{result[f'striped_lines_{axis}']} striped, trend {result[f'line_trend_{axis}']:+.2f}"
                     for axis in ("y", "x"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("h5ad", help="merged or per-sample MSI preprocessing h5ad")
    parser.add_argument("out_dir", help="output directory for the PNG maps and msi_qc.csv")
    parser.add_argument("--top", type=int, default=100, help="features used by the stripe index (default 100)")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    adata = ad.read_h5ad(args.h5ad)
    spot_sizes = adata.uns["spot_size"]
    rows = []
    for sample_id in adata.obs["sample_id"].astype(str).unique():
        subset = adata[adata.obs["sample_id"].astype(str).to_numpy() == sample_id]
        spot_size = spot_sizes[sample_id] if isinstance(spot_sizes, dict) else spot_sizes
        rows.append(report_sample(subset, sample_id, spot_size, args.top, args.out_dir))
        print(f"{sample_id}: {summary(rows[-1])}")

    with open(os.path.join(args.out_dir, "msi_qc.csv"), "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
