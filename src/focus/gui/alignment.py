"""Coordinate alignment browser confirmations, cancellation and server lifetime."""

from __future__ import annotations
from focus.gui.headers import cors_response
from focus.gui.routes.alignment import register_routes
from werkzeug.serving import BaseWSGIServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.alignment.types import (
        ModalityMetadata,
        AlignmentPayload,
        AlignmentResult,
    )

import os
import time
import threading

from flask import Flask
from werkzeug.serving import make_server

from focus.runtime.cancellation import PipelineCancelled
from focus.gui.json import _ValidJSONProvider


class DirectMappingAlignmentGUI:
    """
    A GUI interface that allows the user to directly map target coordinates to a reference image.

    Serves a Flask web app on localhost:8000 that displays reference and target modalities
    and collects user-provided alignment transformations sample by sample.

    Parameters
    ----------
        dataset_size: int
            The number of samples to be aligned.
        dataset_completed_event: threading.Event
            Event that signals when the alignment thread has finished processing all samples.
    """

    def __init__(
        self, dataset_size: int, dataset_completed_event: threading.Event
    ) -> None:
        """Create browser-confirmation state and routes; clear the supplied completion event.

        Parameters
        ----------
        dataset_size : int
            Number reported by the alignment status endpoint.
        dataset_completed_event : threading.Event
            Shared worker-completion signal, retained and cleared during initialization."""
        self._dataset_size = dataset_size  # Hold how many samples need to be processed

        # Synchronization events
        self._user_event = (
            threading.Event()
        )  # Event to signal when the user has saved the aligned coordinates
        self._dataset_completed_event = dataset_completed_event  # Event to signal when the dataset processing is completed

        # Sample metadata
        self._sample_id: str | None = None  # Name of the sample (sample ID)
        self._sample_index: int | None = None  # Index of the sample in the dataset

        # Modalities' metadata
        self._reference_metadata: ModalityMetadata | None = (
            None  # Metadata of the reference modality
        )
        self._target_metadata: ModalityMetadata | None = (
            None  # Metadata of the target modality
        )

        # Modalities' data (Image.Image for IMAGE modalities, list[dict] for SPOT modalities)
        self._reference_payload: AlignmentPayload = None
        self._target_payload: AlignmentPayload = None
        # Previously confirmed warp of the current sample (GET /initial_state)
        self._initial_state: dict[str, object] | None = None

        # Aligned coordinates
        self._aligned_target: AlignmentResult | None = (
            None  # Aligned target modality data
        )

        # Error state (set by alignment thread on crash)
        self._error_message: str | None = None

        # Set when the run is interrupted from the main GUI
        self._cancelled = False
        self._server_lock = threading.Lock()

        # Reset the events
        self._user_event.clear()
        self._dataset_completed_event.clear()

        self._basedir = os.path.join(os.path.dirname(__file__), "alignment")

        self.app = Flask(__name__)
        # Guarantee valid JSON responses (non-finite floats -> null) so the
        # browser client never receives an unparseable payload.
        self.app.json = _ValidJSONProvider(self.app)

        # Enable CORS
        self.app.after_request(cors_response)

        self._server: BaseWSGIServer | None = None
        register_routes(self)

    def set_error(self, message: str) -> None:
        """Signal that the alignment thread encountered an error. Triggers server shutdown."""
        self._error_message = message
        self._dataset_completed_event.set()

    def cancel(self) -> None:
        """Interrupt: release the waiting alignment thread and close the server without delay."""
        self._cancelled = True
        self._user_event.set()
        self._dataset_completed_event.set()
        with self._server_lock:
            server, self._server = self._server, None
        if server is not None:
            server.shutdown()

    def align_sample(
        self,
        sample_id: str,
        sample_index: int,
        reference_metadata: ModalityMetadata,
        target_metadata: ModalityMetadata,
        reference_payload: AlignmentPayload,
        target_payload: AlignmentPayload,
        initial_state: dict[str, object] | None = None,
    ) -> AlignmentResult | None:
        """
        Align the target coordinates to the reference image for a given sample.

        Parameters
        ----------
            sample_id: str
                The sample identifier.
            sample_index: int
                The index of the sample in the dataset.
            reference_metadata: dict
                The metadata of the reference modality.
            target_metadata: dict
                The metadata of the target modality.
            reference_payload: dict | Image.Image
                The payload of the reference modality.
            target_payload: dict | Image.Image
                The payload of the target modality.
            initial_state: dict | None
                A previously confirmed warp to start from (matrix, landmarks, exclusions),
                served at ``/initial_state``; None starts from the automatic fit.

        Returns
        -------
            aligned_result: dict
                The alignment result as a dictionary.
        """

        # Set the reference image and target coordinates
        self._sample_id = sample_id
        self._sample_index = sample_index
        self._reference_metadata = reference_metadata
        self._target_metadata = target_metadata
        self._reference_payload = reference_payload
        self._target_payload = target_payload
        self._initial_state = initial_state

        # Clear the user event
        self._user_event.clear()

        # Wait for the user to save the aligned coordinates
        self._user_event.wait()
        if self._cancelled:
            raise PipelineCancelled()

        self._sample_id = None
        self._sample_index = None
        self._reference_metadata = None
        self._target_metadata = None
        self._reference_payload = None
        self._target_payload = None
        self._initial_state = None

        return self._aligned_target

    def enable_gui(self) -> None:
        # Start a thread that will pend on the dataset completed event
        """Start the alignment server and wait for its completion event."""
        threading.Thread(target=self._disable_gui, daemon=True).start()

        server = make_server("localhost", 8000, self.app)
        with self._server_lock:
            if self._cancelled:
                server.server_close()
                return
            self._server = server
        server.serve_forever()
        server.server_close()

    def _disable_gui(self) -> None:
        # Wait for the dataset to be completed
        """Shut down the alignment server and notify its waiting coordinator."""
        self._dataset_completed_event.wait()

        # Give the browser time to read any error screen; otherwise just drain in-flight requests
        delay = 60 if self._error_message else 2
        time.sleep(delay)

        with self._server_lock:
            server, self._server = self._server, None
        if server is not None:
            server.shutdown()
