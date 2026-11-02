"""Schema HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from focus.gui.schema import _build_schema
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI

from flask import jsonify


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Schema ---

    @gui.app.route("/api/schema", methods=["GET"])
    def get_schema() -> ResponseReturnValue:
        """Return the GUI's configuration schema and option lists."""
        return jsonify(_build_schema())
