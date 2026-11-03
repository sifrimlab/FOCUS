"""Static HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI

from flask import send_from_directory


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Static file serving ---

    @gui.app.route("/")
    def index() -> ResponseReturnValue:
        """Serve the compiled GUI entry document."""
        return send_from_directory(gui._basedir, "index.html")

    @gui.app.route("/<path:filename>")
    def serve_static(filename: str) -> ResponseReturnValue:
        """Serve a bundled GUI asset by its requested path."""
        return send_from_directory(gui._basedir, filename)
