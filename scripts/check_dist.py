"""Check FOCUS release archives before publishing.

The sdist must carry the complete GUI source (gui_src/, the GPL Corresponding
Source of the bundled GUIs) and no node_modules. A local `npm install` in
gui_src/ breaks this silently: setuptools follows npm's workspace symlinks
(gui_src/node_modules/<workspace> -> gui_src/<workspace>), visits each real
directory once, and the MANIFEST.in prune of node_modules then drops the
sources. Build releases from a clean checkout; this script fails otherwise.

The wheel must carry both compiled GUIs (focus/gui/main, focus/gui/alignment).

Usage (from a git checkout): python scripts/check_dist.py dist/
"""

import fnmatch
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

# Mirrors the prune / global-exclude lines of MANIFEST.in.
NOT_SHIPPED = (
    "*.DS_Store",
    "*.tsbuildinfo",
    "gui_src/*/.vscode/*",
    "gui_src/*/.idea/*",
    "gui_src/*/dist/*",
    "gui_src/*/.vite/*",
)
WHEEL_REQUIRED = (
    "focus/gui/main/index.html",
    "focus/gui/alignment/index.html",
    "focus/py.typed",
)


def check_sdist(path: Path) -> list[str]:
    """Return archive errors for missing GUI sources and included build intermediates."""
    with tarfile.open(path) as tar:
        names = {n.split("/", 1)[1] for n in tar.getnames() if "/" in n}
    tracked = subprocess.run(
        ["git", "ls-files", "gui_src"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    expected = {
        f for f in tracked if not any(fnmatch.fnmatch(f, p) for p in NOT_SHIPPED)
    }
    errors = [f"sdist is missing {f}" for f in sorted(expected - names)]
    errors += [f"sdist is missing {f}" for f in owned_sources() if f not in names]
    errors += [f"sdist ships {n}" for n in sorted(names) if "/node_modules/" in f"/{n}"]
    return errors


def owned_sources() -> list[str]:
    """List owned Python, stubs and recorded test contracts required in a source archive."""
    extensions = {".py", ".pyi", ".json", ".npy", ".npz"}
    return sorted(
        path.as_posix()
        for root in (
            "src/focus",
            "scripts",
            "tests",
            "typing",
            "docs/development",
        )
        for path in Path(root).rglob("*")
        if path.is_file() and path.suffix in extensions
    )


def check_wheel(path: Path) -> list[str]:
    """Check typing metadata and every compiled GUI asset against the source contents."""
    with zipfile.ZipFile(path) as whl:
        names = set(whl.namelist())
        errors = [f"wheel is missing {f}" for f in WHEEL_REQUIRED if f not in names]
        for directory in ("main", "alignment"):
            for asset in sorted((Path("src/focus/gui") / directory).rglob("*")):
                if not asset.is_file() or asset.name == ".DS_Store":
                    continue
                name = asset.relative_to("src").as_posix()
                if name not in names:
                    errors.append(f"wheel is missing {name}")
                elif whl.read(name) != asset.read_bytes():
                    errors.append(f"wheel asset differs from source: {name}")
        if any(name.startswith("focus/GUI/") for name in names):
            errors.append("wheel contains the obsolete uppercase GUI package")
    return errors


def main(dist: Path) -> int:
    """Parse command-line arguments and execute the requested workflow."""
    sdists, wheels = sorted(dist.glob("*.tar.gz")), sorted(dist.glob("*.whl"))
    if not sdists or not wheels:
        print(f"expected an sdist and a wheel in {dist}", file=sys.stderr)
        return 1
    errors = [e for s in sdists for e in check_sdist(s)] + [
        e for w in wheels for e in check_wheel(w)
    ]
    for e in errors[:50]:
        print(e, file=sys.stderr)
    if errors:
        print(
            f"{len(errors)} problem(s); build the release from a clean checkout.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {', '.join(p.name for p in sdists + wheels)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "dist")))
