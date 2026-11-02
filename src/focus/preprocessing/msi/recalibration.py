"""Estimate per-spectrum mass offsets/slopes and apply dtype-preserving recalibrated copies."""

from numpy.typing import NDArray

from focus.preprocessing.msi.types import RecalibrationSummary

import numpy as np

import warnings
from dataclasses import dataclass


from focus.preprocessing.msi.settings import (
    _MAD_TO_SIGMA,
    _RECAL_COARSE_FACTOR,
    _RECAL_LINE_SMOOTHING,
    _RECAL_MIN_LINE_MATCHES,
    _RECAL_MIN_PIXEL_MATCHES,
    _RECAL_OUTLIER_SIGMA,
    _RECAL_SLOPE_MIN_REFERENCES,
    _RECAL_SLOPE_MIN_SPAN,
)
from focus.preprocessing.msi.matching import (
    _match_all,
    recalibration_search_ppm,
    sample_offset_ppm,
)
from focus.preprocessing.msi.features import (
    feature_bin_width,
    find_features,
    peak_histogram,
    recurrence_map,
)


def _smooth_and_fill_lines(values: np.ndarray) -> np.ndarray:
    """
    Running median over `_RECAL_LINE_SMOOTHING` scan lines (NaN = no estimate), then linear
    interpolation over lines that are still empty, holding the edge values. Returns zeros when no
    line has an estimate.
    """
    if values.size == 0 or not np.isfinite(values).any():
        return np.zeros_like(values, dtype=np.float64)
    half = _RECAL_LINE_SMOOTHING // 2
    padded = np.pad(values.astype(np.float64), half, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, _RECAL_LINE_SMOOTHING)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows
        smoothed = np.nanmedian(windows, axis=1)
    valid = np.isfinite(smoothed)
    index = np.arange(values.size)
    return np.interp(index, index[valid], smoothed[valid])


def neighbourhood_nanmedian(
    values: np.ndarray, pixel_coordinates: np.ndarray
) -> np.ndarray:
    """
    3 x 3 raster nan-median of a per-spectrum value (NaN = invalid), including the spectrum itself.

    `pixel_coordinates` is the (N, 2) integer raster grid of the spectra (any origin). The result is
    NaN where no spectrum of the neighbourhood holds a valid value.
    """
    n_spectra = values.size
    result = np.full(n_spectra, np.nan, dtype=np.float64)
    if n_spectra == 0:
        return result

    rows = (pixel_coordinates[:, 0] - pixel_coordinates[:, 0].min()).astype(np.int64)
    cols = (pixel_coordinates[:, 1] - pixel_coordinates[:, 1].min()).astype(np.int64)
    height, width = int(rows.max()) + 1, int(cols.max()) + 1
    index_dtype = np.int32 if n_spectra < np.iinfo(np.int32).max else np.int64
    spectrum_at = np.full((height, width), -1, dtype=index_dtype)
    spectrum_at[rows, cols] = np.arange(n_spectra, dtype=index_dtype)
    values = values.astype(np.float64, copy=False)

    shifts = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)]
    for start in range(0, n_spectra, 1_000_000):
        stop = min(start + 1_000_000, n_spectra)
        r, c = rows[start:stop], cols[start:stop]
        stack = np.full((len(shifts), stop - start), np.nan, dtype=np.float64)
        for k, (dr, dc) in enumerate(shifts):
            rr, cc = r + dr, c + dc
            inside = (rr >= 0) & (rr < height) & (cc >= 0) & (cc < width)
            neighbour = np.full(stop - start, -1, dtype=np.int64)
            neighbour[inside] = spectrum_at[rr[inside], cc[inside]]
            has = neighbour >= 0
            stack[k, has] = values[neighbour[has]]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN neighbourhoods
            result[start:stop] = np.nanmedian(stack, axis=0)
    return result


