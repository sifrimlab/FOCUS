"""Profile-spectrum fixtures: rendered peaks, imzML writing with a declared type, small rasters."""

import re
from pathlib import Path

import numpy as np

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.sample import MsiSample
from focus.preprocessing.msi.peak_stats import GridModel
from tests.msi.data import write_imzml

STEP = 0.004


def grid_for(step: float = STEP) -> GridModel:
    """A constant grid step over 300 to 1000 Da."""
    return GridModel(np.log(300.0), 64 / np.log(1000.0 / 300.0), np.full(64, step))


COORDS = np.array([(line, col) for line in range(3) for col in range(10)], np.int32)


def profile_spectrum(
    peak_mz: np.ndarray,
    peak_height: np.ndarray,
    points_per_peak: float,
    step: float = 0.004,
    low: float = 300.0,
    high: float = 1000.0,
    baseline: float = 20.0,
    noise: float = 8.0,
    shape: str = "gaussian",
    trim: bool = False,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Render known peaks on a regular m/z grid as a profile spectrum (m/z float64, intensity float32).

    `points_per_peak` is the full width at half maximum in grid steps. `shape` is "gaussian",
    "lorentzian" or "asymmetric" (the right half twice as wide). `trim` keeps only the grid points
    within three widths of a peak (zero-trimmed islands, no baseline outside them).
    """
    rng = rng or np.random.default_rng(0)
    mz = np.arange(low, high, step)
    width = points_per_peak * step
    reach = int(np.ceil(3 * width / step))
    y = np.zeros_like(mz)
    keep = np.zeros(mz.size, dtype=bool)
    for m, h in zip(peak_mz, peak_height):
        centre = int(round((m - low) / step))
        window = slice(max(centre - reach, 0), min(centre + reach + 1, mz.size))
        keep[window] = True
        x = (mz[window] - m) / width
        if shape == "lorentzian":
            y[window] += h / (1 + 4 * x**2)
        else:
            x = np.where(x > 0, x / 2, x) if shape == "asymmetric" else x
            y[window] += h * np.exp(-4 * np.log(2) * x**2)
    y += baseline + (rng.normal(0, noise, mz.size) if noise else 0.0)
    y = np.maximum(y, 0)
    if trim:
        return mz[keep], y[keep].astype(np.float32)
    return mz, y.astype(np.float32)


def write_profile_imzml(
    directory: Path,
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    coords: np.ndarray,
    declared: str | None = "profile",
    continuous: bool = False,
) -> None:
    """Write an imzML that declares its spectrum type ("profile", "centroid" or None).

    `continuous` makes every spectrum point to the first one's m/z array.
    """
    write_imzml(str(directory), mz_list, intensity_list, coords)
    path = directory / "data.imzML"
    text = path.read_text()
    if declared:
        text = text.replace(
            '<cvParam name="32-bit float" value=""/>',
            f'<cvParam name="32-bit float" value=""/><cvParam name="{declared} spectrum" value=""/>',
        )
    if continuous:
        first: list[str] = []

        def share(match: re.Match[str]) -> str:
            """Point every m/z array at the first spectrum's offset."""
            first.append(first[0] if first else match.group(2))
            return f"{match.group(1)}{first[0]}{match.group(3)}"

        text = re.sub(
            r'(ref="mzArray"/>.*?name="external offset" value=")(\d+)(")',
            share,
            text,
        )
    path.write_text(text)


def write_profile(
    directory: Path,
    points: float = 6.0,
    step: float = 0.004,
    declared: str | None = "profile",
    trim: bool = True,
    continuous: bool = False,
    nan_in: int | None = None,
) -> Path:
    """Write a 30-spectrum raster of profile spectra around four known peaks."""
    rng = np.random.default_rng(7)
    mz_list, intensity_list = [], []
    for k in range(COORDS.shape[0]):
        peaks = np.array([420.1234, 512.3456, 650.4321, 780.7654]) * (
            1 + 1e-6 * rng.normal(0, 0.3, 4)
        )
        mz, y = profile_spectrum(
            peaks,
            1e4 * rng.lognormal(0, 0.3, 4),
            points,
            step=step,
            low=400.0,
            high=800.0,
            baseline=0.0 if trim else 20.0,
            noise=0.0 if trim else 5.0,
            trim=trim and not continuous,
            rng=rng,
        )
        if nan_in == k:
            y[5] = np.nan
        mz_list.append(mz)
        intensity_list.append(y)
    write_profile_imzml(
        directory, mz_list, intensity_list, COORDS, declared, continuous
    )
    return directory


def sample_for(root: Path, directory: Path) -> MsiSample:
    """An MsiSample over one positive-mode directory laid out as the pipeline expects."""
    target = root / "S" / "MSI" / "pos"
    target.parent.mkdir(parents=True, exist_ok=True)
    directory.rename(target)
    return MsiSample(str(root), "S", "MSI", ion_mode=MsiIonMode.POSITIVE)
