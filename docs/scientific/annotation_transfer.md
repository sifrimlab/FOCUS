# Spatial Annotation Transfer

## 1. Overview

Spatial annotations are manually drawn regions of interest (ROIs) — typically polygons delineating histological structures such as tumour, stroma, necrosis, or specific cell-rich areas — exported from annotation software such as QuPath. FOCUS can transfer these region labels to the molecular measurements at each reference spot, enabling region-of-interest analysis: for example, comparing lipid profiles between tumour and stromal tissue.

Annotation transfer is an optional pipeline stage (Stage 2.5) that runs after alignment and before registration. It assigns a string label to `.obs['spatial_annotation']` of the reference modality AnnData; this label is propagated to `mdata.obs['spatial_annotation']` at MuData compilation.

---

## 2. Input Format

FOCUS reads annotations in **GeoJSON FeatureCollection** format, the default export format of QuPath.

### 2.1 File naming and placement

One GeoJSON file per sample must be placed in the annotation modality's sample directory. The pipeline scans for any `.geojson` file in `{dataset_path}/{sample_id}/{annotation_modality_name}/` and uses the first match.

### 2.2 GeoJSON structure

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "annotation_id",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y1], ...]]
      },
      "properties": {
        "classification": { "name": "Tumour" },
        "name": "Region 1"
      }
    }
  ]
}
```

Both `Polygon` and `MultiPolygon` geometry types are supported. Interior holes (inner rings) are **ignored** — only exterior rings are used to construct the Shapely geometry.

### 2.3 Label resolution priority

The annotation label assigned to each polygon is resolved in the following priority order:

1. `feature.properties.classification.name` — QuPath's primary classification field
2. `feature.properties.name` — fallback name property
3. `feature.id` — feature identifier string

### 2.4 Coordinate units

Annotation coordinates must be in the same units and coordinate system as the annotation modality's image (typically pixel coordinates of the microscopy image). The pipeline passes reference spot coordinates that have been aligned into the annotation modality's space via `obsm['{annotation_modality}_spatial']`.

---

## 3. Algorithm

Given $N$ reference spot positions $\{\mathbf{p}_i\}_{i=1}^N$ (expressed in the annotation modality's coordinate frame) and $M$ annotation polygons $\{(P_j, \ell_j)\}_{j=1}^M$, the algorithm assigns each spot a label $\ell_i \in \{\ell_1, \ldots, \ell_M, \texttt{None}\}$.

### 3.1 Polygon preparation

For each polygon $P_j$, Shapely's `prepare()` function is called:

```python
shapely.prepare(polygons_arr)
```

This pre-computes a GEOS prepared geometry object which enables $O(1)$ amortised point-containment queries via the `GEOSPreparedContains` fast path, avoiding repeated index construction.

### 3.2 Descending area ordering

Polygons are sorted in descending order of area:

$$\text{area}(P_1) \geq \text{area}(P_2) \geq \cdots \geq \text{area}(P_M)$$

The iteration proceeds from largest to smallest polygon. Because label assignment **overwrites** (last write wins), the smallest polygon that contains a given spot is ultimately assigned to it. This ensures that when polygons overlap — for instance, a fine-grained `Tumour_core` region nested inside a larger `Tumour` region — the most specific (smallest-area) annotation wins.

### 3.3 Vectorised point-in-polygon queries

For each polygon $P_j$ (processed in descending area order), all $N$ reference spot positions are tested simultaneously using Shapely's vectorised `contains`:

$$h_{ij} = \text{GEOSPreparedContains}(P_j,\, \mathbf{p}_i), \qquad h_{ij} \in \{0, 1\}$$

This uses the prepared geometry fast path and operates on a NumPy array of Shapely point objects created via `shapely.points(coords[:, 0], coords[:, 1])`.

### 3.4 Label assignment

After iterating over all polygons in descending area order:

$$\ell_i = \ell_j \quad \text{for all } j \text{ and all } i \text{ where } h_{ij} = 1$$

The last write (smallest polygon) wins for spots inside multiple polygons. Spots not contained in any polygon retain label `None`.

### 3.5 Complexity

- **Polygon preparation:** $O(V_j)$ per polygon where $V_j$ is the vertex count; amortised over all queries.
- **Point-in-polygon query:** $O(N)$ per polygon with prepared geometries.
- **Total:** $O(M \cdot N)$ point tests, but each individual test is highly optimised by GEOS.

For typical datasets (hundreds of spots, tens of polygons), this runs in under one second. For larger datasets (tens of thousands of spots), the prepared-geometry path keeps runtimes practical.

---

## 4. Output

After annotation transfer, the reference modality AnnData contains:

| Field | Type | Description |
|-------|------|-------------|
| `.obs['spatial_annotation']` | `pd.Categorical` | Label string per spot, or `None` for unannotated spots |

At MuData compilation, this column is promoted to:

| Field | Location | Description |
|-------|----------|-------------|
| `mdata.obs['spatial_annotation']` | MuData top-level | Shared annotation label for all modalities |

---

## 5. Preparing QuPath Annotations

### 5.1 Drawing annotations

1. Open the whole-slide image in QuPath.
2. Select **Annotations** → **Create annotation** or use the polygon/brush tool.
3. Assign a classification to each annotation: right-click the annotation → **Assign class** → select or create a class name.
4. Verify that each annotation has a non-empty classification name — this becomes the label in `spatial_annotation`.

### 5.2 Exporting GeoJSON

1. With the annotations layer active, go to **File** → **Export regions** → **Export as GeoJSON**.
2. In the export dialog:
   - Select **Include classification** (or "Include default names") to ensure `classification.name` is populated.
   - Export the full annotation layer, not individual annotations.
3. Name the exported file `{sample_id}.geojson` (any `.geojson` filename is accepted; FOCUS uses the first file found).
4. Place the file in `{dataset_path}/{sample_id}/{annotation_modality_name}/`.

!!! tip "One file per sample"
    Export a separate GeoJSON for each tissue section (sample). Pooling multiple sections in one file is not supported — each GeoJSON is processed independently against the reference spots of its corresponding sample.

!!! warning "Interior holes"
    QuPath can draw annotations with holes (donut-shaped regions). FOCUS currently ignores interior rings and uses only the outer boundary. Spots inside holes will be labelled as if they are inside the outer polygon.
