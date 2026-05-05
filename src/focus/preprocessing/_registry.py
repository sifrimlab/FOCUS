from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ModalityHandler:
	"""Describes how to construct samples, dataset, and extract settings for a modality."""
	create_samples: Callable[..., list]
	create_dataset: Callable[..., Any]
	extract_settings: Callable[[dict], dict]


_MODALITY_REGISTRY: dict[str, ModalityHandler] = {}


def register_modality(modality_type: str, handler: ModalityHandler) -> None:
	"""Register a modality handler for use by the preprocessing dispatcher."""
	_MODALITY_REGISTRY[modality_type] = handler
