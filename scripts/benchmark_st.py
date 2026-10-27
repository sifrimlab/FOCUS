#!/usr/bin/env python3
"""Reproducible ST diagnostic; outputs stay outside the input dataset.

PYTHONPATH=src python scripts/benchmark_st.py MEL.h5ad --output /tmp/focus-st-benchmark
Runs the production entry point (spot_cluster_labels), so the unit plan (native, lattice
blocks or Voronoi supercells) follows --clustering-max-units and --clustering-min-counts.
"""
import argparse
import json
import threading
import time
from pathlib import Path

import anndata as ad
import numpy as np
import psutil
import scipy.sparse as sp

from focus.preprocessing.st_clustering import spot_cluster_labels
from focus.preprocessing.st_settings import DEFAULTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    for name, value in DEFAULTS.items():
        parser.add_argument('--' + name.replace('_', '-'), type=type(value), default=value)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    a = ad.read_h5ad(args.input)
    X = sp.csr_matrix(a.X)
    original = X.copy()
    coords = np.asarray(a.obsm['spatial'], np.float32)
    counts = np.asarray(X.sum(axis=1)).ravel()
    result = {'input': str(args.input.resolve()), 'shape': list(a.shape)}
    labels = {}
    process = psutil.Process()

    def measured(name, run):
        stop = threading.Event()
        peak = [process.memory_info().rss]
        def monitor():
            while not stop.wait(.05): peak[0] = max(peak[0], process.memory_info().rss)
        thread = threading.Thread(target=monitor, daemon=True); thread.start()
        start = time.perf_counter()
        try: value = run()
        finally: stop.set(); thread.join()
        result.setdefault(name, {}).update(seconds=time.perf_counter()-start, peak_process_rss_mb=peak[0]/1e6)
        return value

    settings = {key: getattr(args, key) for key in DEFAULTS}
    L, eligible, info = measured('clustering', lambda: spot_cluster_labels(
        X, a.var_names, coords, a.obs, a.uns.get('spot_size'), settings))
    labels['clustering'] = L
    result['clustering'].update(info)
    result['clustering']['median_counts_by_cluster'] = {label: float(np.median(counts[L == label])) for label in np.unique(L)}
    np.save(args.output/'labels.npy', L.astype(str), allow_pickle=False)
    result['expression_unchanged'] = (X != original).nnz == 0
    # Descriptive marker enrichment, not cell type annotation or differential-expression inference.
    totals = np.asarray(X.sum(axis=0)).ravel()
    global_composition = totals / max(totals.sum(), 1)
    enrichment = {}
    for label in np.unique(L[eligible]):
        sums = np.asarray(X[L == label].sum(axis=0)).ravel()
        composition = sums / max(sums.sum(), 1)
        score = np.log2((composition + 1e-6) / (global_composition + 1e-6))
        score[sums < 20] = -np.inf
        idx = np.argsort(score)[-10:][::-1]
        enrichment[label] = [{'gene': str(a.var_names[i]), 'log2_composition_enrichment': float(score[i])} for i in idx if np.isfinite(score[i])]
    result['descriptive_marker_enrichment'] = enrichment
    def convert(value):
        if isinstance(value, np.ndarray): return value.tolist()
        if isinstance(value, np.generic): return value.item()
        raise TypeError(type(value).__name__)
    (args.output/'metrics.json').write_text(json.dumps(result, indent=2, default=convert))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 1+len(labels), figsize=(6*(1+len(labels)), 6), layout='constrained')
    axes[0].scatter(*coords.T, c=np.log1p(counts), s=.3, cmap='viridis', rasterized=True)
    axes[0].set_title('Input log1p UMI counts')
    for ax, (name, lab) in zip(axes[1:], labels.items()):
        from focus.spot_colors import cluster_colors
        colors = cluster_colors(lab)
        ax.scatter(*coords.T, c=[colors[label] for label in lab], s=.3, rasterized=True)
        ax.set_title(name + ' clusters (colors local to each run)')
    for ax in axes: ax.set_aspect('equal'); ax.invert_yaxis(); ax.set_xlabel('x'); ax.set_ylabel('y')
    fig.savefig(args.output/'comparison.png', dpi=180)
    print(json.dumps({key: result['clustering'][key] for key in ['units', 'n_spots', 'n_eligible_spots', 'n_selected_genes', 'n_clusters', 'seconds', 'peak_process_rss_mb']}, indent=2))
    print('PC1-UMI rho:', info.get('pc_log_counts_spearman', [None])[0])
    print(args.output/'metrics.json')


if __name__ == '__main__': main()
