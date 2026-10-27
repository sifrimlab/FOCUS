"""Lightweight ST clustering settings shared by CLI, GUI and preprocessing."""
import numpy as np

METHOD_VERSION = "st-units-2"
DEFAULTS = {
    "clustering_min_counts": 50,
    "clustering_min_genes": 20,
    "clustering_n_top_genes": 2000,
    "clustering_n_pcs": 50,
    "clustering_n_neighbors": 15,
    "clustering_resolution": 0.5,
    "clustering_random_state": 0,
    "clustering_max_units": 250_000,
}
# Lower bounds of the integer settings (all others are >= 1).
_MINIMUM = {"clustering_random_state": 0, "clustering_max_units": 1_000}


def clustering_settings(settings):
    result = {key: settings.get(key, default) for key, default in DEFAULTS.items()}
    for key, value in result.items():
        if key == "clustering_resolution":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a positive finite number")
        elif isinstance(value, bool) or not isinstance(value, int) or value < _MINIMUM.get(key, 1):
            raise ValueError(f"{key} must be an integer >= {_MINIMUM.get(key, 1)}")
    return result
