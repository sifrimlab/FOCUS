# Scientific Overview

## Problem framing

FOCUS addresses a spatial correspondence problem in multi-instrument tissue profiling: each modality is sampled on a different spatial grid and coordinate system, yet downstream analysis requires a single aligned observation index across modalities.

Given modalities \(\{M_k\}\), FOCUS constructs a shared set of observations indexed by a reference modality \(R\). For each non-reference modality \(T\), it estimates features at reference locations and stores them in modality-specific matrices with matched rows.

---

## Scientific design principles

1. **Modality-specific preprocessing**
   - Each modality uses dedicated preprocessing algorithms appropriate to its physics and data structure.
2. **User-guided spatial correspondence**
   - Alignment is interactive (`manual`) or declared as pre-existing (`pre_aligned`) when coordinate systems are already matched.
3. **Registration matched to modality type**
   - Image modalities use learned patch embeddings (`feature_extraction`).
   - Spot-based modalities use Gaussian-weighted interpolation (`spot_interpolation`) or equal-weight summation (`spot_aggregation`).
4. **Explicit checkpointing by stage**
   - Intermediate outputs are persisted per sample and merged, enabling restart and auditability.
5. **Open analysis formats**
   - AnnData (`.h5ad`) for intermediate data and MuData (`.h5mu`) for integrated outputs.

---

## Four-stage method

```text
Raw data -> [1] Preprocessing -> [2] Alignment -> [3] Registration -> [4] Compilation
```

### Stage 1: Preprocessing

Raw modality files are transformed into standardized intermediate representations:

- Image modalities (`microscopy_image`, `raman`) -> OME-TIFF
- Spot modalities (`msi`, `st`) -> AnnData

This stage also computes modality-specific metadata used later (for example `spot_size` for spot interpolation).

### Stage 2: Alignment

Alignment computes coordinates of reference spots expressed in each target modality frame.

For a non-reference modality \(T\), the aligned reference coordinates are stored as:

\[
\texttt{obsm['\{T\}_spatial']} \in \mathbb{R}^{N_R \times 2}
\]

where row \(i\) corresponds to reference observation \(i\).

### Stage 3: Registration

For each target modality \(T\), FOCUS computes a feature vector at each aligned reference location.

- `feature_extraction` (`microscopy_image`): deep patch embeddings (Prov-GigaPath).
- `spot_interpolation` (`msi`, `st`): Gaussian-weighted average over the target spots in each anchor footprint.
- `spot_aggregation` (`msi`, `st`): equal-weight sum over the target spots in each anchor footprint (no normalization); accumulates signal for subcellular-resolution data.
- `raman_pixel_interpolation` (`raman`): the same Gaussian interpolation over hyperspectral OME-TIFF pixels (temporary, pending a Raman-specific feature extractor).

Generic interpolation form:

\[
\hat{f}(r_i)=\frac{\sum_{j\in\mathcal{N}_i} w_{ij} f(t_j)}{\sum_{j\in\mathcal{N}_i} w_{ij}},
\qquad
w_{ij}=\exp\!\left(-\frac{\|r_i-t_j\|^2}{2\sigma^2}\right)
\]

with neighborhood \(\mathcal{N}_i\) and \(\sigma\) defined by the implementation in `registration_methods.md`.

### Stage 4: Compilation

Compilation to MuData is conditional:

- Reference modality is spot-based (`msi` or `st`), and
- At least one non-reference modality has `registration_type != "none"`.

These two conditions are equivalent to the "`perform_registration` is `true` **and** the reference
modality is spot-based" gate described in [Compilation](../pipeline/compilation.md), since
`perform_registration` is effectively "at least one modality is registered".

When compiled, modalities share a harmonized observation index and top-level spatial metadata.

---

## Supported modality types

- `microscopy_image`
- `msi`
- `raman`
- `st`

The scientific details for each preprocessing pipeline are documented in:

- `microscopy_methods.md`
- `msi_methods.md`
- `raman_methods.md`
- `registration_methods.md`
- `alignment_methods.md`
- `annotation_transfer.md`
