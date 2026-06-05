import timm, torch, huggingface_hub, tqdm
import numpy as np

from focus.constants import SegmentationBackgroundColor
from focus.registration._utils import (
	ensure_hwc3,
	compute_patch_coordinates,
	cut_patch_batch,
	background_mask,
	resolve_bg_color,
)

# --- Internal batch-sizing constants (no user-facing configuration) -----------
# The batch size is chosen automatically from the free GPU memory via an
# empirical probe; these only bound and seed that estimate.
_PROBE_BATCH = 8          # patches forwarded once to measure per-sample GPU cost
_MIN_BATCH = 8            # never go below this on GPU
_MAX_BATCH = 512          # caps GPU batch *and* the in-flight patch RAM
_SAFETY_FRACTION = 0.8    # fraction of available GPU memory we allow ourselves
_CPU_BATCH = 32           # fixed batch when running on CPU (no GPU memory limit)

# torch>=2.0 raises a dedicated OOM error; fall back to RuntimeError otherwise.
_OOMError = getattr(torch.cuda, "OutOfMemoryError", RuntimeError)


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

		# ImageNet normalization constants for this pretrained model, kept resident
		# on the device as broadcastable (1, 3, 1, 1) tensors and applied per batch.
		self._mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1).to(self.device)
		self._std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1).to(self.device)

	# ------------------------------------------------------------------ encoding

	def _encode_np_batch(self, patch_batch: np.ndarray) -> np.ndarray:
		"""
		Encode a single CPU patch batch and return its embeddings on the CPU.

		This is the one place patches are moved to the GPU. Only ``patch_batch``
		(at most ``_MAX_BATCH`` patches) is ever resident on the device, so GPU
		memory is bounded regardless of how many patches the dataset contains.

		Parameters
		----------
		patch_batch : np.ndarray
			``(B, H, W, 3)`` float32 patches in the image's [0, 1] value range.

		Returns
		-------
		np.ndarray
			``(B, embedding_size)`` float32 embeddings.
		"""
		# (B, H, W, C) -> (B, C, H, W); .contiguous() so the host buffer can be pinned.
		x = torch.from_numpy(patch_batch).permute(0, 3, 1, 2).contiguous()
		if self.device.type == "cuda":
			x = x.pin_memory()
		x = x.to(self.device, non_blocking=True)
		x = (x - self._mean) / self._std
		with torch.inference_mode():
			emb = self.patch_encoder(x)
		return emb.float().cpu().numpy()

	def _forward_with_oom_retry(self, patches: np.ndarray, batch_size: int) -> tuple[np.ndarray, int]:
		"""
		Encode ``patches`` (n, H, W, 3), recovering from a CUDA OOM.

		The probe sizes batches conservatively, but cuDNN workspace growth on the
		first real batch or another process grabbing memory can still trigger an
		OOM. On OOM we free the cache, halve the working batch size, split, and
		retry — and return the (possibly reduced) batch size so the caller shrinks
		subsequent chunks too.
		"""
		try:
			return self._encode_np_batch(patches), batch_size
		except _OOMError:
			if self.device.type == "cuda":
				torch.cuda.empty_cache()
			new_bs = max(_MIN_BATCH, batch_size // 2)
			if new_bs >= batch_size:
				# Can't shrink any further — re-raise rather than spin forever.
				raise
			outs = []
			for s in range(0, patches.shape[0], new_bs):
				emb, new_bs = self._forward_with_oom_retry(patches[s:s + new_bs], new_bs)
				outs.append(emb)
			return np.concatenate(outs, axis=0), new_bs

	def _estimate_batch_size(self, sample_patch: np.ndarray) -> int:
		"""
		Conservatively choose a batch size from the free GPU memory.

		Runs one forward pass on ``_PROBE_BATCH`` copies of a representative patch
		(through the exact path real batches use) to measure the per-sample
		activation cost, then sizes the batch to ``_SAFETY_FRACTION`` of the memory
		actually available to us (free device memory plus torch's reusable cache).
		On CPU there is no such limit, so a fixed batch is used.

		Parameters
		----------
		sample_patch : np.ndarray
			A ``(1, H, W, 3)`` float32 patch used to probe the model.

		Returns
		-------
		int
			The chosen batch size.
		"""
		if self.device.type != "cuda":
			return _CPU_BATCH

		torch.cuda.empty_cache()
		torch.cuda.reset_peak_memory_stats(self.device)
		base = torch.cuda.memory_allocated(self.device)  # resident model weights

		trial = np.repeat(sample_patch, _PROBE_BATCH, axis=0)
		self._encode_np_batch(trial)
		peak = torch.cuda.max_memory_allocated(self.device)

		per_sample = max(1.0, (peak - base) / _PROBE_BATCH)
		free, _ = torch.cuda.mem_get_info(self.device)
		reusable = torch.cuda.memory_reserved(self.device) - torch.cuda.memory_allocated(self.device)
		available = free + reusable

		batch = int(_SAFETY_FRACTION * available / per_sample)
		batch = max(_MIN_BATCH, min(_MAX_BATCH, batch))
		batch -= batch % 8                       # tensor-core friendly
		batch = max(_MIN_BATCH, batch)

		torch.cuda.empty_cache()
		return batch

	def _embed_patches_streaming(
		self,
		img: np.ndarray,
		top_left: np.ndarray,
		bg_color: np.ndarray,
		patch_size: int,
		step_reporter,
		anchor_mode: bool,
	) -> tuple[np.ndarray, np.ndarray]:
		"""
		Stream patches through the encoder in GPU-memory-bounded batches.

		Patches are cut from the resident image on the fly (never materialized as
		one giant array), background patches are skipped through the network, and
		only one batch is on the GPU at a time.

		Parameters
		----------
		img : np.ndarray
			HWC (3-channel) image to cut patches from.
		top_left : np.ndarray
			``(M, 2)`` int32 top-left coordinates of every patch.
		bg_color : np.ndarray
			``(3,)`` background color used to detect empty patches.
		patch_size : int
			Side length of each square patch.
		step_reporter : StepReporter, optional
			Reports per-patch progress to the GUI.
		anchor_mode : bool
			True for anchor-based extraction (one output row per patch; background
			rows are all-zero). False for free-form extraction (background patches
			are dropped from the output).

		Returns
		-------
		embeddings : np.ndarray
			``(M, D)`` with zero background rows (anchor mode), or
			``(n_foreground, D)`` (free-form mode).
		fg_index : np.ndarray
			Indices into ``top_left`` of the foreground patches, in output order.
		"""
		n_patches = top_left.shape[0]

		# Probe a representative patch to choose the batch size.
		sample = cut_patch_batch(img, top_left[:1], patch_size)
		batch_size = self._estimate_batch_size(sample)

		if step_reporter:
			step_reporter.step("Extracting patch embeddings", 0, n_patches)

		embeddings = None            # anchor: preallocated (M, D); free-form: stays None
		fg_emb_chunks: list[np.ndarray] = []   # free-form accumulation
		fg_index_chunks: list[np.ndarray] = []
		embedding_dim = None
		fg_count = 0
		processed = 0

		pbar = tqdm.tqdm(total=n_patches, desc="Extracting patch embeddings", unit="patch")
		start = 0
		while start < n_patches:
			end = min(start + batch_size, n_patches)
			patches = cut_patch_batch(img, top_left[start:end], patch_size)

			# Foreground patches within this chunk (background is skipped/zeroed).
			fg_local = np.where(~background_mask(patches, bg_color))[0]

			if fg_local.size > 0:
				emb, batch_size = self._forward_with_oom_retry(patches[fg_local], batch_size)

				if embedding_dim is None:
					embedding_dim = emb.shape[1]
					if anchor_mode:
						embeddings = np.zeros((n_patches, embedding_dim), dtype=np.float32)

				global_fg = np.arange(start, end)[fg_local]
				if anchor_mode:
					embeddings[global_fg] = emb
				else:
					fg_emb_chunks.append(emb)
				fg_index_chunks.append(global_fg)
				fg_count += fg_local.size

			processed = end
			pbar.update(end - start)
			if step_reporter:
				step_reporter.update("Extracting patch embeddings", processed, n_patches)
			start = end
		pbar.close()

		fg_index = (np.concatenate(fg_index_chunks) if fg_index_chunks
					else np.zeros((0,), dtype=np.int64))

		# Every patch was background: learn the embedding dim from one dummy
		# forward so the output keeps the correct width (and row count).
		if embedding_dim is None:
			embedding_dim = self._encode_np_batch(sample).shape[1]

		if anchor_mode:
			if embeddings is None:
				embeddings = np.zeros((n_patches, embedding_dim), dtype=np.float32)
			assert embeddings.shape[0] == n_patches, (embeddings.shape, n_patches)
		else:
			embeddings = (np.concatenate(fg_emb_chunks, axis=0) if fg_emb_chunks
						  else np.zeros((0, embedding_dim), dtype=np.float32))
			assert embeddings.shape[0] == fg_count == fg_index.shape[0]

		if self.device.type == "cuda":
			torch.cuda.empty_cache()

		return embeddings, fg_index

	# ------------------------------------------------------------------- public

	def extract_features(
		self,
		image: np.ndarray,
		patch_centers: np.ndarray | None = None,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		patch_size: int = 224,
		step_reporter=None,
	) -> tuple[np.ndarray, np.ndarray]:
		'''
		Use the patch extractor to compute patch embeddings for the image.

		Patches are streamed (cut on the fly) from the resident image and encoded
		in GPU-memory-bounded batches whose size is chosen automatically, so both
		RAM and GPU memory stay flat as the patch count grows into the millions.

		When ``patch_centers`` is provided (anchor-based registration), the output
		contains exactly one row per input center.  Background-only patches receive
		a zero embedding vector so the observation count stays aligned with the
		anchor modality (required for MuData compilation).  Only valid patches are
		actually forwarded through the neural network for efficiency.

		When ``patch_centers`` is None, non-overlapping patches are extracted across
		the image foreground and background patches are removed (original behaviour).

		Parameters
		----------
		image : np.ndarray
			The input microscopy image as a NumPy array of shape (H, W, C).
		patch_centers : np.ndarray, optional
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the
			patch centers to extract.  If None, non-overlapping patches are extracted
			across the entire image foreground.
		background_color : SegmentationBackgroundColor
			The color used to identify background pixels.
		patch_size : int
			The size of the patches to extract (default is 224).

		Returns
		-------
		patch_embeddings : np.ndarray
			Shape (N, embedding_size).  When patch_centers is provided N equals
			len(patch_centers); background patches have all-zero embeddings.
		center_coordinates : np.ndarray
			Shape (N, 2) — actual centre pixel positions for each patch.
		'''
		img = ensure_hwc3(image)
		top_left, center_coordinates = compute_patch_coordinates(img.shape, patch_size, patch_centers)
		n_patches = top_left.shape[0]

		if n_patches == 0:
			return np.zeros((0, 0), dtype=np.float32), center_coordinates

		bg_color = resolve_bg_color(background_color)
		anchor_mode = patch_centers is not None

		patch_embeddings, fg_index = self._embed_patches_streaming(
			img, top_left, bg_color, patch_size, step_reporter, anchor_mode
		)

		if anchor_mode:
			# One row per anchor spot (background rows are zero); centers unchanged.
			return patch_embeddings, center_coordinates
		# Free-form: keep only foreground rows and their centers, in matching order.
		return patch_embeddings, center_coordinates[fg_index]
