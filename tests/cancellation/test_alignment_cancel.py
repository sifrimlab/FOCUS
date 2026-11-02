"""AlignmentCancel behavioral scenarios."""

import threading
import time

from focus.gui.alignment import DirectMappingAlignmentGUI
from focus.runtime.cancellation import PipelineCancelled


class TestAlignmentCancel:
    """Behavioral contracts for alignment cancel."""

    def test_cancel_releases_waiting_sample(self) -> None:
        """Verify cancel releases waiting sample."""
        done = threading.Event()
        gui = DirectMappingAlignmentGUI(1, done)
        raised = []

        def wait_for_user() -> None:
            """Signal that confirmation is pending, then wait on the fixture event until cancellation."""
            try:
                gui.align_sample("s1", 1, {}, {}, None, None)
            except PipelineCancelled:
                raised.append(True)

        thread = threading.Thread(target=wait_for_user, daemon=True)
        thread.start()
        time.sleep(0.1)
        gui.cancel()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert raised == [True]
        assert done.is_set()
