"""Cross-sample gene filtering without changing sparse counts or ordering."""

import anndata as ad
import numpy as np


def filter_genes(
    adata: ad.AnnData,
    min_spots_per_gene: float | None,
    min_count_spots_ratio: float | None,
) -> ad.AnnData:
    """
    Cross-sample gene filtering in a single pass over the per-sample submatrices.

    Combines two opt-in criteria so each sample's rows are sliced and reduced only once
    (rather than once per criterion), and the matrix is materialized only once at the end:

    - min_spots_per_gene: keep a gene expressed in at least this fraction of a sample's
      spots, in at least one sample. ceil ensures min_cells >= 1 when the fraction > 0, so
      genes with zero expression always fail this threshold.
    - min_count_spots_ratio: keep a gene whose (total counts / expressed spots) meets the
      threshold in at least one sample. Genes absent from a sample are neutral (neither pass
      nor fail).

    Both criteria are per-sample: clearing one in a single sample is enough, so a gene
    confined to one sample is retained on the strength of that sample alone.

    When both criteria are requested a gene must satisfy both, though not necessarily in the
    same sample. Computing the ratio test on the full matrix is equivalent to running it after
    the frequency filter: subsetting genes (columns) does not change the per-gene
    expressed/total counts of the surviving genes. Operates on sparse matrices without
    densification.
    """
    sample_ids = adata.obs["sample_id"].unique()

    freq_preserved = np.zeros(adata.n_vars, dtype=int)
    ratio_preserved = np.zeros(adata.n_vars, dtype=int)

    for sid in sample_ids:
        sample_mask = (adata.obs["sample_id"] == sid).values
        X_sample = adata.X[sample_mask, :]  # one slice per sample
        expressed_per_gene = np.asarray((X_sample > 0).sum(axis=0)).flatten()

        if min_spots_per_gene is not None:
            min_cells = max(1, int(np.ceil(X_sample.shape[0] * min_spots_per_gene)))
            freq_preserved += (expressed_per_gene >= min_cells).astype(int)

        if min_count_spots_ratio is not None:
            total_counts = np.asarray(X_sample.sum(axis=0)).flatten()
            expressed_mask = expressed_per_gene > 0
            ratio_ok = np.zeros(adata.n_vars, dtype=bool)
            ratio_ok[expressed_mask] = (
                total_counts[expressed_mask]
                >= min_count_spots_ratio * expressed_per_gene[expressed_mask]
            )
            ratio_preserved += ratio_ok.astype(int)

    # Passing in a single sample is enough: a gene that clears the per-sample threshold
    # somewhere is retained, regardless of how many samples it is detected in.
    gene_mask = np.ones(adata.n_vars, dtype=bool)
    if min_spots_per_gene is not None:
        gene_mask &= freq_preserved > 0
    if min_count_spots_ratio is not None:
        gene_mask &= ratio_preserved > 0

    return adata[:, gene_mask].copy()
