"""Register route groups in the original endpoint order."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
from focus.gui.routes import static
from focus.gui.routes import schema
from focus.gui.routes import browser
from focus.gui.routes import samples
from focus.gui.routes import configuration
from focus.gui.routes import runs
from focus.gui.routes import status
from focus.gui.routes import maintenance


def register_routes(gui: MainGUI) -> None:
    """Attach the original routes to the supplied server state."""
    static.register_routes(gui)
    schema.register_routes(gui)
    browser.register_routes(gui)
    samples.register_routes(gui)
    configuration.register_routes(gui)
    runs.register_routes(gui)
    status.register_routes(gui)
    maintenance.register_routes(gui)
