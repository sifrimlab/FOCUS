"""Linear spectral unmixing: VCA endmembers and fully constrained least-squares abundances.

Under the linear mixing model each pixel spectrum ``x`` is a non-negative combination of ``p`` pure
component spectra (endmembers) whose abundances sum to one: ``x = E a + noise``, ``a >= 0``,
``sum(a) = 1``.

- ``vertex_component_analysis``: endmembers by Vertex Component Analysis (Nascimento and
  Bioucas-Dias 2005). The data are projected on their ``p``-dimensional signal subspace and the
  endmembers are the pixels at the extremes of successive directions orthogonal to the subspace
  already spanned. VCA assumes that every endmember occurs as a nearly pure pixel.
- ``fully_constrained_abundances``: abundances by fully constrained least squares (Heinz and Chang
  2001), the least-squares solution on the probability simplex. FOCUS solves it exactly with a
  primal active-set method (Lawson and Hanson 1974) that carries the sum-to-one constraint as an
  equality on the passive set, instead of the augmented-matrix approximation of Heinz and Chang.
"""

from __future__ import annotations

import numpy as np
from numba import njit

# Pixels per batch of the abundance solver (bounds the (N, p) intermediates).
_BATCH = 262_144


def _snr_db(spectra: np.ndarray, projected: np.ndarray, mean: np.ndarray) -> float:
    """VCA signal-to-noise estimate (dB) from the data and its ``p``-dimensional projection."""
    n, channels = spectra.shape
    p = projected.shape[1]
    power_y = float((spectra**2).sum() / n)
    power_x = float((projected**2).sum() / n + mean @ mean)
    noise = power_y - power_x
    signal = power_x - p / channels * power_y
    if noise <= 0:
        return np.inf
    if signal <= 0:
        return -np.inf
    return float(10 * np.log10(signal / noise))


def vertex_component_analysis(
    spectra: np.ndarray, p: int, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """``(p, C)`` endmembers of ``(N, C)`` spectra and the indices of the pixels they come from.

    Follows the reference algorithm: a projective projection on the first ``p`` right singular
    vectors at high SNR (above ``15 + 10 log10(p)`` dB), else an orthogonal projection on the first
    ``p - 1`` principal components lifted by a constant coordinate. The endmembers are the selected
    pixels in the projected (noise-reduced) data, in the original units.
    """
    data = np.asarray(spectra, dtype=np.float64)
    n, channels = data.shape
    p = int(min(p, channels, n))
    mean = data.mean(axis=0)
    centred = data - mean
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    scores = centred @ vt[:p].T
    if _snr_db(data, scores, mean) < 15 + 10 * np.log10(p):
        basis = vt[: p - 1]
        x = centred @ basis.T
        denoised = x @ basis + mean
        lift = np.sqrt((x**2).sum(axis=1)).max() if p > 1 else 1.0
        y = np.column_stack([x, np.full(n, lift)])
    else:
        _, _, vt = np.linalg.svd(data, full_matrices=False)
        basis = vt[:p]
        x = data @ basis.T
        denoised = x @ basis
        u = x.mean(axis=0)
        scale = x @ u
        scale[np.abs(scale) < 1e-12] = 1e-12
        y = x / scale[:, None]
    rng = np.random.default_rng(seed)
    a = np.zeros((p, p))
    a[-1, 0] = 1.0
    picks = np.zeros(p, dtype=np.int64)
    for i in range(p):
        w = rng.random(p)
        f = w - a @ np.linalg.pinv(a) @ w
        f /= max(float(np.linalg.norm(f)), 1e-300)
        picks[i] = int(np.argmax(np.abs(y @ f)))
        a[:, i] = y[picks[i]]
    return denoised[picks].astype(np.float32), picks


@njit(cache=True)
def _face_solution(
    gram: np.ndarray, b: np.ndarray, idx: np.ndarray, ridge: float
) -> np.ndarray:
    """Least-squares solution on the face ``idx`` of the simplex (sum-to-one KKT system)."""
    m = idx.size
    kkt = np.zeros((m + 1, m + 1))
    rhs = np.zeros(m + 1)
    for r in range(m):
        for c in range(m):
            kkt[r, c] = gram[idx[r], idx[c]]
        kkt[r, r] += ridge
        kkt[r, m] = 1.0
        kkt[m, r] = 1.0
        rhs[r] = b[idx[r]]
    rhs[m] = 1.0
    return np.linalg.solve(kkt, rhs)[:m]


@njit(cache=True)
def _entering(
    gram: np.ndarray, b: np.ndarray, a: np.ndarray, passive: np.ndarray
) -> int:
    """Free coordinate with the largest reduced gradient (KKT violation), or -1 at the optimum."""
    grad = b - gram @ a
    lam = 0.0
    count = 0
    for j in range(a.size):
        if passive[j]:
            lam += grad[j]
            count += 1
    lam /= count
    best, best_j = 1e-10 * (1.0 + np.abs(b).max()), -1
    for j in range(a.size):
        if not passive[j] and grad[j] - lam > best:
            best, best_j = grad[j] - lam, j
    return best_j


@njit(cache=True)
def _simplex_qp(gram: np.ndarray, b: np.ndarray, out: np.ndarray) -> None:
    """Minimise ``a^T G a / 2 - b^T a`` on the simplex for every row of ``b`` (active set)."""
    p = gram.shape[0]
    ridge = 1e-12 * max(np.trace(gram) / p, 1e-300)
    for i in range(b.shape[0]):
        bi = b[i]
        start = np.argmin(0.5 * np.diag(gram) - bi)
        a = np.zeros(p)
        a[start] = 1.0
        passive = np.zeros(p, dtype=np.bool_)
        passive[start] = True
        for _ in range(4 * p + 8):
            entering = _entering(gram, bi, a, passive)
            if entering < 0:
                break
            passive[entering] = True
            for _inner in range(p + 1):
                idx = np.flatnonzero(passive)
                z = _face_solution(gram, bi, idx, ridge)
                if z.min() > 0:
                    a[:] = 0.0
                    a[idx] = z
                    break
                # Step towards z until the first passive coordinate reaches zero; drop it.
                alpha = 1.0
                for r in range(idx.size):
                    if z[r] <= 0:
                        alpha = min(alpha, a[idx[r]] / (a[idx[r]] - z[r]))
                for r in range(idx.size):
                    a[idx[r]] += alpha * (z[r] - a[idx[r]])
                    if a[idx[r]] <= 1e-12:
                        a[idx[r]] = 0.0
                        passive[idx[r]] = False
        out[i] = a


def fully_constrained_abundances(
    spectra: np.ndarray, endmembers: np.ndarray
) -> np.ndarray:
    """``(N, p)`` abundances of ``(N, C)`` finite spectra on ``(p, C)`` endmembers (rows sum to 1)."""
    e = np.asarray(endmembers, dtype=np.float64)
    gram = e @ e.T
    out = np.empty((len(spectra), len(e)), dtype=np.float64)
    for start in range(0, len(spectra), _BATCH):
        block = np.asarray(spectra[start : start + _BATCH], dtype=np.float64)
        _simplex_qp(
            gram, np.ascontiguousarray(block @ e.T), out[start : start + _BATCH]
        )
    np.clip(out, 0.0, 1.0, out=out)
    out /= np.maximum(out.sum(axis=1, keepdims=True), 1e-300)
    return out.astype(np.float32)
