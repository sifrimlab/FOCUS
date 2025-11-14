import os, time, threading
import numpy as np
from PIL import Image
from typing import Callable


from io import BytesIO

from flask import Flask, request, jsonify, send_file
from werkzeug.serving import make_server
from threading import Thread

class DirectMappingAlignmentGUI:
    '''
    A GUI interface that allows the user to directly map target coordinates to a reference image

    Parameters
    ----------
        samples: list[str]
            A list of sample identifiers.
    '''

    def __init__(self, samples: list[str], dataset_completed_event: threading.Event):

        self._samples = samples

        self._reference_image: Image.Image | None = None
        self._raster_size: np.ndarray | None = None
        self._target_coordinates: np.ndarray | None = None
        self._aligned_coordinates: np.ndarray | None = None

        self._user_event = threading.Event()
        self._dataset_completed_event = dataset_completed_event

        # Reset the events
        self._user_event.clear()
        self._dataset_completed_event.clear()

        self.app = Flask(__name__)
        self._server = None
        self._register_routes()

    def _register_routes(self):
        @self.app.route('/')
        def index():
            # Get the absolute path of the HTML file
            html_path = os.path.join(os.path.dirname(__file__), "direct_mapping.html")

            # Read the HTML file
            with open(html_path, 'r') as file:
                html_content = file.read()
            
            return html_content

        @self.app.route('/get_reference_image', methods=['GET'])
        def get_reference_image():
            # If self._reference_image is None, return error 404
            if self._reference_image is None:
                return jsonify({"status": "error", "message": "Reference image not set."}), 404
            
            # Convert the PIL image to a BytesIO object
            img_io = BytesIO()
            self._reference_image.save(img_io, 'PNG')
            img_io.seek(0)
            return send_file(img_io, mimetype='image/png')
        
        @self.app.route('/get_target_coordinates', methods=['GET'])
        def get_target_coordinates():
            if self._target_coordinates is None:
                return jsonify({"status": "error", "message": "Target coordinates not set."}), 404
            
            # Conver the target coordinates to a JSON serializable format. self._target_coordinates is a numpy array of shape (N, 2)
            coordinates_list = [{'x': float(coord[0]), 'y': float(coord[1])} for coord in self._target_coordinates]
            return jsonify({"status": "success", "coordinates": coordinates_list})
        
        @self.app.route('/save_aligned_coordinates', methods=['POST'])
        def save_aligned_coordinates():
            data = request.get_json()

            aligned_coordinates = data.get('aligned_coordinates')

            # Convert the aligned coordinates to a numpy array
            self._aligned_coordinates = np.array([[float(coord['x_aligned']), float(coord['y_aligned'])] for coord in aligned_coordinates], dtype=np.float32)
            self._user_event.set()

            return jsonify({"status": "success", "message": "Aligned coordinates saved."})
        
        @self.app.route('//get_target_raster_size', methods=['GET'])
        def get_raster_size():
            if self._raster_size is None:
                return jsonify({"status": "error", "message": "Raster size not set."}), 404
            
            raster_size_list = [int(size) for size in self._raster_size]
            return jsonify({"status": "success", "raster_size": raster_size_list})
        
        @self.app.route('/is_dataset_completed', methods=['GET'])
        def is_dataset_completed():
            return jsonify({"status": "success", "completed": self._dataset_completed_event.is_set()})
        
    def align_sample(self, sample_id: str, reference_image: np.ndarray, target_coordinates: np.ndarray, raster_size: np.ndarray) -> np.ndarray:
        '''
        Align the target coordinates to the reference image for a given sample.

        Parameters
        ----------
            sample_id: str
                The sample identifier.
            reference_image: np.ndarray
                The reference image as a numpy array.
            target_coordinates: np.ndarray
                The target coordinates as a numpy array of shape (N, 2).
            raster_size: np.ndarray
                The raster size as a numpy array of shape (2,).

        Returns
        -------
            aligned_coordinates: np.ndarray
                The aligned coordinates as a numpy array of shape (N, 2).
        '''

        # Set the reference image and target coordinates
        self._reference_image = Image.fromarray(reference_image)
        self._raster_size = raster_size
        self._target_coordinates = target_coordinates
        self._aligned_coordinates = None

        # Clear the user event
        self._user_event.clear()
        print(f"Please align the coordinates for sample '{sample_id}' using the GUI.")

        # Wait for the user to save the aligned coordinates
        self._user_event.wait()

        print(f"Aligned coordinates for sample '{sample_id}' have been saved.")
        self._reference_image = None
        self._target_coordinates = None
        self._raster_size = None

        return self._aligned_coordinates
        
    def enable_gui(self):

        # Start a thread that will pend on the dataset completed event
        threading.Thread(target=self._disable_gui, daemon=True).start()
        
        self._server = make_server('localhost', 8080, self.app)
        self._server.serve_forever()

    def _disable_gui(self):
        
        # Wait for the dataset to be completed
        self._dataset_completed_event.wait()

        if self._server:
            self._server.shutdown()
            self._server = None

