import os, time
import numpy as np
from PIL import Image
from typing import Callable


from io import BytesIO

from flask import Flask, request, jsonify, send_file
from werkzeug.serving import make_server
from threading import Thread

class LandmarkSelectionGUI:
    '''
    A GUI to allow the user to select landmarks on two images to proceed with alignment.

    Parameters
    ----------
    fixed_image : np.ndarray
        The fixed image to be used for alignment.
    moving_image : np.ndarray
        The moving image to be aligned to the fixed image.
    save_landmarks_callback : Callable
        A callback function to save the selected landmarks.
    '''

    def __init__(self, fixed_image: np.ndarray, moving_image: np.ndarray, save_landmarks_callback: Callable):

        if not isinstance(fixed_image, np.ndarray) or not isinstance(moving_image, np.ndarray):
            raise TypeError("The images must be numpy arrays.")
        if not callable(save_landmarks_callback):
            raise TypeError("The callback function must be callable.")

        # Check if the images are float32 and normalized
        if fixed_image.dtype != np.float32 or moving_image.dtype != np.float32:
            raise TypeError("The images must be of type float32.")
        if not (0 <= fixed_image.min() <= 1 and 0 <= fixed_image.max() <= 1):
            raise ValueError("The fixed image must be normalized between 0 and 1.")
        if not (0 <= moving_image.min() <= 1 and 0 <= moving_image.max() <= 1):
            raise ValueError("The moving image must be normalized between 0 and 1.")
        
        self.save_landmarks_callback = save_landmarks_callback
        self.fixed_image = self._convert_image_PIL(fixed_image)
        self.moving_image = self._convert_image_PIL(moving_image)

        self.app = Flask(__name__)
        self._server = None
        self._register_routes()

    def _register_routes(self):
        @self.app.route('/')
        def index():
            # Get the absolute path of the HTML file
            html_path = os.path.join(os.path.dirname(__file__), "landmark_selection.html")

            # Read the HTML file
            with open(html_path, 'r') as file:
                html_content = file.read()
            
            return html_content

        @self.app.route('/get_image/<image_id>')
        def get_image(image_id: str):
            if image_id not in ["fixed", "moving"]:
                return jsonify({"error": "Invalid image ID"}), 400
            
            # Convert the image to base64
            if image_id == "fixed":
               img = self.fixed_image
            elif image_id == "moving":
                img = self.moving_image

            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            return send_file(buffer, mimetype='image/png')
        
        @self.app.route('/save_landmarks', methods=['POST'])
        def save_landmarks():
            data = request.get_json()

            fixed_landmarks = data.get('fixed_landmarks')
            moving_landmarks = data.get('moving_landmarks')

            fixed_landmarks_np = np.array([(point['x'], point['y']) for point in fixed_landmarks], dtype=np.int32)
            moving_landmarks_np = np.array([(point['x'], point['y']) for point in moving_landmarks], dtype=np.int32)

            self.save_landmarks_callback(fixed_landmarks_np, moving_landmarks_np)
            Thread(target=self.disable_gui, daemon=True).start()
            return jsonify({"status": "success", "message": "Landmarks saved successfully."})
        
    def enable_gui(self):
        self._server = make_server('localhost', 5000, self.app)
        self._server.serve_forever()

    def disable_gui(self):
        time.sleep(1)  # Give some time for the server respond to client
        if self._server:
            self._server.shutdown()
            self._server = None


    def _convert_image_PIL(self, image: np.ndarray) -> Image.Image:
        '''
        Convert a numpy array to a base64 string.

        Parameters
        ----------
        image : np.ndarray
            The image to convert.

        Returns
        ----------
        str
            The base64 string representation of the image.
        '''
        if not isinstance(image, np.ndarray):
            raise TypeError("The input must be a numpy array.")
        
        # Evaluate the image type
        if image.dtype != np.uint8:
            image = (image * 255).astype(np.uint8)

        # Convert the image to a PIL image
        if len(image.shape) == 2:  # Grayscale
            pil_image = Image.fromarray(image, mode='L')
        else:  # RGB (assuming 3-channel array)
            pil_image = Image.fromarray(image, mode='RGB')

        return pil_image
        
    def get_html(self) -> str:
        '''
        Get the HTML content for the GUI.

        Returns
        ----------
        str
            The HTML content for the GUI.
        '''
        
        # Get the absolute path of the HTML file
        html_path = os.path.join(os.path.dirname(__file__), "landmark_selection.html")

        # Read the HTML file
        with open(html_path, 'r') as file:
            html_content = file.read()
        
        return html_path

    def get_image(self, image_id: str):
        '''
        Get the image from the GUI.

        Parameters
        ----------
        image_id : str
            The ID of the image to get.
        
        Returns
        ----------
        str
            The base64 string representation of the image.
        '''
        if image_id == "fixed":
            return self._convert_image_base64(self.fixed_image)
        elif image_id == "moving":
            return self._convert_image_base64(self.moving_image)
        else:
            # Placeholder for error
            return "Invalid image ID"

