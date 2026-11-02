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


def annotate_masses(
    database: pd.DataFrame | None,
    mz_vector: NDArray[np.float32],
    ion_mode: str,
    mass_tolerance: int,
) -> NDArray[np.str_]:
    """
    Annotate the reference M/Z vector using the lipid annotation database.

    Parameters
    ----------
    database : pd.DataFrame or None
            Lipid names, masses and ion modes. None raises the original ValueError.
    mz_vector : np.ndarray[np.float32]
            The reference M/Z vector to annotate.
    ion_mode : MsiIonMode
            The ion mode of the M/Z vector.
    mass_tolerance : int
            The mass tolerance in ppm for matching the M/Z values.

    Returns
    -------
    np.ndarray
            Unicode annotation strings, one per reference mass, in the input order.
    """
    if database is None:
        raise ValueError("Lipid annotation database is not provided.")
    annotations = []
    for mz in mz_vector:
        # Compute the mass tolerance in Da
        tolerance_da = mz * mass_tolerance / 1e6
        # Filter the lipid DB for matching entries
        matches = database[
            (database["ionized_mass"] >= mz - tolerance_da)
            & (database["ionized_mass"] <= mz + tolerance_da)
            & (database["ion_mode"] == ion_mode)
        ]
        if matches.shape[0] > 0:
            annotation_str = "; ".join(matches["db_name"].tolist())
        else:
            annotation_str = "Unannotated"
        annotations.append(annotation_str)
    return np.array(annotations, dtype=str)
