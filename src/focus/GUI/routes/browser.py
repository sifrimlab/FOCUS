"""Browser HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
import os

from flask import request, jsonify


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Filesystem browser ---

    @gui.app.route("/api/browse", methods=["GET"])
    def browse_filesystem() -> ResponseReturnValue:
        """List child directories in case-insensitive order for the browser endpoint."""
        path = request.args.get("path", "").strip()
        if not path:
            path = os.path.expanduser("~")
        path = os.path.realpath(path)
        if not os.path.isdir(path):
            return jsonify({"error": f"Not a directory: {path}"}), 400
        parent: str | None = os.path.dirname(path)
        parent = None if parent == path else parent
        entries = []
        try:
            for name in sorted(os.listdir(path), key=str.lower):
                full = os.path.join(path, name)
                try:
                    if os.path.isdir(full):
                        entries.append({"name": name})
                except OSError:
                    pass
        except PermissionError as e:
            return jsonify({"error": str(e)}), 403
        return jsonify({"path": path, "parent": parent, "entries": entries})

    @gui.app.route("/api/browse_files", methods=["GET"])
    def browse_filesystem_files() -> ResponseReturnValue:
        """Like /api/browse but returns both directories and files."""
        path = request.args.get("path", "").strip()
        if not path:
            path = os.path.expanduser("~")
        path = os.path.realpath(path)
        if not os.path.isdir(path):
            return jsonify({"error": f"Not a directory: {path}"}), 400
        parent: str | None = os.path.dirname(path)
        parent = None if parent == path else parent
        entries = []
        try:
            for name in sorted(os.listdir(path), key=str.lower):
                full = os.path.join(path, name)
                try:
                    is_dir = os.path.isdir(full)
                    entries.append({"name": name, "is_dir": is_dir})
                except OSError:
                    pass
        except PermissionError as e:
            return jsonify({"error": str(e)}), 403
        return jsonify({"path": path, "parent": parent, "entries": entries})
