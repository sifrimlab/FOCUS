"""Synthetic data and shared setup for msi scenarios."""

from collections.abc import Sequence
import os

import numpy as np

# Ten isolated calibrants spread over the mass range, plus one analyte that is never a reference.
CALIBRANTS = np.array(
    [
        401.2512,
        455.3021,
        512.4150,
        568.3307,
        624.4789,
        703.5751,
        766.5381,
        828.5519,
        886.6191,
        952.7022,
    ]
)
ANALYTE = 678.5070
CENTRE = 700.0


def true_error_ppm(
    line: int | np.ndarray,
    col: int | np.ndarray,
    mz: float | np.ndarray,
    n_lines: int,
    n_cols: int,
    line_drift: tuple[float, float] = (0.0, 30.0),
    row_ramp: float = 5.0,
) -> np.ndarray | float:
    """Injected drift: line drift down the rows, a 0 to `row_ramp` ppm ramp along each row, a TOF-like slope."""
    start, stop = line_drift
    return (
        start
        + (stop - start) * line / (n_lines - 1)
        + row_ramp * col / (n_cols - 1)
        + 0.01 * (mz - CENTRE)
    )


def synthetic_sample(
    n_lines: int = 30,
    n_cols: int = 40,
    dim_lines: tuple[int, ...] = (10, 11),
    missing_lines: tuple[int, ...] = (20, 21),
    line_drift: tuple[float, float] = (0.0, 30.0),
    seed: int = 0,
    row_ramp: float = 5.0,
    n_noise: int = 30,
) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """
    Drifted centroid spectra on a raster. `dim_lines` lose 98% of every intensity (a weak laser);
    `missing_lines` have no calibrant at all.
    """
    rng = np.random.default_rng(seed)
    mz_list, intensity_list, coords = [], [], []
    for line in range(n_lines):
        for col in range(n_cols):
            true = np.concatenate([CALIBRANTS, [ANALYTE]])
            error = true_error_ppm(
                line, col, true, n_lines, n_cols, line_drift, row_ramp
            ) + rng.normal(0, 0.3, true.size)
            peaks = true * (1 + error * 1e-6)
            intensity = 1e5 * rng.lognormal(
                0.0, 0.2, true.size
            )  # pixel-to-pixel variation for the clustering
            if line in missing_lines:
                peaks, intensity = peaks[-1:], intensity[-1:]  # analyte only
            noise = rng.uniform(300, 1100, n_noise)
            noise = noise[
                np.abs(noise[:, None] - true[None, :]).min(axis=1) > 200e-6 * noise
            ]
            mz = np.concatenate([peaks, noise])
            intensity = np.concatenate([intensity, rng.exponential(1e3, noise.size)])
            if line in dim_lines:
                intensity = intensity * 0.02
            order = np.argsort(mz)
            mz_list.append(mz[order])
            intensity_list.append(intensity[order].astype(np.float32))
            coords.append((line, col))
    return mz_list, intensity_list, np.array(coords, dtype=np.int32)


