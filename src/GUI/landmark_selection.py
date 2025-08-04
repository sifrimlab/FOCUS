import os, time, math
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
    image_size_cap : int | None, optional
        The maximum size of the images to be displayed in the GUI. If None, no cap is applied. Default is None.
    '''

    def __init__(self, fixed_image: np.ndarray, moving_image: np.ndarray, save_landmarks_callback: Callable, image_size_cap: int | None = None):

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
        
        self._image_size_cap = image_size_cap
        self._original_size_fixed = fixed_image.shape[:2]
        self._original_size_moving = moving_image.shape[:2]
        
        self.save_landmarks_callback = save_landmarks_callback
        self.fixed_image, self._applied_scaling_factor_fixed = self._convert_image_PIL(fixed_image)
        self.moving_image, self._applied_scaling_factor_moving = self._convert_image_PIL(moving_image)

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
            moving_image_xflip = data.get('moving_image_xflip', False)
            moving_image_yflip = data.get('moving_image_yflip', False)

            fixed_landmarks_np = np.array([(point['x'], point['y']) for point in fixed_landmarks], dtype=np.int32)
            moving_landmarks_np = np.array([(point['x'], point['y']) for point in moving_landmarks], dtype=np.int32)

            # Convert the landmarks to the original scale
            #fixed_landmarks_np = self._map_landmark_to_original_scale(fixed_landmarks_np, self._applied_scaling_factor_fixed, self._original_size_fixed)
            #moving_landmarks_np = self._map_landmark_to_original_scale(moving_landmarks_np, self._applied_scaling_factor_moving, self._original_size_moving)

            self.save_landmarks_callback(fixed_landmarks_np, moving_landmarks_np, moving_image_xflip, moving_image_yflip)
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
        
        # Check if it's necessary to resize the image
        if self._image_size_cap is not None:
            if image.ndim == 3:
                W, H, C = image.shape
            else:
                W, H = image.shape
                C = 1

            scaling_factor = max(1, math.ceil(max(W, H) / self._image_size_cap))

            N_W = W // scaling_factor
            N_H = H // scaling_factor

            # Downscale the image by computing local averages
            trimmed_height = N_H * scaling_factor
            trimmed_width = N_W * scaling_factor
            trimmed_data = image[:trimmed_width, :trimmed_height, :] if image.ndim == 3 else image[:trimmed_width, :trimmed_height]

            # Vectorized mean pooling
            if image.ndim == 2:
                reshaped = trimmed_data.reshape(N_W, scaling_factor, N_H, scaling_factor)
                downscaled_image = np.percentile(reshaped, 95, axis=(1, 3))
                downscaled_image = ((downscaled_image - downscaled_image.min()) / (downscaled_image.max() - downscaled_image.min()))
            else:
                reshaped = trimmed_data.reshape(N_W, scaling_factor, N_H, scaling_factor, C)
                downscaled_image = np.percentile(reshaped, 95, axis=(1, 3))

                for channel in range(C):
                    downscaled_image[:, :, channel] = ((downscaled_image[:, :, channel] - downscaled_image[:, :, channel].min()) / (downscaled_image[:, :, channel].max() - downscaled_image[:, :, channel].min()))

            self._applied_scaling_factor = scaling_factor
        else:
            downscaled_image = image
            scaling_factor = 1.0
        
        # Evaluate the image type
        if downscaled_image.dtype != np.uint8:
            downscaled_image = (downscaled_image * 255).astype(np.uint8)

        # Convert the image to a PIL image
        if len(downscaled_image.shape) == 2:  # Grayscale
            pil_image = Image.fromarray(downscaled_image, mode='L')
        else:  # RGB (assuming 3-channel array)
            pil_image = Image.fromarray(downscaled_image, mode='RGB')

        return pil_image, scaling_factor


    def _map_landmark_to_original_scale(self, landmarks: np.ndarray[np.int32], scaling_factor: int, original_shape: np.ndarray[np.int32]) -> np.ndarray[np.int32]:
        '''
        Given the landmarks in the downscaled image, map them back to the original image scale.

        Parameters
        ----------
        landmarks : np.ndarray[np.int32]
            The landmarks in the downscaled image.
        scaling_factor : int
            The scaling factor used to downscale the image.
        original_shape : np.ndarray[np.int32]
            The original shape of the image before downscaling.

        Returns
        ----------
        np.ndarray[np.int32]
            The landmarks mapped to the original image scale.
        '''
        
        # Top-left corner of the source cluster
        x_orig_start = landmarks[:, 0] * scaling_factor
        y_orig_start = landmarks[:, 1] * scaling_factor
        
        # Bottom-right corner (adjusted for edge clusters)
        x_orig_end = min((landmarks[:, 0] + 1) * scaling_factor, original_shape[0])
        y_orig_end = min((landmarks[:, 1] + 1) * scaling_factor, original_shape[0])
        
        # Center of the cluster
        centers = np.zeros_like(landmarks, dtype=np.int32)
        centers[:, 0] = (x_orig_start + x_orig_end) // 2
        centers[:, 1] = (y_orig_start + y_orig_end) // 2
        
        return centers
