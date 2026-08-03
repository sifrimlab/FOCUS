# Spatial Annotation Transfer

## 1. Objective

Annotation transfer assigns region labels from GeoJSON polygons to reference observations and stores labels in `.obs['spatial_annotation']`.

It is a **distinct pipeline stage that runs after alignment** (`_run_annotation_transfer` in `orchestrator.py`), not a part of compilation. The label is written onto the reference modality file at this stage and runs even when registration and compilation are inactive. The [compilation stage](../pipeline/compilation.md), when it runs, only **promotes** the already-written label to the top level of the MuData object (`mdata.obs['spatial_annotation']`).

Input coordinates must already be expressed in the annotation modality frame (see [§6 Requirements](#6-requirements)).

---

## 2. Input format and label resolution

GeoJSON `FeatureCollection` with `Polygon`/`MultiPolygon` geometries.

Label priority (`load_geojson`):

1. `properties.classification.name`
2. `properties.name`
3. `feature.id`

Interior holes are ignored in current implementation (only exterior rings are used).

Config-level validation requires exactly one `.geojson` file per sample in the annotation modality directory.

---

## 3. Algorithm

The work is done per sample (`transfer_annotations` in `annotations/transfer.py`). All labels are initialized to `None`. For each unique sample id:

1. Skip the sample if it has no annotation file, or if the GeoJSON contains no usable polygon features (its spots stay `None`).
2. Load polygon geometries and labels via `load_geojson`.
3. Compute each polygon's area.
4. Prepare geometries with `shapely.prepare` (builds the spatial index used by the fast containment path).
5. Create vectorized point objects from the spot coordinates.
6. Iterate polygons in **descending area** order; for each polygon, compute a vectorized containment mask via `shapely.contains` and assign that polygon's label to every contained spot.

Because assignment overwrites previous labels, descending-area traversal yields **smallest containing polygon wins** in overlaps.

The resulting labels are stored on the reference modality as a pandas `Categorical` (`obs['spatial_annotation']`). A spot that falls outside **every** polygon keeps the value `None`, which becomes a missing (`NaN`) category in the output.

Formally, for point set \(\{p_i\}\) and polygons \(\{P_j\}\):

\[
\ell_i \leftarrow \ell_j \quad \text{if } p_i \in P_j
\]

with \(P_j\) iterated from largest to smallest area.

---

## 4. Output placement

Per-sample annotated reference file:

```text
{dataset_path}/{sample_id}/annotations/{reference_name}_{sample_id}_annotated.h5ad
```

Merged annotated reference file:

```text
{dataset_path}/merged/annotations/{reference_name}_merged_annotated.h5ad
```

MuData compilation promotes labels to top-level:

```python
mdata.obs['spatial_annotation']
```

---

## 5. Coordinate source in pipeline

The transfer always runs **per sample**, never against the merged aligned file (which may be absent or incomplete if a prior run was interrupted). For each sample, the spot `sample_id`s are read from the reference modality file, and the coordinates used for the point-in-polygon test are taken as follows:

- **If the annotation modality equals the reference modality:** use `obsm['spatial']` from the reference sample directly.
- **Otherwise:** use the aligned coordinates `obsm['{annotation_modality}_spatial']` from that sample's aligned file (`aligned_files[annotation_modality][sample_id]`). These are the reference spots expressed in the annotation modality's frame, produced by the [alignment stage](alignment_methods.md).

Either way, the point-in-polygon queries are performed in annotation-modality coordinates, matching the GeoJSON pixel space.

---

## 6. Requirements

- **Config block.** `spatial_annotations` is an optional object. When present it must declare:
    - `modality_name`: must be the `name` of a modality declared in the config.
    - `file_type`: must be `"geojson"` (the only supported type).
- **Exactly one `.geojson` per sample.** Each sample directory must contain exactly one `.geojson` file under `{dataset_path}/{sample_id}/{modality_name}/`. Zero files raises `FileNotFoundError`; more than one raises `ValueError`.
- **Alignment requirement.** If the annotation modality is **not** the reference modality, `perform_alignment` must be `true`. Otherwise validation fails with:

    > `'spatial_annotations.modality_name' is '<name>' (a non-reference modality). Transferring annotations from a non-reference modality requires 'perform_alignment' to be true.`

    Alignment is what produces the `obsm['{annotation_modality}_spatial']` coordinates the transfer reads.
- **Shared coordinate frame.** The GeoJSON polygon coordinates and the spot coordinates must live in the same pixel space. When the annotation modality is the reference, the reference's `obsm['spatial']` must already be in the GeoJSON's pixel frame (no alignment step corrects it).

---

## 7. Tuning and control

The algorithm itself **exposes no tunable parameters.** There is no distance threshold, polygon buffering, or coordinate snapping, and the overlap rule (smallest-area-wins) is fixed. Containment is exact, so a spot outside a polygon boundary is left unlabeled (`None`).

The outcome is therefore controlled entirely by the inputs you provide:

- **Polygon granularity and validity.** Finer polygons yield finer labels. Invalid/self-intersecting geometry can produce unexpected containment results, since only the exterior ring of each polygon is used (interior holes are ignored).
- **Label resolution.** The label assigned to a region comes from `classification.name` → `name` → `feature.id`, in that order. To get meaningful labels from QuPath, export with classifications so that `classification.name` is populated.
- **Region nesting.** When regions overlap or nest, the **smallest-area** polygon wins. Arrange your annotations so the most specific region is also the smallest if you want it to take precedence.
- **Alignment accuracy.** When the annotation modality differs from the reference, the quality of the spot-to-polygon assignment depends directly on how well the alignment stage placed the reference spots into the annotation modality's frame.
