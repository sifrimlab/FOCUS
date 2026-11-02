"""Cache readout for MSI preprocessing."""

import numpy as np

import anndata as ad
import pandas as pd


def read_cached_sample(
    path: str,
) -> tuple[pd.DataFrame, list[float], dict[str, np.ndarray]] | None:
    """
    Read .var (mz, mz_mode, lipid_annotation), .uns["spot_size"] and .uns["recalibration_reference"]
    from a per-sample h5ad without materializing X. Returns None if the file is missing or unreadable.
    """
    try:
        adata = ad.read_h5ad(path, backed="r")
        try:
            var_subset = adata.var[["mz", "mz_mode", "lipid_annotation"]].copy()
            spot_size = list(adata.uns["spot_size"])
            reference = {
                str(mode): np.asarray(masses, dtype=np.float64)
                for mode, masses in adata.uns["recalibration_reference"].items()
            }
        finally:
            if adata.file is not None:
                adata.file.close()
        return var_subset, spot_size, reference
    except Exception:
        return None
