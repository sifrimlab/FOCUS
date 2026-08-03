# MuData Compilation Stage

## 1. Overview

Compilation is the final pipeline step. It assembles the per-modality registered outputs into a single [MuData](https://mudata.readthedocs.io/) object (`.h5mu`) in which **row _i_ refers to the same reference spot in every modality**.

Compilation performs **no new spatial alignment or feature computation**. It is an assembly step that (a) validates that each modality's rows are aligned to the reference, (b) drops reference spots that are uncovered in any modality, (c) namespaces feature names to keep them collision-free, and (d) writes the combined object to disk.

!!! abstract "Scientific background"
    For how this stage fits the overall method, see
    [Scientific Overview](../scientific/overview.md#stage-4-compilation). Annotation labels promoted
    here are produced earlier by
    [Spatial Annotation Transfer](../scientific/annotation_transfer.md).

---

## 2. When it runs

Compilation runs only when **both** of these are true:

- `perform_registration` is `true`, **and**
- the **reference modality is spot-based** (`msi` or `st`).

If the reference is image-based, or registration is disabled, the stage never runs and no `.h5mu` is produced. The final outputs are then the per-modality merged files from earlier stages (`merged/alignment/`, `merged/registration/`, `merged/annotations/`). When enabled, compilation is the last stage (stage 4, or stage 5 when annotation transfer is also enabled).

Even when the stage runs, it **skips writing the MuData** (logging the reason to `focus.log`) if any of the following hold:

- the reference modality's merged file is missing;
- fewer than **two** modalities pass row-alignment validation (see [§4](#4-algorithm));
- every reference spot is dropped by the coverage filter.

!!! note "This is not the same as 'at least one modality registered'"
    The entry gate depends on `perform_registration` and the reference type, not on counting registered modalities. The requirement that ≥2 modalities survive is enforced *inside* the stage: a modality is excluded if its merged registration file is missing or its rows cannot be aligned to the reference.

---

## 3. Inputs

- **Anchor (the reference modality).** Compilation loads the reference's merged file once and treats it as the **anchor** that defines the shared observations. If annotation transfer ran, the anchor is the **annotated** reference file (`merged/annotations/...`); otherwise it is the reference's merged preprocessed file. The shared `obsm['spatial']`, `obs['sample_id']`, and (if present) `uns['spot_size']` are taken from the anchor.
- **Each target modality's merged registered file** (`merged/registration/...`), providing that modality's feature matrix `X`, `var`, and `obs`.

---

## 4. Algorithm

For a spot-based reference, compilation proceeds as follows:

1. **Load the anchor** and capture the shared arrays: `obsm['spatial']` (cast to `float32`), `obs['sample_id']`, and `uns['spot_size']` (if present). The anchor's observation count `n_anchor_obs` defines the expected row count for every modality.
2. **Add the anchor modality** to the modality set (its `obsm['spatial']` and `uns['spot_size']` are removed here; they become top-level, see [§5](#5-output-structure)).
3. **Validate and add each target modality** (iterated in config declaration order). A target is included only if **all** of these hold; otherwise it is **skipped with a warning**:
    - its merged registration file exists;
    - its `n_obs` equals `n_anchor_obs`;
    - it has an `obs['sample_id']` column;
    - its `sample_id` sequence matches the anchor's **element-wise**.

    The element-wise `sample_id` check is a safety guard: matching row counts alone is not enough, because the per-sample concatenation order during registration can diverge from the anchor's. Mis-paired rows would otherwise produce a MuData that cannot be read back.
4. **Drop uncovered spots.** A reference spot is removed from **all** modalities if its feature vector is **all-zero in any target modality**. This is why the final MuData can have fewer observations than the reference spot grid. All-zero rows come from two sources:
    - **spot interpolation**: no target spot fell within the reference spot's spatial footprint (the spot lies outside the target tissue section);
    - **feature extraction**: the image patch at the spot was at least 99% background pixels.

    If this leaves zero spots, compilation is skipped.
5. **Synchronize observation names** across modalities to the (possibly filtered) anchor's `obs_names`, so row _i_ is the same reference spot everywhere.
6. **Namespace feature names.** Every modality's `var_names` are rewritten to `"{modality}:{name}"` and de-duplicated. This prevents cross-modality collisions (e.g. MSI's bare integer feature names `"0"`, `"1"`, …) that would otherwise break MuData read-back.
7. **Build the MuData**, attach the shared top-level `obs['sample_id']`, `obsm['spatial']`, and `uns['spot_size']`, and **promote** `obs['spatial_annotation']` to the top level if the anchor carries it.
8. **Write** `merged/multimodal_dataset.h5mu` (via a compatibility writer that converts nullable-string columns to `object` dtype for broad reader support).

---

## 5. Output structure

Output path:

```text
{dataset_path}/merged/multimodal_dataset.h5mu
```

Layout:

- **`mod['<modality>']`**: one `AnnData` per modality, ordered anchor-first then by config declaration order. Each carries its `X`, `var`, and `obs`. Per-modality AnnData do **not** carry `obsm['spatial']` or `uns['spot_size']`; those are stored once at the top level.
- **`obs['sample_id']`**: shared sample identifiers.
- **`obs['spatial_annotation']`**: shared region labels, present only when annotation transfer ran.
- **`obsm['spatial']`**: shared spot coordinates in the reference frame, shape `(n_obs, 2)`, `float32`.
- **`uns['spot_size']`**: copied verbatim from the anchor (a length-2 `float32` array), present only if the anchor had it.

### Feature names are namespaced

Because feature names are rewritten to `"{modality}:{name}"`, query features with the prefixed name:

```python
mdata.mod["st"].var_names      # e.g. 'st:CD3E', 'st:GAPDH', ...
mdata.mod["msi"].var_names     # e.g. 'msi:0', 'msi:1', ...
```

---

## 6. Common surprises

- **Fewer spots than the reference grid.** The coverage filter ([step 4](#4-algorithm)) removes spots uncovered in any modality. Check `focus.log` for `Removing N/M anchor spots with no coverage`.
- **A modality is missing from `.mod`.** It failed row-alignment validation ([step 3](#4-algorithm)). Look for the corresponding warning in `focus.log` (missing file, observation-count mismatch, or sample-ID sequence mismatch).
- **No `.h5mu` was written.** See [§2](#2-when-it-runs) for the skip conditions; the reason is logged.
- **Feature lookups fail.** Use the namespaced names (`{modality}:{name}`), not the bare ones.

See [Troubleshooting](../troubleshooting.md) and the [FAQ](../faq.md) for more.

---

## 7. Configuration

Compilation has no dedicated configuration keys. Whether and how it runs is determined entirely by:

- `reference_modality` resolving to a spot-based modality (`msi`/`st`);
- `perform_registration` being `true`;
- which target modalities produced a valid merged registration output.

---

## 8. Notes

- Load the result with `mudata.read_h5mu(...)`. Each `mod` entry is a standard `AnnData`, compatible with scanpy and squidpy; the shared `mdata.obsm['spatial']` follows the squidpy convention.