def ion_peaks(
    rng: np.random.Generator,
    mass: float,
    n: int,
    ppm_sd: float = 1.0,
    tail: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """`n` centroids of one ion: Gaussian m/z scatter, plus an optional (fraction, low, high) ppm tail."""
    error = rng.normal(0.0, ppm_sd, n)
    if tail is not None:
        fraction, low, high = tail
        in_tail = rng.random(n) < fraction
        error[in_tail] = rng.uniform(low, high, int(in_tail.sum()))
    return mass * (1 + error * 1e-6)


def backbone_sample(
    ions: list[tuple[float, float, float, tuple[float, float, float] | None]],
    n_spectra: int = 2000,
    n_noise: int = 0,
    seed: int = 0,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Spectra holding each (mass, detection rate, ppm_sd, tail) ion at 1e5, among `n_noise` noise centroids at about 50."""
    rng = np.random.default_rng(seed)
    columns = [
        (ion_peaks(rng, mass, n_spectra, ppm_sd, tail), rng.random(n_spectra) < rate)
        for mass, rate, ppm_sd, tail in ions
    ]
    mz_list, intensity_list = [], []
    for k in range(n_spectra):
        peaks = [mz[k] for mz, present in columns if present[k]]
        mz = np.concatenate([peaks, rng.uniform(350, 1000, n_noise)])
        intensity = np.concatenate(
            [1e5 * rng.lognormal(0, 0.2, len(peaks)), rng.exponential(50, n_noise)]
        )
        order = np.argsort(mz)
        mz_list.append(mz[order])
        intensity_list.append(intensity[order].astype(np.float32))
    return mz_list, intensity_list


# Ions of the synthetic sample kinds: resolvable masses, the share of tissue (or cell) spectra holding
# each, and a median intensity spread over two orders of magnitude.
_KIND = np.random.default_rng(7)
KIND_IONS = np.sort(np.exp(_KIND.uniform(np.log(300), np.log(1200), 150)))
KIND_IONS = KIND_IONS[
    np.concatenate(([True], np.diff(KIND_IONS) / KIND_IONS[1:] * 1e6 > 30))
]
KIND_PRESENCE = _KIND.uniform(0.02, 1.0, KIND_IONS.size)
KIND_LEVEL = _KIND.lognormal(0, 1.5, KIND_IONS.size)
MATRIX = np.array([379.0925, 568.1350, 757.1775])


def kind_sample(
    kind: str, n_spectra: int = 1500, seed: int = 0
) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """
    Spectra of one kind of sample, and which of them hold the ions: 'tissue' (ions only), 'culture'
    (ions among 1000 noise centroids), 'mixed' (every other spectrum is background: matrix ions and
    noise) or 'microgrid' (3% cells in background).
    """
    rng = np.random.default_rng(seed)
    mz_list, intensity_list, holds = [], [], []
    for k in range(n_spectra):
        cell = {"tissue": True, "culture": True, "mixed": k % 2 == 0}.get(
            kind, rng.random() < 0.03
        )
        noise = 0 if kind == "tissue" else (200 if kind == "mixed" and cell else 1000)
        parts, intensities = (
            [rng.uniform(300, 1200, noise)],
            [rng.exponential(50, noise)],
        )
        if cell:
            present = rng.random(KIND_IONS.size) < KIND_PRESENCE
            parts.append(
                KIND_IONS[present] * (1 + rng.normal(0, 1.5, present.sum()) * 1e-6)
            )
            intensities.append(
                1000 * KIND_LEVEL[present] * rng.lognormal(0, 0.3, present.sum())
            )
        else:
            parts.append(MATRIX * (1 + rng.normal(0, 1.5, MATRIX.size) * 1e-6))
            intensities.append(rng.lognormal(np.log(2000), 0.3, MATRIX.size))
        mz, intensity = np.concatenate(parts), np.concatenate(intensities)
        order = np.argsort(mz)
        mz_list.append(mz[order])
        intensity_list.append(intensity[order].astype(np.float32))
        holds.append(cell)
    return mz_list, intensity_list, np.array(holds)


def selector_sample(
    n_spectra: int = 400, seed: int = 0
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Spectra with isolated calibrants, one crowded candidate and one calibrant smeared by drift."""
    rng = np.random.default_rng(seed)
    mz_list, intensity_list = [], []
    for k in range(n_spectra):
        peaks = [400.10, 405.20, 410.30]  # frequent, low mass
        intensities = [1e5, 1e5, 1e5]
        if k % 2 == 0:
            peaks.append(900.50)  # less frequent, high mass
            intensities.append(1e5)
        peaks += [700.0, 700.0 * (1 + 20e-6)]  # crowded: competitor within the window
        intensities += [2e5, 1e5]
        peaks.append(
            600.0 * (1 + rng.uniform(-15, 15) * 1e-6)
        )  # one calibrant smeared over 30 ppm
        intensities.append(1e5)
        order = np.argsort(peaks)
        mz_list.append(np.array(peaks)[order])
        intensity_list.append(np.array(intensities)[order])
    return mz_list, intensity_list


def dense_selector_sample(
    n_spectra: int = 600, n_noise: int = 3000, seed: int = 0
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Calibrants at about 800 counts among thousands of noise centroids (about 50 counts), drifting 0 to 30 ppm."""
    rng = np.random.default_rng(seed)
    mz_list, intensity_list = [], []
    for k in range(n_spectra):
        drift = 30.0 * k / n_spectra
        calibrants = CALIBRANTS * (
            1 + (drift + rng.normal(0, 1, CALIBRANTS.size)) * 1e-6
        )
        mz = np.concatenate([calibrants, rng.uniform(350, 2000, n_noise)])
        intensity = np.concatenate(
            [800 * rng.lognormal(0, 0.3, CALIBRANTS.size), rng.exponential(50, n_noise)]
        )
        order = np.argsort(mz)
        mz_list.append(mz[order])
        intensity_list.append(intensity[order].astype(np.float32))
    return mz_list, intensity_list


def write_imzml(
    directory: str,
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    coords: np.ndarray,
    pixel_size: int = 20,
    stage: Sequence[tuple[float, float]] | np.ndarray | None = None,
    intensity_dtype: str = "<f4",
) -> None:
    """Minimal processed-mode imzML + IBD (positions are written 1-based, as the spec requires).

    `stage`, when given, holds each spectrum's stage position (x, y) in micrometres (3DPositionX/Y).
    `intensity_dtype` is "<f4", "<f8", "<i4" or "<i8".
    """
    intensity_type = {
        "<f4": "32-bit float",
        "<f8": "64-bit float",
        "<i4": "32-bit integer",
        "<i8": "64-bit integer",
    }[intensity_dtype]
    intensity_size = np.dtype(intensity_dtype).itemsize
    os.makedirs(directory, exist_ok=True)
    entries = []
    with open(os.path.join(directory, "data.ibd"), "wb") as ibd:
        ibd.write(b"\0" * 16)
        for mz, intensity in zip(mz_list, intensity_list):
            mz_offset = ibd.tell()
            ibd.write(mz.astype("<f8").tobytes())
            intensity_offset = ibd.tell()
            ibd.write(intensity.astype(intensity_dtype).tobytes())
            entries.append((mz.size, mz_offset, intensity_offset))

    def array(ref: str, n: int, size: int, offset: int) -> str:
        """Return an imzML binary-array element describing the exact length, element size and IBD offset."""
        return (
            f'<binaryDataArray encodedLength="0"><referenceableParamGroupRef ref="{ref}"/>'
            f'<cvParam name="external array length" value="{n}"/>'
            f'<cvParam name="external encoded length" value="{n * size}"/>'
            f'<cvParam name="external offset" value="{offset}"/><binary/></binaryDataArray>'
        )

    spectra = []
    for k, ((line, col), (n, mz_offset, intensity_offset)) in enumerate(
        zip(coords, entries)
    ):
        position = (
            ""
            if stage is None
            else f'<userParam name="3DPositionX" value="{stage[k][0]}"/>'
            f'<userParam name="3DPositionY" value="{stage[k][1]}"/>'
        )
        spectra.append(
            f'<spectrum id="s{k}" index="{k}" defaultArrayLength="0"><scanList count="1"><scan>'
            f'<cvParam name="position x" value="{col + 1}"/><cvParam name="position y" value="{line + 1}"/>'
            f'{position}</scan></scanList><binaryDataArrayList count="2">'
            f"{array('mzArray', n, 8, mz_offset)}{array('intensities', n, intensity_size, intensity_offset)}"
            f"</binaryDataArrayList></spectrum>"
        )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><mzML xmlns="http://psi.hupo.org/ms/mzml" version="1.1">'
        '<referenceableParamGroupList count="2">'
        '<referenceableParamGroup id="mzArray"><cvParam name="64-bit float" value=""/></referenceableParamGroup>'
        f'<referenceableParamGroup id="intensities"><cvParam name="{intensity_type}" value=""/></referenceableParamGroup>'
        '</referenceableParamGroupList><scanSettingsList count="1"><scanSettings id="ss">'
        f'<cvParam name="pixel size x" value="{pixel_size}"/><cvParam name="pixel size y" value="{pixel_size}"/>'
        '<cvParam name="horizontal line scan" value=""/></scanSettings></scanSettingsList>'
        f'<run id="r"><spectrumList count="{len(spectra)}">{"".join(spectra)}</spectrumList></run></mzML>'
    )
    with open(os.path.join(directory, "data.imzML"), "w") as f:
        f.write(xml)


class StructuredTissue:
    """Smooth and sharp composition fields over a tissue frame (micrometres), seen by either polarity.

    Four latent fields, each a sum of Gaussian blobs plus piecewise-constant Voronoi regions, set the
    intensities of twelve peaks per polarity through polarity-specific loadings, so the two polarities
    share anatomy but not intensity relations. Each spectrum also carries its tissue position as two
    m/z values above the pairing bands (4000 + x/1000, 6000 + y/1000), never used for pairing.
    """

    POSITIVE_PEAKS = np.linspace(400.0, 1000.0, 12)
    NEGATIVE_PEAKS = np.linspace(430.0, 1030.0, 12)

    def __init__(self, extent: tuple[float, float], seed: int = 0) -> None:
        """Draw the fields over [0, extent] with a margin, and the polarity loadings."""
        rng = np.random.default_rng(seed)
        width, height = extent
        self.blobs = [
            (
                rng.uniform(-200, width + 200, 80),
                rng.uniform(-200, height + 200, 80),
                rng.uniform(15, 60, 80),
                rng.normal(0, 1, 80),
            )
            for _ in range(4)
        ]
        self.seeds = np.column_stack(
            [rng.uniform(-200, width + 200, 14), rng.uniform(-200, height + 200, 14)]
        )
        self.levels = rng.normal(0, 1, (14, 4))
        self.loadings = {
            "pos": rng.normal(0, 1, (12, 4)),
            "neg": rng.normal(0, 1, (12, 4)),
        }
        self.rng = rng

    def fields(self, xy: np.ndarray) -> np.ndarray:
        """Latent field values (n, 4) at tissue positions ``xy``."""
        out = np.zeros((len(xy), 4))
        for k, (cx, cy, sigma, weight) in enumerate(self.blobs):
            d2 = (xy[:, :1] - cx) ** 2 + (xy[:, 1:] - cy) ** 2
            out[:, k] = (weight * np.exp(-d2 / (2 * sigma**2))).sum(axis=1)
        region = np.argmin(
            ((xy[:, None, :] - self.seeds[None]) ** 2).sum(axis=2), axis=1
        )
        return out + 0.7 * self.levels[region]

    def spectra(
        self, xy: np.ndarray, mode: str
    ) -> tuple[list[np.ndarray], list[np.ndarray]]:
        """Centroid spectra of one polarity at tissue positions ``xy``."""
        peaks = self.POSITIVE_PEAKS if mode == "pos" else self.NEGATIVE_PEAKS
        log_intensity = self.fields(xy) @ self.loadings[mode].T
        log_intensity += self.rng.normal(0, 0.3, log_intensity.shape)
        mz_list, intensity_list = [], []
        for (x, y), row in zip(xy, log_intensity):
            mz_list.append(np.concatenate([peaks, [4000 + x / 1000, 6000 + y / 1000]]))
            intensity_list.append(np.concatenate([1e4 * np.exp(row), [1e3, 1e3]]))
        return mz_list, intensity_list


def tissue_position(mz: np.ndarray) -> tuple[float, float]:
    """Tissue position (x, y) a StructuredTissue spectrum carries."""
    return (
        float((mz[(mz > 3500) & (mz < 5000)][0] - 4000) * 1000),
        float((mz[mz > 5000][0] - 6000) * 1000),
    )
