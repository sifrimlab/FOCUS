"""Select isolated recurring sample ions suitable for mass recalibration."""

import numpy as np
from focus.runtime.reporter_context import get_reporter

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.settings import _RECAL_SLOPE_MIN_SPAN, _SIGNAL_TO_NOISE
from focus.preprocessing.msi.matching import (
    _flatten_spectra,
    _peak_blocks,
    match_references,
    recalibration_search_ppm,
)
from focus.preprocessing.msi.features import (
    feature_bin_width,
    find_features,
    peak_histogram,
    recurrence_map,
)


def _replay_matching(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    centre_mz: np.ndarray,
    low_mz: np.ndarray,
    high_mz: np.ndarray,
    window_ppm: float,
    recurrence: tuple[int, np.ndarray, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    The calibrant matching of recalibration (:func:`match_references`, noise gate `recurrence`) replayed
    with every candidate at `centre_mz` as a reference, its window `window_ppm` ppm wide on either side.

    Returns, per candidate, the spectra whose match lies within its own feature [`low_mz`, `high_mz`]
    (hits), those whose match lies elsewhere in the window (misses: another ion was taken), and the
    m/z sum of the hits.
    """
    hits = np.zeros(centre_mz.size, dtype=np.int64)
    misses = np.zeros(centre_mz.size, dtype=np.int64)
    hit_sum = np.zeros(centre_mz.size, dtype=np.float64)
    for start, stop in _peak_blocks(mz_list):
        flat_mz, flat_intensity, offsets = _flatten_spectra(
            mz_list[start:stop], intensity_list[start:stop]
        )
        if flat_mz.size == 0:
            continue
        errors = match_references(
            flat_mz,
            flat_intensity,
            offsets,
            centre_mz,
            np.zeros((stop - start, centre_mz.size)),
            float(window_ppm),
            _SIGNAL_TO_NOISE,
            *recurrence,
        )
        matched = np.isfinite(errors)
        mz = centre_mz * (1 + np.where(matched, errors, 0.0).astype(np.float64) * 1e-6)
        own = matched & (mz >= low_mz) & (mz <= high_mz)
        hits += own.sum(axis=0)
        misses += (matched & ~own).sum(axis=0)
        hit_sum += np.where(own, mz, 0.0).sum(axis=0)
    return hits, misses, hit_sum


class _CalibrationReferenceSelector:
    """
    Deterministic selection of the reference M/Z values one sample is recalibrated against.

    One selector serves one sample: the references are ions of that sample's own spectra that the
    calibrant matching of recalibration finds reliably. Every spectrum contributes -- there is no
    subsampling and no RNG, so repeated runs on the same input return byte-identical references.

    Usage: construct with the ppm tolerance, hand over the sample's payload with :meth:`add_sample`,
    then call :meth:`select`. The payload is held (not copied) until :meth:`select` returns, because the
    selection replays the matching on it (:func:`_replay_matching`).
    """

    # Kernel FWHM, in mass tolerances, of the candidates' feature detection: half the backbone's, so
    # that two ions about half a tolerance apart are two candidates, not one.
    _KERNEL_FWHM = 0.5
    # The matching is replayed on this many of the most frequent features.
    _TRIAL_CANDIDATES = 1000
    # A candidate is isolated when at most this share of its matches takes another ion.
    _ISOLATION_MAX_WRONG = 0.1
    # Size of the candidate pool, as a multiple of the number of references requested.
    _POOL_FACTOR = 20
    # Pool members need at least this fraction of the best candidate's score, so the spread step
    # cannot fill an empty m/z segment with a rare peak.
    _POOL_MIN_RELATIVE_SCORE = 0.1

    def __init__(self, mass_tolerance: float) -> None:
        """
        Parameters
        ----------
        mass_tolerance : float
                Mass tolerance in ppm, used for the feature grid and the feature kernel.
        """
        self._mass_tolerance = mass_tolerance
        self._log_bin_width = feature_bin_width(mass_tolerance)
        self._search_ppm = recalibration_search_ppm(mass_tolerance)
        # Payload per ion mode, held until select().
        self._mz: dict[str, list[np.ndarray]] = {}
        self._intensity: dict[str, list[np.ndarray]] = {}
        # Filled by select(): per ion mode, the ascending m/z of the candidate pool (for mass_scale_shifts),
        # and for the log the number of features and of isolated trial candidates, with the trial
        # candidates' median share of spectra matched and of matches taking another ion.
        self.pool: dict[str, np.ndarray] = {}
        self.candidates: dict[str, tuple[int, int, float, float]] = {}

    def add_sample(
        self,
        sample_mz: dict[str, list[np.ndarray]],
        sample_intensity: dict[str, list[np.ndarray]],
    ) -> None:
        """
        Hand over an M/Z payload; it is held, not copied, until :meth:`select` returns.

        Parameters
        ----------
        sample_mz : dict[MsiIonMode, list[np.ndarray]]
                One M/Z array per spectrum, per ion mode present in this sample.
        sample_intensity : dict[MsiIonMode, list[np.ndarray]]
                The matching intensity arrays.
        """
        for mode, spectra in sample_mz.items():
            self._mz.setdefault(mode, []).extend(spectra)
            self._intensity.setdefault(mode, []).extend(sample_intensity[mode])

    def select(
        self,
        number_of_references: int,
        required_mz: dict[str, np.ndarray] | None = None,
    ) -> dict[str, np.ndarray]:
        """
        Select the reference M/Z values from the payload handed over.

        Per ion mode the sample's features are found (:func:`find_features`, kernel FWHM
        ``_KERNEL_FWHM`` tolerances): the maxima of its peak density that rise above the local
        background. The ``_TRIAL_CANDIDATES`` features holding the most peaks are trial candidates, and
        the calibrant matching of recalibration is replayed on the sample with each of them as a
        reference (:func:`_replay_matching`): the window, ``recalibration_search_ppm`` wide on either
        side, is centred on the feature, and the most intense peak in it that passes the noise gate is
        taken. A match within the candidate's own feature (up to the valleys toward its neighbours) is a
        hit; one elsewhere in the window took another ion. A candidate is isolated when at most
        ``_ISOLATION_MAX_WRONG`` of its matches took another ion, and is scored by its hits per spectrum.
        Its reference m/z is the mean m/z of its hits, the sample's average position of what matching
        finds.

        Selection, in order:

        1. `required_mz` (e.g. sprayed internal standards): the feature nearest each mass within the
           search window, regardless of isolation.
        2. A pool of at most ``_POOL_FACTOR * number_of_references`` isolated candidates scoring at least
           ``_POOL_MIN_RELATIVE_SCORE`` of the best one, taken greedily in descending score; a candidate
           closer than twice the search window to an already chosen one is skipped, so no two references
           can ever match the same peak.
        3. Spread: the pool's log-m/z span is cut into `number_of_references` equal segments and
           the best pool member of each segment is taken.
        4. Fill from the pool by score until `number_of_references` are chosen.

        Ties are broken by ascending m/z, giving a total order. When required masses (known standards)
        are observed, the references are moved onto the standards' mass scale: every reference m is
        divided by (1 + d(m) * 1e-6), where d is the ppm offset of the observed standards from their
        given masses. d is a least-squares line in m when at least two standards span
        ``_RECAL_SLOPE_MIN_SPAN`` of the reference m/z span, and their median offset (a constant)
        otherwise. The pool's m/z are kept in :attr:`pool`.

        Parameters
        ----------
        number_of_references : int
                Number of references per ion mode (plus the observed required masses).
        required_mz : dict[MsiIonMode, np.ndarray] | None
                Masses that must be references when observed.

        Returns
        -------
        dict[MsiIonMode, np.ndarray]
                Ascending reference M/Z values per ion mode; an empty array for a mode without features.
        """

        recalibration_reference: dict[str, np.ndarray] = {}
        window = self._search_ppm * 1e-6
        w = self._log_bin_width

        for mode in MsiIonMode.list():
            recalibration_reference[mode] = np.array([], dtype=np.float64)
            self.pool[mode] = np.array([], dtype=np.float64)
            self.candidates[mode] = (0, 0, 0.0, 0.0)
            spectra = self._mz.get(mode, [])
            if not spectra:
                continue

            histogram = peak_histogram(spectra, w)
            features = find_features(
                *histogram, len(spectra), self._mass_tolerance, self._KERNEL_FWHM
            )
            if features.mz.size == 0:
                continue

            # Trial candidates: the most frequent features, plus the feature nearest each required mass.
            trial = np.argsort(-features.peaks, kind="stable")[: self._TRIAL_CANDIDATES]
            required = []
            if required_mz is not None and mode in required_mz:
                for mass in np.sort(np.asarray(required_mz[mode], dtype=np.float64)):
                    nearest = int(np.argmin(np.abs(features.mz - mass)))
                    if abs(features.mz[nearest] - mass) > window * mass:
                        get_reporter().warning(
                            f"Internal standard m/z {mass:.4f} ({str(mode).upper()}) is not observed; "
                            f"it is not used as a recalibration reference"
                        )
                        continue
                    required.append((nearest, float(mass)))
            trial = np.unique(
                np.concatenate([trial, [k for k, _ in required]]).astype(np.int64)
            )

            recurrence = recurrence_map(
                find_features(*histogram, len(spectra), self._mass_tolerance),
                w,
                self._search_ppm,
            )
            hits, misses, hit_sum = _replay_matching(
                spectra,
                self._intensity[mode],
                features.mz[trial],
                np.exp(features.low[trial] * w),
                np.exp((features.high[trial] + 1) * w),
                self._search_ppm,
                recurrence,
            )
            feature_mz = np.where(
                hits > 0, hit_sum / np.maximum(hits, 1), features.mz[trial]
            )
            scores = hits / len(spectra)
            isolated = (hits > 0) & (
                misses <= self._ISOLATION_MAX_WRONG * (hits + misses)
            )
            self.candidates[mode] = (
                int(features.mz.size),
                int(isolated.sum()),
                float(np.median((hits + misses) / len(spectra))),
                float(np.median(misses / np.maximum(hits + misses, 1))),
            )
            order = np.lexsort((feature_mz, -scores))
            position = {int(k): i for i, k in enumerate(trial)}

            def separated(
                candidate: tuple[int, float], others: list[tuple[int, float]]
            ) -> bool:
                """Check that a candidate mass is outside every selected reference window."""
                return all(
                    abs(candidate[1] - other[1]) > 2 * window * other[1]
                    for other in others
                )

            chosen: list[tuple[int, float]] = []  # (trial candidate, m/z)

            # 1. Required masses.
            anchors: list[tuple[float, float]] = []  # (reference m/z, given m/z)
            for feature, mass in required:
                candidate = (position[feature], float(feature_mz[position[feature]]))
                if separated(candidate, chosen):
                    chosen.append(candidate)
                    anchors.append((candidate[1], mass))

            # 2. Candidate pool.
            pool: list[tuple[int, float]] = []
            best_score = scores[isolated].max() if isolated.any() else 0.0
            for k in order:
                if (
                    len(pool) >= self._POOL_FACTOR * number_of_references
                    or scores[k] < self._POOL_MIN_RELATIVE_SCORE * best_score
                ):
                    break
                if isolated[k]:
                    candidate = (int(k), float(feature_mz[k]))
                    if separated(candidate, chosen + pool):
                        pool.append(candidate)
            self.pool[mode] = np.sort(
                np.array([candidate[1] for candidate in pool], dtype=np.float64)
            )

            # 3. Spread over the pool's log-m/z span.
            if pool:
                log_mz = np.log([candidate[1] for candidate in pool])
                edges = np.linspace(
                    log_mz.min(), log_mz.max(), number_of_references + 1
                )
                segment = np.clip(
                    np.searchsorted(edges, log_mz, side="right") - 1,
                    0,
                    number_of_references - 1,
                )
                for s in range(number_of_references):
                    members = [
                        candidate for candidate, seg in zip(pool, segment) if seg == s
                    ]
                    if members and separated(members[0], chosen):
                        chosen.append(members[0])

            # 4. Fill from the pool by score.
            for candidate in pool:
                if len(chosen) >= number_of_references:
                    break
                if separated(candidate, chosen):
                    chosen.append(candidate)

            references = np.array(
                [candidate[1] for candidate in chosen], dtype=np.float64
            )
            if anchors:
                masses = np.array([mass for _, mass in anchors])
                offsets = np.array(
                    [(reference - mass) / mass * 1e6 for reference, mass in anchors]
                )
                span = references.max() - references.min()
                if (
                    len(anchors) >= 2
                    and span > 0
                    and np.ptp(masses) >= _RECAL_SLOPE_MIN_SPAN * span
                ):
                    slope, intercept = np.polyfit(masses, offsets, 1)
                    shift_ppm = intercept + slope * references
                    description = f"{shift_ppm.min():+.1f} to {shift_ppm.max():+.1f} ppm over the references"
                else:
                    shift_ppm = float(np.median(offsets))
                    description = f"{shift_ppm:+.1f} ppm"
                references = references / (1 + shift_ppm * 1e-6)
                get_reporter().detail(
                    f"Recalibration references ({str(mode).upper()}) anchored to "
                    f"{len(anchors)} internal standard(s): {description}"
                )
            recalibration_reference[mode] = np.sort(references)

        return recalibration_reference
