"""Encode microscopy windows on a fixed lattice and pool final-layer tokens per anchor spot."""

from dataclasses import dataclass
from functools import partial

from numpy.typing import NDArray
from focus.registration.batching import Encoded, forward_with_retry

from focus.registration.batching import estimate_batch_size
from timm.models.vision_transformer import VisionTransformer
from focus.spatial.footprint import AnchorGeometry
from typing import cast
from focus.runtime.reporting import StepReporter
import timm
import torch
import huggingface_hub
import tqdm
import numpy as np
import scipy.sparse as sp

from focus.config.options import SegmentationBackgroundColor
from focus.registration.windows import (
    ensure_hwc3,
    compute_patch_coordinates,
    cut_patch_batch,
    cut_window_batch,
    background_mask,
    resolve_bg_color,
    token_background_grid,
)
from focus.registration.readout import (
    LevelGeometry,
    TokenGrid,
    WindowLattice,
    build_lattice,
    footprint_tokens,
    patch_links,
)


@dataclass
class SpotFeatures:
    """Per-spot outputs of :meth:`MicroscopyImageFeatureExtractor.extract_spot_features`."""

    x: NDArray[np.float32]  # (N, D) footprint embeddings; zero rows read nothing
    lattice: WindowLattice  # windows of the encoded image
    patch_embedding: NDArray[np.float32] | None = (
        None  # (N, D) CLS of each spot's patch
    )
    patch_id: NDArray[np.int64] | None = (
        None  # (N,) lattice window of the patch, -1 = none
    )


