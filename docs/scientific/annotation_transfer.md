# Spatial Annotation Transfer

## 1. Objective

Annotation transfer assigns region labels from GeoJSON polygons to reference observations and stores labels in `.obs['spatial_annotation']`.

Input coordinates must already be expressed in the annotation modality frame.

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

For each sample:

1. Load polygon geometries and labels.
2. Compute polygon areas.
3. Prepare geometries with `shapely.prepare`.
4. Create vectorized point objects from coordinates.
5. Iterate polygons in **descending area** order.
6. For each polygon, compute vectorized containment mask via `shapely.contains` and assign labels.

Because assignment overwrites previous labels, descending-area traversal yields **smallest containing polygon wins** in overlaps.

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

- If annotation modality equals reference: use `obsm['spatial']` from reference sample.
- Otherwise: use aligned coordinates `obsm[f'{annotation_modality}_spatial']` from per-sample aligned reference file.

This guarantees point-in-polygon queries are performed in annotation-modality coordinates.
