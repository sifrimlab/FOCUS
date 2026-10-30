import timm, torch, huggingface_hub, tqdm
import numpy as np

from focus.constants import SegmentationBackgroundColor
from focus.registration._utils import (
	ensure_hwc3,
	compute_patch_coordinates,
	cut_patch_batch,
	cut_window_batch,
	background_mask,
	token_background_mask,
	resolve_bg_color,
)
from focus.registration._readout_plan import TokenGrid, plan_windows, readout_membership

# --- Internal batch-sizing constants (no user-facing configuration) -----------
# The batch size is chosen automatically from the free GPU memory via an
# empirical probe; these only bound and seed that estimate.
_PROBE_BATCH = 8          # windows forwarded once to measure per-window GPU cost
_MAX_BATCH = 512          # caps the GPU batch
_MAX_BATCH_PIXELS = 512 * 224 * 224   # caps the in-flight window pixels (host RAM)
_SAFETY_FRACTION = 0.8    # fraction of available GPU memory we allow ourselves
_CPU_BATCH = 32           # batch cap when running on CPU (no GPU memory limit)

# torch>=2.0 raises a dedicated OOM error; fall back to RuntimeError otherwise.
_OOMError = getattr(torch.cuda, "OutOfMemoryError", RuntimeError)


class MicroscopyImageFeatureExtractor:
	"""
	Prov-GigaPath tile encoder with a footprint-restricted CLS readout.

	``extract_spot_features`` gives every anchor spot the CLS embedding of exactly the tissue
	inside its footprint: the image is encoded in windows, and each spot appends its own CLS
	query to its window's token sequence. Through the attention mask that query attends only to
	itself and to the tokens whose centres lie inside the footprint, while the window's own CLS
	and patch tokens attend to each other exactly as in a standard forward pass (they never see
	the queries). A query covering every token therefore reproduces the standard CLS embedding,
	which is checked when the model is loaded.

	``extract_grid_features`` is the plain per-tile CLS on a non-overlapping grid (anchorless use).
	"""

	MODEL_ID = "prov-gigapath/prov-gigapath"

	def __init__(self, path: str, hf_token: str = None, model=None):

		self.source_path = path
		self.hf_token = hf_token
		self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

		if model is None:
			huggingface_hub.login(token=self.hf_token)
			# dynamic_img_size: windows larger than 224 px get interpolated position embeddings.
			model = timm.create_model(f"hf_hub:{self.MODEL_ID}", pretrained=True, dynamic_img_size=True)
		self.patch_encoder = model
		self.patch_encoder.eval()
		self.patch_encoder.to(self.device)

		# ImageNet normalization constants for this pretrained model, kept resident
		# on the device as broadcastable (1, 3, 1, 1) tensors and applied per batch.
		self._mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1).to(self.device)
		self._std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1).to(self.device)

		m = self.patch_encoder
		if not (getattr(m, "has_class_token", False) and m.global_pool == "token"
				and getattr(m, "attn_pool", None) is None and m.pos_embed is not None):
			raise RuntimeError("The encoder must be a ViT with a class token, token pooling and position embeddings")
		self.grid = TokenGrid(patch=int(m.patch_embed.patch_size[0]), base_side=int(m.patch_embed.img_size[0]))
		self.n_prefix = int(m.num_prefix_tokens)
		self.embedding_dim = int(m.num_features)
		self._verify_readout()

	# ------------------------------------------------------------------ encoding

	def _to_device(self, patch_batch: np.ndarray) -> torch.Tensor:
		"""(B, H, W, 3) float32 [0, 1] host batch -> normalised (B, 3, H, W) device tensor."""
		# .contiguous() so the host buffer can be pinned.
		x = torch.from_numpy(patch_batch).permute(0, 3, 1, 2).contiguous()
		if self.device.type == "cuda":
			x = x.pin_memory()
		x = x.to(self.device, non_blocking=True)
		return (x - self._mean) / self._std

	def _encode_np_batch(self, patch_batch: np.ndarray) -> np.ndarray:
		"""Standard CLS embedding of each window of a ``(B, H, W, 3)`` batch, on the CPU."""
		x = self._to_device(patch_batch)
		with torch.inference_mode():
			emb = self.patch_encoder(x)
		return emb.float().cpu().numpy()

	def _readout_mask(self, member: torch.Tensor, n_seq: int) -> torch.Tensor:
		"""
		``(B, 1, L, L)`` boolean attention mask, True = may attend, for ``n_seq`` window tokens
		(prefix + patches) followed by ``R`` readout queries.

		The window block attends to itself only (a standard forward). Readout ``r`` attends to
		itself, to any register tokens, and to the patch tokens in ``member[:, r]``; never to the
		window's CLS or to other readouts.
		"""
		b, r, t = member.shape
		length = n_seq + r
		mask = torch.zeros((b, 1, length, length), dtype=torch.bool, device=member.device)
		mask[:, :, :n_seq, :n_seq] = True
		if r:
			mask[:, 0, n_seq:, self.n_prefix:n_seq] = member
			if self.n_prefix > 1:
				mask[:, 0, n_seq:, 1:self.n_prefix] = True
			idx = torch.arange(n_seq, length, device=member.device)
			mask[:, 0, idx, idx] = True
		return mask

	def _forward_readouts(self, x: torch.Tensor, member: torch.Tensor) -> torch.Tensor:
		"""Readout embeddings ``(B, R, D)`` of normalised windows ``x`` for memberships ``(B, R, T)``."""
		m = self.patch_encoder
		t = m.patch_drop(m._pos_embed(m.patch_embed(x)))
		b, n_seq, c = t.shape
		# Every readout starts from the window CLS's initial state (cls_token + its position embedding).
		t = torch.cat([t, t[:, :1].expand(b, member.shape[1], c)], dim=1)
		t = m.norm_pre(t)
		mask = self._readout_mask(member, n_seq)
		for blk in m.blocks:
			t = blk(t, attn_mask=mask)
		t = m.norm(t)[:, n_seq:]
		return m.head(m.head_drop(m.fc_norm(t)))

	def _encode_readouts(self, windows: np.ndarray, member: np.ndarray) -> np.ndarray:
		x = self._to_device(windows)
		mem = torch.from_numpy(member).to(self.device)
		with torch.inference_mode():
			emb = self._forward_readouts(x, mem)
		return emb.float().cpu().numpy()

	def _verify_readout(self) -> None:
		"""A readout over every token must equal the standard CLS embedding of the same window."""
		side = self.grid.base_side
		gen = torch.Generator().manual_seed(0)
		x = torch.rand((1, side, side, 3), generator=gen).numpy()
		n_tokens = (side // self.grid.patch) ** 2
		standard = self._encode_np_batch(x)[0]
		readout = self._encode_readouts(x, np.ones((1, 1, n_tokens), dtype=bool))[0, 0]
		scale = float(np.abs(standard).max()) or 1.0
		if not np.allclose(readout, standard, rtol=1e-3, atol=1e-3 * scale):
			raise RuntimeError(
				"Footprint readout self-test failed: a readout over the whole window does not reproduce "
				f"the encoder's CLS embedding (max difference {np.abs(readout - standard).max():.3g})"
			)

	def _forward_with_oom_retry(self, encode, windows: np.ndarray, member: np.ndarray | None,
								batch_size: int) -> tuple[np.ndarray, int]:
		"""
		Encode a batch, recovering from a CUDA OOM.

		The probe sizes batches conservatively, but cuDNN workspace growth on the
		first real batch or another process grabbing memory can still trigger an
		OOM. On OOM we free the cache, halve the working batch size, split, and
		retry, and return the (possibly reduced) batch size so the caller shrinks
		subsequent batches too.
		"""
		try:
			return (encode(windows) if member is None else encode(windows, member)), batch_size
		except _OOMError:
			if self.device.type == "cuda":
				torch.cuda.empty_cache()
			new_bs = max(1, min(batch_size, len(windows)) // 2)
			if len(windows) <= 1:
				# Can't shrink any further: re-raise rather than spin forever.
				raise
			outs = []
			for s in range(0, len(windows), new_bs):
				part = None if member is None else member[s:s + new_bs]
				emb, new_bs = self._forward_with_oom_retry(encode, windows[s:s + new_bs], part, new_bs)
				outs.append(emb)
			return np.concatenate(outs, axis=0), new_bs

	def _estimate_batch_size(self, windows: np.ndarray, member: np.ndarray | None) -> int:
		"""
		Conservatively choose a batch size for windows shaped like ``windows[0]``.

		Runs one forward pass on copies of a representative window (through the exact path
		real batches use) to measure the per-window activation cost, then sizes the batch to
		``_SAFETY_FRACTION`` of the memory actually available (free device memory plus torch's
		reusable cache). The batch is also capped by ``_MAX_BATCH_PIXELS`` so host memory
		stays flat for large windows.
		"""
		h, w = windows.shape[1:3]
		pixel_cap = max(1, _MAX_BATCH_PIXELS // (h * w))
		if self.device.type != "cuda":
			return max(1, min(_CPU_BATCH, pixel_cap))

		torch.cuda.empty_cache()
		probe = min(_PROBE_BATCH, pixel_cap)
		while True:
			torch.cuda.reset_peak_memory_stats(self.device)
			base = torch.cuda.memory_allocated(self.device)  # resident model weights
			trial_w = np.repeat(windows[:1], probe, axis=0)
			try:
				if member is None:
					self._encode_np_batch(trial_w)
				else:
					self._encode_readouts(trial_w, np.repeat(member[:1], probe, axis=0))
				break
			except _OOMError:
				torch.cuda.empty_cache()
				if probe == 1:
					raise
				probe = max(1, probe // 2)
		peak = torch.cuda.max_memory_allocated(self.device)

		per_window = max(1.0, (peak - base) / probe)
		free, _ = torch.cuda.mem_get_info(self.device)
		reusable = torch.cuda.memory_reserved(self.device) - torch.cuda.memory_allocated(self.device)
		batch = int(_SAFETY_FRACTION * (free + reusable) / per_window)
		batch = max(1, min(_MAX_BATCH, pixel_cap, batch))
		if batch >= 16:
			batch -= batch % 8                   # tensor-core friendly
		torch.cuda.empty_cache()
		return batch

	# ------------------------------------------------------------------- public

	def extract_spot_features(
		self,
		image: np.ndarray,
		centres0: np.ndarray,
		geom,
		level,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		step_reporter=None,
	) -> np.ndarray:
		'''
		One footprint-restricted CLS embedding per anchor spot.

		Parameters
		----------
		image : np.ndarray
			The encoded pyramid level, ``(H, W, C)`` float32 in [0, 1].
		centres0 : np.ndarray
			``(N, 2)`` anchor centres in level-0 image pixels.
		geom : AnchorGeometry
			The anchor footprints in the level-0 image frame.
		level : LevelGeometry
			Which level ``image`` is, and its scale relative to level 0.
		background_color : SegmentationBackgroundColor
			Fill color of non-tissue pixels; tokens that are at least 99 % this color are
			never read out.

		Returns
		-------
		np.ndarray
			``(N, D)`` float32. Spots that cannot be placed in the image, or whose footprint
			holds only background, keep an all-zero row.
		'''
		img = ensure_hwc3(image)
		n = len(centres0)
		out = np.zeros((n, self.embedding_dim), dtype=np.float32)
		plan = plan_windows(geom, centres0, level, self.grid)
		if plan.truncated and step_reporter:
			step_reporter.warning(
				f"{plan.truncated} footprints are wider than the largest encoder window "
				f"({self.grid.max_side} px): they are read through that window, centred on the spot"
			)
		bg = resolve_bg_color(background_color)
		p = self.grid.patch

		if step_reporter:
			step_reporter.step(2, 3, "Extracting spot embeddings", unit="spot", items_total=n)
		pbar = tqdm.tqdm(total=n, desc="Extracting spot embeddings", unit="spot")
		processed = len(plan.unplaced)
		pbar.update(processed)

		for (h, w), chunks in plan.groups():
			n_seq = self.n_prefix + (h // p) * (w // p)
			first = cut_window_batch(img, np.array([chunks[0].origin]), h, w)
			r_probe = len(chunks[0].spots) + (-(n_seq + len(chunks[0].spots)) % 8)
			batch_size = self._estimate_batch_size(first, np.ones((1, r_probe, n_seq - self.n_prefix), dtype=bool))
			start = 0
			while start < len(chunks):
				batch = chunks[start:start + batch_size]
				start += len(batch)
				windows = cut_window_batch(img, np.array([c.origin for c in batch]), h, w)
				background = token_background_mask(windows, p, bg)

				live, members, spots = [], [], []
				for b, chunk in enumerate(batch):
					member, keep = readout_membership(chunk, plan, geom, level, self.grid, background[b])
					if keep.any():
						live.append(b)
						members.append(member[keep])
						spots.append(chunk.spots[keep])
				if live:
					r = max(len(s) for s in spots)
					r += -(n_seq + r) % 8           # pad so the sequence length is a multiple of 8
					member_arr = np.zeros((len(live), r, n_seq - self.n_prefix), dtype=bool)
					for k, mem in enumerate(members):
						member_arr[k, :len(mem)] = mem
					emb, batch_size = self._forward_with_oom_retry(
						self._encode_readouts, windows[live], member_arr, batch_size,
					)
					for k, s in enumerate(spots):
						out[s] = emb[k, :len(s)]

				step = sum(len(c.spots) for c in batch)
				processed += step
				pbar.update(step)
				if step_reporter:
					step_reporter.update(processed, n)
		pbar.close()

		if self.device.type == "cuda":
			torch.cuda.empty_cache()
		return out

	def extract_grid_features(
		self,
		image: np.ndarray,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		patch_size: int = 224,
		step_reporter=None,
	) -> tuple[np.ndarray, np.ndarray]:
		'''
		Standard CLS embedding of every non-overlapping ``patch_size`` tile of the image foreground.

		Tiles that are at least 99 % background color are dropped.

		Returns
		-------
		patch_embeddings : np.ndarray
			``(M, D)`` float32, one row per foreground tile (``(0, D)`` when there is none).
		center_coordinates : np.ndarray
			``(M, 2)`` tile centres ``(x0 + patch_size // 2, y0 + patch_size // 2)``.
		'''
		img = ensure_hwc3(image)
		top_left, center_coordinates = compute_patch_coordinates(img.shape, patch_size)
		n_patches = top_left.shape[0]
		if n_patches == 0:
			return np.zeros((0, self.embedding_dim), dtype=np.float32), center_coordinates

		bg_color = resolve_bg_color(background_color)
		sample = cut_patch_batch(img, top_left[:1], patch_size)
		batch_size = self._estimate_batch_size(sample, None)

		if step_reporter:
			step_reporter.step(2, 3, "Extracting patch embeddings", unit="patch", items_total=n_patches)
		chunks, fg_chunks = [], []
		pbar = tqdm.tqdm(total=n_patches, desc="Extracting patch embeddings", unit="patch")
		start = 0
		while start < n_patches:
			end = min(start + batch_size, n_patches)
			patches = cut_patch_batch(img, top_left[start:end], patch_size)
			fg_local = np.where(~background_mask(patches, bg_color))[0]
			if fg_local.size > 0:
				emb, batch_size = self._forward_with_oom_retry(self._encode_np_batch, patches[fg_local], None, batch_size)
				chunks.append(emb)
				fg_chunks.append(np.arange(start, end)[fg_local])
			pbar.update(end - start)
			if step_reporter:
				step_reporter.update(end, n_patches)
			start = end
		pbar.close()

		if self.device.type == "cuda":
			torch.cuda.empty_cache()
		if not chunks:
			return np.zeros((0, self.embedding_dim), dtype=np.float32), center_coordinates[:0]
		return np.concatenate(chunks, axis=0), center_coordinates[np.concatenate(fg_chunks)]
