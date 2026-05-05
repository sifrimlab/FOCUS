import json

from shapely.geometry import Polygon, MultiPolygon


def load_geojson(path: str) -> list[tuple[str, Polygon | MultiPolygon]]:
	"""
	Load a QuPath-style GeoJSON FeatureCollection.

	Returns a list of (label, geometry) pairs. Both Polygon and MultiPolygon
	geometries are supported. Interior holes are ignored (only exterior rings
	are used). Coordinates are assumed to be [x, y] pixel values.

	Label priority:
	  1. feature.properties.classification.name
	  2. feature.properties.name
	  3. feature.id
	"""
	with open(path) as f:
		data = json.load(f)

	result: list[tuple[str, Polygon | MultiPolygon]] = []
	for feat in data.get("features", []):
		geom = feat.get("geometry", {})
		geom_type = geom.get("type")
		if geom_type not in ("Polygon", "MultiPolygon"):
			continue

		props = feat.get("properties") or {}
		classification = props.get("classification") or {}
		label = (
			classification.get("name")
			or props.get("name")
			or str(feat.get("id", "unknown"))
		)

		if geom_type == "Polygon":
			exterior = geom["coordinates"][0]
			geometry: Polygon | MultiPolygon = Polygon(exterior)
		else:  # MultiPolygon
			# Each element is a polygon's rings; take only the exterior ring of each
			geometry = MultiPolygon([Polygon(rings[0]) for rings in geom["coordinates"]])

		result.append((str(label), geometry))

	return result