class MicroscopyImageFeatureExtractor:
    """
    Prov-GigaPath tile encoder read out per anchor spot.

    ``extract_spot_features`` encodes the image with a standard forward pass in encoder-sized
    windows on a fixed lattice (stride half a window; see ``focus.registration.readout``). Each
    token of the image's global token grid is taken from the one window it is most central in,
    and every spot's embedding is the mean of the final-layer tokens its footprint overlaps,
    weighted by that overlap. Optionally each spot also gets the standard CLS embedding of its
    patch, the window that reads the token holding its centre.

    ``extract_grid_features`` is the plain per-tile CLS on a non-overlapping grid (anchorless use).
    """

    MODEL_ID = "prov-gigapath/prov-gigapath"

    def __init__(
        self,
        path: str,
        hf_token: str | None = None,
        model: VisionTransformer | None = None,
    ) -> None:
        """Initialize the tile model on the selected device and verify its token layout.

        Parameters
        ----------
        path : str
            Dataset root retained for the encoder instance.
        hf_token : str, optional
            HuggingFace login token used only when no model is supplied.
        model : VisionTransformer, optional
            Existing model, used in place. Otherwise download the original pretrained model.

        Raises
        ------
        RuntimeError
            If the model is not a class-token ViT or its token layout is not the expected grid.

        Notes
        -----
        Selects CUDA when available, sets evaluation mode and moves model parameters
        and normalization tensors to that device. Does not change the global RNG state."""
        self.source_path = path
        self.hf_token = hf_token
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        if model is None:
            huggingface_hub.login(token=self.hf_token)
            # dynamic_img_size: windows of an image smaller than 224 px are still encoded.
            model = cast(
                VisionTransformer,
                timm.create_model(
                    f"hf_hub:{self.MODEL_ID}", pretrained=True, dynamic_img_size=True
                ),
            )
        self.patch_encoder = model
        self.patch_encoder.eval()
        self.patch_encoder.to(self.device)

        # ImageNet normalization constants for this pretrained model, kept resident
        # on the device as broadcastable (1, 3, 1, 1) tensors and applied per batch.
        self._mean = (
            torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1).to(self.device)
        )
        self._std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1).to(self.device)

        m = self.patch_encoder
        if not (
            getattr(m, "has_class_token", False)
            and m.global_pool == "token"
            and getattr(m, "attn_pool", None) is None
        ):
            raise RuntimeError(
                "The encoder must be a ViT with a class token and token pooling"
            )
        self.grid = TokenGrid(
            patch=int(m.patch_embed.patch_size[0]),
            base_side=int(m.patch_embed.img_size[0]),
        )
        self.n_prefix = int(m.num_prefix_tokens)
        self.embedding_dim = int(m.num_features)
        self._verify_tokens()

    # ------------------------------------------------------------------ encoding

    def _to_device(self, patch_batch: NDArray[np.float32]) -> torch.Tensor:
        """(B, H, W, 3) float32 [0, 1] host batch -> normalised (B, 3, H, W) device tensor."""
        # .contiguous() so the host buffer can be pinned.
        x = torch.from_numpy(patch_batch).permute(0, 3, 1, 2).contiguous()
        if self.device.type == "cuda":
            x = x.pin_memory()
        x = x.to(self.device, non_blocking=True)
        return (x - self._mean) / self._std

    def _encode_np_batch(self, patch_batch: NDArray[np.float32]) -> NDArray[np.float32]:
        """Standard CLS embedding of each window of a ``(B, H, W, 3)`` batch, on the CPU."""
        x = self._to_device(patch_batch)
        with torch.inference_mode():
            emb = self.patch_encoder(x)
        return cast(np.ndarray, emb.float().cpu().numpy())

    def _encode_windows(
        self, windows: NDArray[np.float32], with_cls: bool = True
    ) -> Encoded:
        """
        Final-layer patch tokens ``(B, T, D)`` of each window, row-major, and, with ``with_cls``,
        the standard CLS embedding ``(B, D)`` (identical to the model's own forward).
        """
        m = self.patch_encoder
        x = self._to_device(windows)
        with torch.inference_mode():
            feats = m.forward_features(x)
            tokens = feats[:, self.n_prefix :].float().cpu().numpy()
            if not with_cls:
                return (tokens,)
            cls = m.forward_head(feats).float().cpu().numpy()
        return tokens, cls

    def _verify_tokens(self) -> None:
        """The encoder must return one final-layer token per patch of a training-size window."""
        side = self.grid.base_side
        gen = torch.Generator().manual_seed(0)
        x = torch.rand((1, side, side, 3), generator=gen).numpy()
        tokens, _ = self._encode_windows(x)
        expected = (side // self.grid.patch) ** 2
        if tokens.shape[1] != expected:
            raise RuntimeError(
                f"The encoder returned {tokens.shape[1]} patch tokens for a {side} px window; "
                f"expected {expected} ({side // self.grid.patch} x {side // self.grid.patch})"
            )

    # ------------------------------------------------------------------- public

    def extract_spot_features(
        self,
        image: NDArray[np.float32],
        centres0: NDArray[np.float32 | np.float64],
        geom: AnchorGeometry,
        level: LevelGeometry,
        background_color: str = SegmentationBackgroundColor.WHITE,
        patch_embeddings: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> SpotFeatures:
        """
        One footprint embedding per anchor spot and, optionally, the embedding of its patch.

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
                never read.
        patch_embeddings : bool
                Also return, per spot, the standard CLS embedding of its patch.

        Returns
        -------
        SpotFeatures
                ``x`` is ``(N, D)`` float32: the mean of the final-layer tokens each footprint
                overlaps, weighted by the overlap. Spots that cannot be placed in the image, or
                whose footprint holds only background, keep an all-zero row, and only they have
                ``patch_id == -1`` (with an all-zero ``patch_embedding`` row).
        """
        img = ensure_hwc3(image)
        n = len(centres0)
        dim = self.embedding_dim
        x_out = np.zeros((n, dim), dtype=np.float32)
        lattice = build_lattice(img.shape[:2], self.grid)
        p = self.grid.patch
        token_bg = token_background_grid(img, p, resolve_bg_color(background_color))
        tokens = footprint_tokens(geom, centres0, level, lattice, token_bg)
        live = tokens.total > 0

        patch = (
            patch_links(centres0, level, lattice, live) if patch_embeddings else None
        )
        needed = np.unique(
            tokens.window if patch is None else np.r_[tokens.window, patch[patch >= 0]]
        )
        patch_cls = (
            None if patch is None else np.zeros((len(needed), dim), dtype=np.float32)
        )

        if step_reporter:
            step_reporter.step(
                2,
                3,
                "Extracting spot embeddings",
                unit="window",
                items_total=len(needed),
            )
        if needed.size:
            h, w = lattice.window_shape
            n_tok = lattice.tokens_per_window
            encode = partial(self._encode_windows, with_cls=patch_embeddings)
            first = cut_window_batch(img, lattice.origin(needed[:1]), h, w)
            batch_size = estimate_batch_size(self.device, first, encode)
            # Each token's weight, normalised by its spot's total, so the sums are the means.
            share = (tokens.weight / tokens.total[tokens.spot]).astype(np.float32)
            pbar = tqdm.tqdm(
                total=len(needed), desc="Extracting spot embeddings", unit="window"
            )
            start = 0
            while start < len(needed):
                batch = needed[start : start + batch_size]
                windows = cut_window_batch(img, lattice.origin(batch), h, w)
                out, batch_size = forward_with_retry(
                    self.device, encode, windows, batch_size
                )
                e0 = np.searchsorted(tokens.window, batch[0], side="left")
                e1 = np.searchsorted(tokens.window, batch[-1], side="right")
                if e1 > e0:
                    pos = np.searchsorted(batch, tokens.window[e0:e1])
                    spots, inverse = np.unique(tokens.spot[e0:e1], return_inverse=True)
                    pool = sp.csr_matrix(
                        (
                            share[e0:e1],
                            (inverse.ravel(), pos * n_tok + tokens.local[e0:e1]),
                        ),
                        shape=(len(spots), len(batch) * n_tok),
                    )
                    x_out[spots] += pool @ out[0].reshape(len(batch) * n_tok, dim)
                if patch_cls is not None:
                    patch_cls[start : start + len(batch)] = out[1]
                start += len(batch)
                pbar.update(len(batch))
                if step_reporter:
                    step_reporter.update(start, len(needed))
            pbar.close()

        if self.device.type == "cuda":
            torch.cuda.empty_cache()
        features = SpotFeatures(x_out, lattice)
        if patch is not None and patch_cls is not None:
            features.patch_id = patch
            features.patch_embedding = np.zeros((n, dim), dtype=np.float32)
            linked = patch >= 0
            features.patch_embedding[linked] = patch_cls[
                np.searchsorted(needed, patch[linked])
            ]
        return features

    def extract_grid_features(
        self,
        image: NDArray[np.float32],
        background_color: str = SegmentationBackgroundColor.WHITE,
        patch_size: int = 224,
        step_reporter: StepReporter | None = None,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
        """
        Standard CLS embedding of every non-overlapping ``patch_size`` tile of the image foreground.

        Tiles that are at least 99 % background color are dropped.

        Returns
        -------
        patch_embeddings : np.ndarray
                ``(M, D)`` float32, one row per foreground tile (``(0, D)`` when there is none).
        center_coordinates : np.ndarray
                ``(M, 2)`` tile centres ``(x0 + patch_size // 2, y0 + patch_size // 2)``.
        """
        img = ensure_hwc3(image)
        top_left, center_coordinates = compute_patch_coordinates(img.shape, patch_size)
        n_patches = top_left.shape[0]
        if n_patches == 0:
            return np.zeros(
                (0, self.embedding_dim), dtype=np.float32
            ), center_coordinates

        def encode(windows: NDArray[np.float32]) -> Encoded:
            """Plain CLS of each tile, as a one-element output tuple."""
            return (self._encode_np_batch(windows),)

        bg_color = resolve_bg_color(background_color)
        sample = cut_patch_batch(img, top_left[:1], patch_size)
        batch_size = estimate_batch_size(self.device, sample, encode)

        if step_reporter:
            step_reporter.step(
                2, 3, "Extracting patch embeddings", unit="patch", items_total=n_patches
            )
        chunks, fg_chunks = [], []
        pbar = tqdm.tqdm(
            total=n_patches, desc="Extracting patch embeddings", unit="patch"
        )
        start = 0
        while start < n_patches:
            end = min(start + batch_size, n_patches)
            patches = cut_patch_batch(img, top_left[start:end], patch_size)
            fg_local = np.where(~background_mask(patches, bg_color))[0]
            if fg_local.size > 0:
                out, batch_size = forward_with_retry(
                    self.device, encode, patches[fg_local], batch_size
                )
                chunks.append(out[0])
                fg_chunks.append(np.arange(start, end)[fg_local])
            pbar.update(end - start)
            if step_reporter:
                step_reporter.update(end, n_patches)
            start = end
        pbar.close()

        if self.device.type == "cuda":
            torch.cuda.empty_cache()
        if not chunks:
            return np.zeros(
                (0, self.embedding_dim), dtype=np.float32
            ), center_coordinates[:0]
        return np.concatenate(chunks, axis=0), center_coordinates[
            np.concatenate(fg_chunks)
        ]
