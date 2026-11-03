"""Load lipid reference tables and annotate m/z values using the original ppm matching rules."""

import os
import pandas as pd
import numpy as np
from numpy.typing import NDArray


def read_lipid_database(lipid_annotation_db: str | None) -> pd.DataFrame | None:
    """Read a CSV/JSON reference table after the original existence, access and column checks."""
    # If the lipid DB is provided, check if it exists and we can read it
    if lipid_annotation_db is not None:
        if not os.path.exists(lipid_annotation_db):
            raise FileNotFoundError(
                f"Lipid annotation database {lipid_annotation_db} does not exist."
            )
        if not os.access(lipid_annotation_db, os.R_OK):
            raise PermissionError(
                f"Lipid annotation database {lipid_annotation_db} is not readable."
            )

    # Based on the data type, load the lipid annotation database
    if lipid_annotation_db is not None:
        if lipid_annotation_db.endswith(".csv"):
            database = pd.read_csv(lipid_annotation_db)
        elif lipid_annotation_db.endswith(".json"):
            database = pd.read_json(lipid_annotation_db)
        else:
            raise ValueError(
                "Invalid lipid annotation database format. Supported formats are CSV and JSON."
            )

        # Check that the required columns are present
        required_columns = ["db_name", "ionized_mass", "ion_mode"]
        if not all(column in database.columns for column in required_columns):
            raise ValueError(
                f"Lipid annotation database must contain the following columns: {required_columns}"
            )
    else:
        database = None

    return database


def _mode_masses(
    database: pd.DataFrame, ion_mode: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Database masses of one ion mode in ascending order, their row positions and names."""
    subset = database[database["ion_mode"] == ion_mode]
    masses = subset["ionized_mass"].to_numpy(dtype=np.float64)
    order = np.argsort(masses, kind="stable")
    return masses[order], order, subset["db_name"].astype(str).to_numpy()


def _match_ranges(
    masses: np.ndarray, mz: np.ndarray, mass_tolerance: float
) -> tuple[np.ndarray, np.ndarray]:
    """[start, stop) of the sorted database masses within `mass_tolerance` ppm of each m/z."""
    tolerance_da = mz * mass_tolerance / 1e6
    return (
        np.searchsorted(masses, mz - tolerance_da, side="left"),
        np.searchsorted(masses, mz + tolerance_da, side="right"),
    )


def annotate_masses(
    database: pd.DataFrame | None,
    mz_vector: NDArray[np.floating],
    ion_mode: str,
    mass_tolerance: float,
) -> NDArray[np.str_]:
    """
    Annotate m/z values with the database entries of the same ion mode within `mass_tolerance` ppm.

    Parameters
    ----------
    database : pd.DataFrame or None
            Lipid names, masses and ion modes. None raises ValueError.
    mz_vector : np.ndarray
            The m/z values to annotate.
    ion_mode : MsiIonMode
            The ion mode of the m/z values.
    mass_tolerance : float
            The mass tolerance in ppm for matching the m/z values.

    Returns
    -------
    np.ndarray
            Unicode annotation strings, one per m/z, in the input order: the matching names joined by
            "; " in database row order, or "Unannotated".
    """
    if database is None:
        raise ValueError("Lipid annotation database is not provided.")
    masses, order, names = _mode_masses(database, ion_mode)
    mz = np.asarray(mz_vector, dtype=np.float64)
    start, stop = _match_ranges(masses, mz, mass_tolerance)
    annotations = [
        "; ".join(names[np.sort(order[a:b])]) if b > a else "Unannotated"
        for a, b in zip(start, stop)
    ]
    return np.array(annotations, dtype=str)


def chance_annotations(
    database: pd.DataFrame,
    mz_vector: NDArray[np.floating],
    ion_mode: str,
    mass_tolerance: float,
    offsets: tuple[int, ...],
) -> float:
    """
    Number of m/z values expected to be annotated by chance: the mean number annotated at masses
    shifted by each of `offsets` window widths (2 x `mass_tolerance` ppm), a target-decoy estimate.
    """
    masses, _, _ = _mode_masses(database, ion_mode)
    mz = np.asarray(mz_vector, dtype=np.float64)
    if mz.size == 0 or masses.size == 0 or not offsets:
        return 0.0
    hits = []
    for k in offsets:
        start, stop = _match_ranges(
            masses, mz * (1 + 2 * k * mass_tolerance * 1e-6), mass_tolerance
        )
        hits.append(int(np.count_nonzero(stop > start)))
    return float(np.mean(hits))


def annotated_mask(
    database: pd.DataFrame | None,
    mz_vector: NDArray[np.floating],
    ion_mode: str,
    mass_tolerance: float,
) -> np.ndarray:
    """Whether each m/z matches a database entry of `ion_mode`; all False without a database."""
    mz = np.asarray(mz_vector, dtype=np.float64)
    if database is None:
        return np.zeros(mz.size, dtype=bool)
    masses, _, _ = _mode_masses(database, ion_mode)
    start, stop = _match_ranges(masses, mz, mass_tolerance)
    return stop > start
