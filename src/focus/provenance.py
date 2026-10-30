"""Content fingerprints for preprocessing and its downstream artifacts."""
import hashlib
import json
import os
import xml.etree.ElementTree as ET

import h5py
import numpy as np

# Namespace of the OME MapAnnotation that carries FOCUS provenance inside an OME-TIFF.
FOCUS_OME_NAMESPACE = "focus"
PREPROCESSING_FINGERPRINT_KEY = "focus_preprocessing_fingerprint"
PIXEL_DIGEST_KEY = "focus_pixel_digest"
# Level-0 pixel offset "x,y" of a preprocessed image's origin in its source image (tissue crop).
CROP_OFFSET_KEY = "focus_crop_offset"
# JSON stat signature of the file a regenerated, pixel-identical image replaced.
SUPERSEDED_SIGNATURE_KEY = "focus_superseded_signature"

_TIFF_SUFFIXES = (".tif", ".tiff", ".qptiff")
_SIGNATURE_CHUNK = 8 * 1024 * 1024


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_SIGNATURE_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_digest(array):
    """sha256 of an array's dtype, shape and bytes."""
    array = np.ascontiguousarray(array)
    digest = hashlib.sha256(json.dumps([array.dtype.str, list(array.shape)]).encode())
    digest.update(array.data)
    return digest.hexdigest()


def planes_digest(planes):
    """sha256 over a sequence of image planes, in order (dtype, shape and bytes of each)."""
    digest = hashlib.sha256()
    for plane in planes:
        plane = np.ascontiguousarray(plane)
        digest.update(json.dumps([plane.dtype.str, list(plane.shape)]).encode())
        digest.update(plane.data)
    return digest.hexdigest()


def stat_signature(path):
    """Path, size and modification time: the identity used for files that carry no fingerprint."""
    stat = os.stat(path)
    return [os.path.abspath(path), stat.st_size, stat.st_mtime_ns]


def file_signature(path):
    """Size and sha256 of the first and last 8 MiB: a cheap content identity for large raw files."""
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        if size <= 2 * _SIGNATURE_CHUNK:
            digest.update(stream.read())
        else:
            digest.update(stream.read(_SIGNATURE_CHUNK))
            stream.seek(size - _SIGNATURE_CHUNK)
            digest.update(stream.read(_SIGNATURE_CHUNK))
    return [size, digest.hexdigest()]


def is_tiff(path):
    return str(path).lower().endswith(_TIFF_SUFFIXES)


def read_ome_annotations(path):
    """FOCUS key/value pairs stored in an OME-TIFF's OME-XML MapAnnotation, or None."""
    import tifffile

    try:
        with tifffile.TiffFile(path) as tif:
            xml = tif.pages[0].description
    except Exception:
        return None
    if not xml or "<OME" not in xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    values = {}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "MapAnnotation" or element.get("Namespace") != FOCUS_OME_NAMESPACE:
            continue
        for entry in element.iter():
            if entry.tag.rsplit("}", 1)[-1] == "M" and entry.get("K") is not None:
                values[entry.get("K")] = entry.text or ""
    return values or None


def cached_fingerprint(path, key=PREPROCESSING_FINGERPRINT_KEY):
    """The fingerprint stored under ``key``: in the OME MapAnnotation of a TIFF, in ``uns`` of an h5ad."""
    if is_tiff(path):
        return (read_ome_annotations(path) or {}).get(key)
    try:
        with h5py.File(path, "r") as f:
            value = f[f"uns/{key}"][()]
            return value.decode() if isinstance(value, bytes) else str(value)
    except (OSError, KeyError):
        return None


def content_signature(path):
    """Identity of an upstream file for downstream caches.

    A FOCUS OME-TIFF is identified by its level-0 pixel digest, an h5ad by its preprocessing
    fingerprint; any other file falls back to its stat signature.
    """
    if is_tiff(path):
        digest = (read_ome_annotations(path) or {}).get(PIXEL_DIGEST_KEY)
        return f"pixels:{digest}" if digest else stat_signature(path)
    key = cached_fingerprint(path)
    return key if key is not None else stat_signature(path)
