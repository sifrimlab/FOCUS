"""Synthetic data and shared setup for encoding scenarios."""

from typing import cast
from types import ModuleType


import numpy as np

from focus.spatial.footprint import AnchorGeometry

try:
    import torch as _torch

    torch: ModuleType | None = _torch
    from timm.models.vision_transformer import VisionTransformer
    from focus.registration.encoder import (
        MicroscopyImageFeatureExtractor as MicroscopyImageFeatureExtractor,
    )
except ImportError:  # the encoder stack is optional for the rest of the suite
    torch = None


def tiny_vit(img_size: int = 64) -> VisionTransformer:
    """Seed Torch with zero and return a two-layer, 32-feature ViT for the requested image side."""
    _torch.manual_seed(0)
    model = VisionTransformer(
        img_size=img_size,
        patch_size=16,
        embed_dim=32,
        depth=2,
        num_heads=2,
        num_classes=0,
        dynamic_img_size=True,
    )
    with _torch.no_grad():
        cast(_torch.Tensor, model.cls_token).normal_(std=1.0)
        cast(_torch.Tensor, model.pos_embed).normal_(std=1.0)
    return model


def lattice_geometry(
    n: int,
    pitch_um: float,
    spot_um: float,
    um_per_px: float,
    offset_px: np.ndarray | tuple[float, float] | list[float],
) -> tuple[np.ndarray, np.ndarray, AnchorGeometry]:
    """Return native µm coordinates, mapped image pixels and square footprints at the supplied scale."""
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    spatial = np.column_stack([i.ravel(), j.ravel()]).astype(np.float64) * pitch_um
    mapped = spatial / um_per_px + offset_px
    return (
        spatial,
        mapped,
        AnchorGeometry.from_arrays(spatial, mapped, np.array([spot_um, spot_um])),
    )
