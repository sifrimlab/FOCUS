"""Content fingerprints for preprocessing and its downstream artifacts."""
import hashlib
import json
import h5py

def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_fingerprint(path):
    try:
        with h5py.File(path, "r") as f:
            value = f["uns/focus_preprocessing_fingerprint"][()]
            return value.decode() if isinstance(value, bytes) else str(value)
    except (OSError, KeyError):
        return None


