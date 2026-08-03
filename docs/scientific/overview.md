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

## Pipeline stages

```text
Raw data → [1] Preprocessing → [2] Alignment → [3] Registration → [4] Compilation
```

[Spatial annotation transfer](annotation_transfer.md) is an optional additional stage. It runs
directly after alignment and independently of registration and compilation. When it is enabled the
pipeline reports five stages and the numbering shifts: annotation transfer is stage 3, registration
stage 4, and compilation stage 5.

### Stage 1: Preprocessing

Raw modality files are transformed into standardized intermediate representations:

- Image modalities ([`microscopy_image`](microscopy_methods.md), [`raman`](raman_methods.md)) → OME-TIFF
- Spot modalities ([`msi`](msi_methods.md), [`st`](st_methods.md)) → AnnData

This stage also computes modality-specific metadata used later (for example `spot_size` for spot interpolation).

### Stage 2: Alignment

Alignment computes coordinates of reference spots expressed in each target modality frame
(see [Alignment Methods](alignment_methods.md)).

For a non-reference modality \(T\), the aligned reference coordinates are stored on the reference's
aligned AnnData under the pair-specific key `obsm['{target_name}_spatial']`, a matrix in
\(\mathbb{R}^{N_R \times 2}\) where row \(i\) corresponds to reference observation \(i\).

### Stage 3: Registration

For each target modality \(T\), FOCUS computes a feature vector at each aligned reference location.

- `feature_extraction` (`microscopy_image`): deep patch embeddings (Prov-GigaPath); valid only for H&E-stained brightfield RGB images, which is the model's pretraining domain.
- `spot_interpolation` (`msi`, `st`): Gaussian-weighted average over the target spots in each anchor footprint.
- `spot_aggregation` (`msi`, `st`): equal-weight sum over the target spots in each anchor footprint (no normalization); accumulates signal for subcellular-resolution data.
- `raman_pixel_interpolation` (`raman`): the same Gaussian interpolation over hyperspectral OME-TIFF pixels, each pixel acting as a spot at its pixel coordinate.

Generic interpolation form:

\[
\hat{f}(r_i)=\frac{\sum_{j\in\mathcal{N}_i} w_{ij} f(t_j)}{\sum_{j\in\mathcal{N}_i} w_{ij}},
\qquad
w_{ij}=\exp\!\left(-\frac{\|r_i-t_j\|^2}{2\sigma^2}\right)
\]

with neighborhood \(\mathcal{N}_i\) and \(\sigma\) defined by the implementation in
[Registration Methods](registration_methods.md).

### Stage 4: Compilation

Compilation to MuData is conditional on **both**:

- `perform_registration` is `true`, and
- the reference modality is spot-based (`msi` or `st`).

`perform_registration` is an independent configuration flag (default `true`), not a derived one: the
gate does **not** count how many modalities have `registration_type != "none"`. The requirement that
at least two modalities survive row-alignment validation is enforced *inside* the stage, not by this
gate (see [Compilation](../pipeline/compilation.md)).

When compiled, modalities share a harmonized observation index and top-level spatial metadata.

---

## Supported modality types

- `microscopy_image`
- `msi`
- `raman`
- `st`

The scientific details for each preprocessing pipeline are documented in:

- [MSI Preprocessing](msi_methods.md)
- [Raman Preprocessing](raman_methods.md)
- [Microscopy Preprocessing](microscopy_methods.md)
- [Spatial Transcriptomics Preprocessing](st_methods.md)

The stages that operate across modalities are documented in:

- [Alignment Methods](alignment_methods.md)
- [Registration Methods](registration_methods.md)
- [Annotation Transfer](annotation_transfer.md)