@dataclass
class MsiRecalibration:
    """
    Per-spectrum m/z recalibration of one sample and ion mode.

    The ppm error of spectrum i at mass m is `offset_ppm[i] + slope_ppm_per_da[i] * (m - centre_mz)`,
    and the corrected mass is `m / (1 + error * 1e-6)`.
    """

    offset_ppm: NDArray[
        np.float64
    ]  # (n_spectra,) float64: line offset + pixel residual at centre_mz
    slope_ppm_per_da: NDArray[
        np.float64
    ]  # (n_spectra,) float64: line slope, 0 when not supported
    centre_mz: float  # mean reference m/z
    matches: NDArray[
        np.int16
    ]  # (n_spectra,) int16: references matched in the pixel-level pass
    summary: RecalibrationSummary

    @classmethod
    def identity(cls, n_spectra: int) -> "MsiRecalibration":
        """Create a zero-offset, zero-slope recalibration model for the requested spectrum count."""
        zeros = np.zeros(n_spectra, dtype=np.float64)
        return cls(zeros, zeros.copy(), 0.0, np.zeros(n_spectra, dtype=np.int16), {})

    def apply(
        self, mz_list: list[np.ndarray], indices: np.ndarray | None = None
    ) -> list[np.ndarray]:
        """Recalibrated copies of `mz_list`; `indices[k]` is the spectrum index of `mz_list[k]` (default: k)."""
        selected_indices = range(len(mz_list)) if indices is None else indices
        corrected = []
        for mz, i in zip(mz_list, selected_indices):
            error_ppm = self.offset_ppm[i] + self.slope_ppm_per_da[i] * (
                mz.astype(np.float64) - self.centre_mz
            )
            corrected.append(
                (mz / (1.0 + error_ppm * 1e-6)).astype(mz.dtype, copy=False)
            )
        return corrected


