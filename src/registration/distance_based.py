import tqdm
import numpy as np

from constants import ModalityType

class MicroscopyImageFeatureExtractor:
	def __init__(self, path: str, hf_token: str = None):

		self.source_path = path
		self.hf_token = hf_token
		self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

		huggingface_hub.login(token=self.hf_token)

		# Create the patch encoder model
		self.patch_encoder = timm.create_model("hf_hub:prov-gigapath/prov-gigapath", pretrained=True)
		self.patch_encoder.eval()
		self.patch_encoder.to(self.device)

	def _extract_patches(self, img: np.ndarray, patch_size: int = 224, patch_centers: np.ndarray = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
		"""
		Extract patches from an image. If patch_centers is None, extracts non-overlapping patches.
		If patch_centers is provided, extracts patches centered at those coordinates.
		
		Parameters
		----------
		img : np.ndarray
			The input image as a NumPy array of shape (H, W, C).
		patch_size : int
			The size of the patches to extract (default is 224).
		patch_centers : np.ndarray, optional
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the patch centers.
			If None, non-overlapping patches are extracted across the entire image.
		
		Returns
		-------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		top_left_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch.
		center_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the center of each patch.
		"""
		
		# Handle different formats
		if img.ndim == 2:
			img = img[..., None]
		h, w, c = img.shape
		if c == 1:
			img = np.repeat(img, 3, axis=2)
		if c == 4:
			img = img[..., :3]
		
		half_patch = patch_size // 2
		
		if patch_centers is not None:
			# Extract patches centered at provided coordinates
			patch_centers = np.asarray(patch_centers, dtype=np.float32)
			n_patches = patch_centers.shape[0]
			
			patches = []
			top_left_coords = []
			center_coords = []
			
			for i in range(n_patches):
				cx, cy = patch_centers[i]
				
				# Compute top-left corner
				x0 = int(cx - half_patch)
				y0 = int(cy - half_patch)
				
				# Clamp to image boundaries
				x0 = max(0, min(x0, w - patch_size))
				y0 = max(0, min(y0, h - patch_size))
				
				# Extract patch
				patch = img[y0:y0+patch_size, x0:x0+patch_size, :]
				
				# Handle edge cases where patch might be smaller than patch_size
				if patch.shape[0] < patch_size or patch.shape[1] < patch_size:
					padded = np.zeros((patch_size, patch_size, 3), dtype=img.dtype)
					padded[:patch.shape[0], :patch.shape[1], :] = patch
					patch = padded
				
				patches.append(patch)
				top_left_coords.append([x0, y0])
				
				# Compute actual center (might differ slightly from requested if clamped)
				actual_center_x = x0 + half_patch
				actual_center_y = y0 + half_patch
				center_coords.append([actual_center_x, actual_center_y])
			
			patches = np.array(patches, dtype=np.float32)
			top_left_coordinates = np.array(top_left_coords, dtype=np.float32)
			center_coordinates = np.array(center_coords, dtype=np.float32)
			
		else:
			# Extract non-overlapping patches
			n_patches_y = h // patch_size
			n_patches_x = w // patch_size
			
			# Crop image to fit exact number of patches
			h_crop = n_patches_y * patch_size
			w_crop = n_patches_x * patch_size
			img = img[:h_crop, :w_crop, :]
			
			# Reshape into patches using stride tricks
			patches = img.reshape(n_patches_y, patch_size, n_patches_x, patch_size, 3)
			patches = patches.transpose(0, 2, 1, 3, 4)  # (n_y, n_x, h, w, c)
			patches = patches.reshape(-1, patch_size, patch_size, 3).astype(np.float32)
			
			# Generate top-left coordinates
			y_coords = np.arange(n_patches_y) * patch_size
			x_coords = np.arange(n_patches_x) * patch_size
			xx, yy = np.meshgrid(x_coords, y_coords)
			top_left_coordinates = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(np.float32)
			
			# Compute center coordinates
			center_coordinates = top_left_coordinates + half_patch
		
		return patches, top_left_coordinates, center_coordinates

	def _filter_empty_patches(self, patches: np.ndarray, topleft_coordinates: np.ndarray, center_coordinates: np.ndarray, background_color: SegmentationBackgroundColor) -> tuple[np.ndarray, np.ndarray]:
		"""
		Filter out patches that are empty (background). A patch is considered empty if the 99% of its pixels are
		the background color.

		Parameters
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		topleft_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
			in the original image.
		center_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the center of each patch
			in the original image.
		background_color : SegmentationBackgroundColor
			The background color to use for filtering.
		
		Returns
		----------
		filtered_patches : np.ndarray
			A NumPy array containing only the non-empty patches.
		filtered_coordinates : np.ndarray
			A NumPy array containing the coordinates of the non-empty patches.
		"""

		if background_color == SegmentationBackgroundColor.WHITE:
			bg_color = np.array([1.0, 1.0, 1.0], dtype=np.float32)
		elif background_color == SegmentationBackgroundColor.BLACK:
			bg_color = np.array([0.0, 0.0, 0.0], dtype=np.float32)
		else:
			raise ValueError(f"Unsupported background color: {background_color}")

		# Calculate the number of background pixels in each patch
		bg_mask = np.all(np.isclose(patches, bg_color, atol=1e-3), axis=-1)  # shape (N, patch_size, patch_size)
		bg_pixel_counts = np.sum(bg_mask, axis=(1, 2))  # shape (N,)

		# Determine threshold for filtering (99% background)
		patch_area = patches.shape[1] * patches.shape[2]
		threshold = patch_area * 0.99

		# Filter patches and coordinates
		valid_indices = np.where(bg_pixel_counts < threshold)[0]
		filtered_patches = patches[valid_indices]
		filtered_topleft_coordinates = topleft_coordinates[valid_indices]
		filtered_center_coordinates = center_coordinates[valid_indices]

		return filtered_patches, filtered_topleft_coordinates, filtered_center_coordinates

	def _extract_patch_embeddings(self, patches: np.ndarray) -> np.ndarray:
		"""
		Extract embeddings from the image patches using a pre-trained model.

		Parameters
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		topleft_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
			in the original image.

		Returns
		----------
		patch_embeddings : np.ndarray
			A NumPy array of shape (N, embedding_size) containing the patch embeddings before slide refinement.
		"""

		# Convert patches and coordinates to torch tensors
		patches_tensor = torch.from_numpy(patches).permute(0, 3, 1, 2).to(self.device)  # shape (N, C, H, W)

		# Apply normalization as defined for this pretrained model
		mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1).to(self.device)
		std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1).to(self.device)
		patches_tensor = (patches_tensor - mean) / std

		# Create a Dataset and a DataLoader
		dataset = torch.utils.data.TensorDataset(patches_tensor)
		dataloader = torch.utils.data.DataLoader(dataset, batch_size=32, shuffle=False)

		embeddings: list[np.ndarray] = []

		# Extract embeddings for the current level
		with torch.inference_mode():
			for batch in tqdm.tqdm(dataloader, desc=f"Extracting patch embeddings", unit="batch"):
				input_tensor = batch[0].to(self.device)                      			# Shape [B, 3, 224, 224]
				embeddings.append(self.patch_encoder(input_tensor).cpu().numpy())       # Shape [B, 1536]

		embeddings: np.ndarray = np.concatenate(embeddings, axis=0)  					# Shape [N, 1536]

		# Free memory
		del mean, std, patches_tensor, dataset, dataloader
		torch.cuda.empty_cache()

		return embeddings
	
	def extract_features(
		self,
		image: np.ndarray,
		patch_centers: np.ndarray | None = None,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		patch_size: int = 224,
	) -> tuple[np.ndarray, np.ndarray]:
		'''
		Use the patch extractor to compute patch embeddings for the image.
		
		Parameters
		----------
		image : np.ndarray
			The input microscopy image as a NumPy array of shape (H, W, C).
		patch_centers : np.ndarray, optional
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the patch centers to extract.
			If None, non-overlapping patches are extracted across the entire image foreground.
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal. This is usefull to match the requirements of futher processing steps.
		patch_size : int
			The size of the patches to use for background removal (default is 224).
		patch_centers : np.ndarray, optional
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the patch centers to extract.
			If None, non-overlapping patches are extracted across the entire image foreground.

		Returns
		-------
		patch_embeddings : np.ndarray
			A NumPy array of shape (N, embedding_size) containing the patch embeddings.
		center_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the center of each patch in the original image.
		'''

		# Extract patch embeddings
		patches, topleft_coordinates, center_coordinates = self._extract_patches(image, patch_size, patch_centers)
		patches, topleft_coordinates, center_coordinates = self._filter_empty_patches(patches, topleft_coordinates, center_coordinates, background_color)
		patch_embeddings = self._extract_patch_embeddings(patches)
		return patch_embeddings, center_coordinates