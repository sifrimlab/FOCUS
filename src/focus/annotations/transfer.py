import logging
import time

import numpy as np
from shapely import STRtree, points as shapely_points, prepare
from shapely.geometry import Polygon, MultiPolygon

from focus.annotations.annotations import load_geojson

logger = logging.getLogger(__name__)


def transfer_annotations(
	coords: np.ndarray,
	sample_ids: np.ndarray,
	annotation_paths: dict[str, str],
) -> np.ndarray:
	"""
	Assign a spatial annotation label to each reference spot.

	Parameters
	----------
	coords : np.ndarray, shape (N, 2)
		Spot coordinates (x, y) expressed in the annotation modality's space.
	sample_ids : np.ndarray, shape (N,)
		Sample identifier for each spot.
	annotation_paths : dict[str, str]
		Mapping of sample_id → path to the GeoJSON annotation file for that sample.

	Returns
	-------
	np.ndarray, shape (N,), dtype object
		Label string for each spot, or None if the spot falls outside all polygons.

	Overlap resolution
	------------------
	When a spot falls inside multiple overlapping polygons, the label of the polygon
	with the **smallest area** is assigned (most fine-grained annotation wins).
	"""
	N = len(coords)
	labels: np.ndarray = np.full(N, None, dtype=object)

	for sid in np.unique(sample_ids):
		if sid not in annotation_paths:
			continue

		mask = sample_ids == sid
		sample_coords = coords[mask]
		sample_indices = np.where(mask)[0]

		t0 = time.perf_counter()
		features = load_geojson(annotation_paths[sid])
		logger.info(f"  GeoJSON loaded: {len(features)} polygons in {time.perf_counter() - t0:.2f}s")
		if not features:
			continue

		polygons: list[Polygon | MultiPolygon] = [geom for _, geom in features]
		label_names = [lbl for lbl, _ in features]
		areas = np.array([geom.area for geom in polygons], dtype=float)
		total_vertices = sum(
			len(g.exterior.coords) if isinstance(g, Polygon)
			else sum(len(p.exterior.coords) for p in g.geoms)
			for g in polygons
		)
		logger.info(f"  Polygon stats: {len(polygons)} polygons, {total_vertices} total vertices, areas min={areas.min():.0f} max={areas.max():.0f}")

		t0 = time.perf_counter()
		polygons_arr = np.asarray(polygons, dtype=object)
		prepare(polygons_arr)
		logger.info(f"  Prepared {len(polygons_arr)} polygons in {time.perf_counter() - t0:.2f}s")

		t0 = time.perf_counter()
		tree = STRtree(polygons_arr)
		logger.info(f"  STRtree built in {time.perf_counter() - t0:.2f}s")

		t0 = time.perf_counter()
		pts = shapely_points(sample_coords[:, 0], sample_coords[:, 1])
		logger.info(f"  {len(pts)} points created in {time.perf_counter() - t0:.2f}s — running spatial query...")

		t0 = time.perf_counter()
		# within dispatches to GEOSPreparedContains on the prepared polygons, which is
		# the fastest path. covered_by would also catch boundary points but is slower.
		matches = tree.query(pts, predicate="within")
		n_matches = matches.shape[1] if matches.ndim == 2 else matches.size // 2
		logger.info(f"  Spatial query done: {n_matches} (point, polygon) pairs in {time.perf_counter() - t0:.2f}s")

		if matches.size == 0:
			continue

		pt_idx = matches[0]
		poly_idx = matches[1]

		# For each point, pick the matching polygon with the smallest area.
		# Sort by area so that np.unique(return_index=True) gives the first
		# (smallest-area) occurrence for each point.
		order = np.argsort(areas[poly_idx])
		pt_sorted = pt_idx[order]
		poly_sorted = poly_idx[order]

		_, first_occ = np.unique(pt_sorted, return_index=True)
		labels[sample_indices[pt_sorted[first_occ]]] = np.array(label_names)[poly_sorted[first_occ]]
		logger.info(f"  {len(first_occ)}/{len(pts)} spots annotated")

	return labels
