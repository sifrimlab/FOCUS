"""Cache readout for MSI preprocessing."""

import numpy as np

import anndata as ad
import pandas as pd


def read_cached_sample(
    path: str,
) -> (
    tuple[
        pd.DataFrame,
        list[float],
        dict[str, np.ndarray],
        dict[str, dict[str, np.ndarray | float]],
    ]
    | None
):
    """
    Read .var (mz, mz_sigma_ppm, mz_mode, lipid_annotation), .uns["spot_size"], .uns["recalibration_reference"] and .uns["recalibration_pool"]
    from a per-sample h5ad without materializing X. Returns None if the file is missing or unreadable.
    """
    try:
        adata = ad.read_h5ad(path, backed="r")
        try:
            var_subset: pd.DataFrame = adata.var[
                ["mz", "mz_sigma_ppm", "mz_mode", "lipid_annotation"]
            ].copy()
            spot_size: list[float] = [float(v) for v in adata.uns["spot_size"]]
            reference = {
                str(mode): np.asarray(masses, dtype=np.float64)
                for mode, masses in adata.uns["recalibration_reference"].items()
            }
            pool: dict[str, dict[str, np.ndarray | float]] = {
                str(mode): {
                    "pool": np.asarray(entry["pool"], dtype=np.float64),
                    "shift_ppm": float(entry["shift_ppm"]),
                }
                for mode, entry in adata.uns.get("recalibration_pool", {}).items()
            }
        finally:
            if adata.file is not None:
                adata.file.close()
        return var_subset, spot_size, reference, pool
    except Exception:
        return None