def estimate_recalibration(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    pixel_coordinates: np.ndarray,
    line_axis: int,
    reference_mz: np.ndarray,
    mass_tolerance: float,
) -> MsiRecalibration:
    """
    Estimate the per-spectrum m/z recalibration of one sample and ion mode.

    1. Line level. Every reference is matched in every spectrum within the wide window
       `recalibration_search_ppm(mass_tolerance)`, centred on the sample's offset
       (:func:`sample_offset_ppm`); lines still without an estimate are matched again around the
       drift of their estimated neighbours until no further line is estimated, so drift larger than
       the window is followed line by line. Per scan line, the per-reference median ppm
       errors are fitted by `a + b * (reference - centre_mz)`; `b` is kept only when enough
       references spanning enough of the m/z range are matched, and is fitted robustly (one
       outlier-rejection pass at `_RECAL_OUTLIER_SIGMA` robust standard deviations). `b` and `a`
       are smoothed by a running median over lines and lines without an estimate are filled by
       interpolation, so every line receives a correction.
    2. Pixel level. References are matched again within the same wide window, now centred on the
       line prediction, so drift along a line (e.g. laser decay within the row) stays in reach of
       the row ends. The references are isolated within that window (see
       _CalibrationReferenceSelector), so the width does not add ambiguity. Each spectrum's
       residual is the median residual over its matched references that lie within `mass_tolerance`
       of their median (at least `_RECAL_MIN_PIXEL_MATCHES` of them, so chance matches do not count),
       then the 3 x 3 raster nan-median of the residuals is taken; spectra with no valid neighbour
       keep the line model alone.

    Parameters
    ----------
    mz_list, intensity_list : list[np.ndarray]
            One array per spectrum.
    pixel_coordinates : np.ndarray
            (N, 2) integer raster coordinates of the spectra; any origin (0- or 1-based).
    line_axis : int
            Column of `pixel_coordinates` that indexes the scan lines.
    reference_mz : np.ndarray
            Ascending reference m/z values.
    mass_tolerance : float
            Mass tolerance in ppm; sets the search window (`recalibration_search_ppm`).

    A match must reach `_SIGNAL_TO_NOISE` times its spectrum's noise (see `noise_threshold`), the
    noise peaks being those outside the sample's recurrent m/z ranges (:func:`recurrence_map` of the
    sample's own features, :func:`find_features`).
    """
    n_spectra = len(mz_list)
    reference_mz = np.sort(np.asarray(reference_mz, dtype=np.float64))
    if n_spectra == 0 or reference_mz.size == 0:
        return MsiRecalibration.identity(n_spectra)

    n_ref = reference_mz.size
    centre_mz = float(reference_mz.mean())
    reference_offset = reference_mz - centre_mz
    reference_span = float(reference_mz[-1] - reference_mz[0])
    pixel_coordinates = np.asarray(pixel_coordinates)
    line = (
        pixel_coordinates[:, line_axis] - pixel_coordinates[:, line_axis].min()
    ).astype(np.int64)
    n_lines = int(line.max()) + 1

    # Pass 1, per scan line and reference: the median error of the matches within the search window.
    # The window is first centred on the sample's offset (sample_offset_ppm), which finds calibrants
    # that the whole sample carries beyond the window. Lines still without an estimate are then
    # searched again around the drift of their estimated neighbours (running median, interpolated and
    # held at the edges), until no further line is estimated: drift larger than the window is
    # followed line by line, as long as neighbouring lines differ by less than the window.
    search_ppm = recalibration_search_ppm(mass_tolerance)
    log_bin_width = feature_bin_width(mass_tolerance)
    recurrence = recurrence_map(
        find_features(
            *peak_histogram(mz_list, log_bin_width), n_spectra, mass_tolerance
        ),
        log_bin_width,
        search_ppm,
    )
    start_ppm = sample_offset_ppm(
        mz_list,
        intensity_list,
        reference_mz,
        _RECAL_COARSE_FACTOR * search_ppm,
        mass_tolerance,
        recurrence,
    )
    line_median = np.full((n_lines, n_ref), np.nan, dtype=np.float64)
    line_matches = np.zeros(n_lines, dtype=np.int64)
    estimated = np.zeros(n_lines, dtype=bool)
    todo = np.arange(n_spectra)
    centre = np.full(n_spectra, start_ppm)
    while todo.size:
        errors = _match_all(
            [mz_list[i] for i in todo],
            [intensity_list[i] for i in todo],
            reference_mz,
            lambda start, stop: np.repeat(centre[start:stop, None], n_ref, axis=1),
            search_ppm,
            recurrence,
        )
        redo = np.unique(line[todo])
        line_median[redo] = np.nan
        line_matches[redo] = 0
        spectrum_idx, ref_idx = np.nonzero(np.isfinite(errors))
        if spectrum_idx.size:
            point_line = line[todo[spectrum_idx]]
            key = point_line * n_ref + ref_idx
            order = np.argsort(key, kind="stable")
            key, point_error = (
                key[order],
                errors[spectrum_idx, ref_idx].astype(np.float64)[order],
            )
            unique_keys, starts = np.unique(key, return_index=True)
            bounds = np.append(starts, key.size)
            for k, flat_key in enumerate(unique_keys):
                line_median[flat_key // n_ref, flat_key % n_ref] = np.median(
                    point_error[bounds[k] : bounds[k + 1]]
                )
            line_matches += np.bincount(point_line, minlength=n_lines)
        del errors
        now = (line_matches >= _RECAL_MIN_LINE_MATCHES) & np.isfinite(line_median).any(
            axis=1
        )
        if not (now & ~estimated).any():
            break
        estimated = now
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # lines without matches
            drift = _smooth_and_fill_lines(
                np.where(estimated, np.nanmedian(line_median, axis=1), np.nan)
            )
        todo = np.flatnonzero(~estimated[line])
        centre = drift[line[todo]]

    # Line slope, where the matched references support it.
    slope = np.full(n_lines, np.nan, dtype=np.float64)
    for line_index in range(n_lines):
        matched = np.isfinite(line_median[line_index])
        if (
            line_matches[line_index] < _RECAL_MIN_LINE_MATCHES
            or matched.sum() < _RECAL_SLOPE_MIN_REFERENCES
        ):
            continue
        x, y = reference_offset[matched], line_median[line_index, matched]
        if (
            reference_span <= 0
            or (x.max() - x.min()) < _RECAL_SLOPE_MIN_SPAN * reference_span
        ):
            continue
        b, a = np.polyfit(x, y, 1)
        residual = y - (a + b * x)
        spread = _MAD_TO_SIGMA * np.median(np.abs(residual - np.median(residual)))
        keep = (
            np.abs(residual) <= _RECAL_OUTLIER_SIGMA * spread
            if spread > 0
            else np.ones_like(residual, dtype=bool)
        )
        if (
            keep.sum() >= _RECAL_SLOPE_MIN_REFERENCES
            and (x[keep].max() - x[keep].min())
            >= _RECAL_SLOPE_MIN_SPAN * reference_span
        ):
            b, a = np.polyfit(x[keep], y[keep], 1)
        slope[line_index] = b
    slope_supported = int(np.isfinite(slope).sum())
    slope = _smooth_and_fill_lines(slope)

    # Line offset given the slope: median over references of the slope-corrected errors.
    offset = np.full(n_lines, np.nan, dtype=np.float64)
    for line_index in range(n_lines):
        matched = np.isfinite(line_median[line_index])
        if line_matches[line_index] >= _RECAL_MIN_LINE_MATCHES and matched.any():
            offset[line_index] = np.median(
                line_median[line_index, matched]
                - slope[line_index] * reference_offset[matched]
            )
    lines_present = np.zeros(n_lines, dtype=bool)
    lines_present[line] = True
    lines_estimated = int((np.isfinite(offset) & lines_present).sum())
    offset = _smooth_and_fill_lines(offset)

    # Pass 2: the same window, centred on the line prediction.
    def predicted(start: int, stop: int) -> np.ndarray:
        """Return per-reference ppm errors for the requested spectrum slice."""
        return (
            offset[line[start:stop], None]
            + slope[line[start:stop], None] * reference_offset[None, :]
        )

    fine = _match_all(
        mz_list, intensity_list, reference_mz, predicted, search_ppm, recurrence
    )
    matches = np.isfinite(fine).sum(axis=1).astype(np.int16)
    # A spectrum's residual counts only when enough of its matches agree within one mass tolerance of
    # their median: chance matches (noise peaks in a spectrum without calibrants) spread over the
    # whole window and rarely agree.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # spectra without matches
        deviation = fine - predicted(0, n_spectra)
        residual = np.nanmedian(deviation, axis=1)
        agreeing = (np.abs(deviation - residual[:, None]) <= mass_tolerance).sum(axis=1)
        residual = np.nanmedian(
            np.where(
                np.abs(deviation - residual[:, None]) <= mass_tolerance,
                deviation,
                np.nan,
            ),
            axis=1,
        )
    del fine, deviation
    residual[agreeing < _RECAL_MIN_PIXEL_MATCHES] = np.nan
    own = np.isfinite(residual)
    smoothed = neighbourhood_nanmedian(residual, pixel_coordinates)
    filled = np.isfinite(smoothed)
    smoothed[~filled] = 0.0

    line_offset = offset[line]
    summary: RecalibrationSummary = {
        "lines": int(lines_present.sum()),
        "lines_estimated": lines_estimated,
        "lines_with_slope": slope_supported,
        "line_offset_ppm": (float(line_offset.min()), float(line_offset.max())),
        "pixel_residual_ppm": float(
            _MAD_TO_SIGMA * np.median(np.abs(residual[own] - np.median(residual[own])))
        )
        if own.any()
        else 0.0,
        "pixels_matched": float(own.mean()),
        "pixels_filled": float((filled & ~own).mean()),
    }
    return MsiRecalibration(
        offset_ppm=line_offset + smoothed,
        slope_ppm_per_da=slope[line],
        centre_mz=centre_mz,
        matches=matches,
        summary=summary,
    )


def describe_recalibration(model: MsiRecalibration) -> str:
    """One-line summary of a recalibration for the activity log."""
    s = model.summary
    if not s:
        return "no recalibration references"
    low, high = s["line_offset_ppm"]
    return (
        f"line offset {low:+.1f} to {high:+.1f} ppm, pixel residual {s['pixel_residual_ppm']:.1f} ppm, "
        f"{s['lines_estimated']}/{s['lines']} lines estimated, slope on {s['lines_with_slope']} lines, "
        f"{100 * s['pixels_matched']:.0f}% pixels matched, {100 * s['pixels_filled']:.0f}% filled from neighbours"
    )
