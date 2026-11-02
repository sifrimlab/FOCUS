"""Optional raw-layer preservation and count normalization shared by ST outputs."""

from anndata import AnnData
import scanpy as sc
from focus.runtime.reporting import StepReporter


def normalize_counts(
    adata: AnnData,
    total_counts_normalize: bool,
    log1p_transform: bool,
    target_sum: float,
    reporter: StepReporter | None = None,
) -> None:
    """Preserve raw counts and apply the requested transforms in their original order."""
    will_normalize = total_counts_normalize or log1p_transform
    if will_normalize:
        adata.layers["raw"] = adata.X.copy()
    if total_counts_normalize:
        if reporter:
            reporter.detail("Normalizing total counts")
        sc.pp.normalize_total(adata, target_sum=target_sum, inplace=True)
    if log1p_transform:
        if reporter:
            reporter.detail("Applying log1p transformation")
        sc.pp.log1p(adata)
