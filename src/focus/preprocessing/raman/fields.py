"""Reusable validation and storage for the existing Raman metadata fields."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar, cast, overload

import numpy as np

ValueT = TypeVar("ValueT")


class MetadataField(Generic[ValueT]):
    """Validate a field on assignment and retain its original backing attribute."""

    def __init__(self, validate: Callable[[object], ValueT]) -> None:
        """Retain the field validator; the class declaration later assigns its storage name.

        Parameters
        ----------
        validate : Callable[[object], ValueT]
            Original validation/coercion operation, called before every storage update."""
        self.validate = validate
        self.storage = ""

    def __set_name__(self, owner: type[object], name: str) -> None:
        """Use the same underscore-prefixed storage as the original properties."""
        self.storage = "_" + name

    @overload
    def __get__(
        self, instance: None, owner: type[object] | None = None
    ) -> MetadataField[ValueT]:
        """Return the descriptor when accessed through the metadata class."""
        ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> ValueT | None:
        """Return the instance field value without changing its validation state."""
        ...

    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> MetadataField[ValueT] | ValueT | None:
        """Return the stored value, including the original unset ``None`` default."""
        if instance is None:
            return self
        return cast(ValueT | None, getattr(instance, self.storage))

    def __set__(self, instance: object, value: ValueT) -> None:
        """Validate before updating storage, preserving a previous value on failure."""
        setattr(instance, self.storage, self.validate(value))


def text(value: object, label: str) -> str:
    """Accept strings with the field's original type-error wording."""
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    return value


def integer(
    value: object, label: str, minimum: int, requirement: str, coerce: bool = False
) -> int:
    """Validate an integer while retaining field-specific bool/coercion behavior."""
    if not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value < minimum:
        raise ValueError(f"{label} must be {requirement}.")
    return int(value) if coerce else value


def positive_float(value: object, label: str) -> float:
    """Apply the original positive-number check and float conversion."""
    if not isinstance(value, (float, int)):
        raise TypeError(f"{label} must be a float or an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be a positive number.")
    return float(value)


def array(
    value: object, label: str, ndim: int, axis: int, description: str
) -> np.ndarray:
    """Validate an array shape and make the same float32 copy as the original setter."""
    if not isinstance(value, np.ndarray):
        raise TypeError(f"{label} must be a numpy array.")
    if value.ndim != ndim or value.shape[axis] != 2:
        raise ValueError(f"{label} must be {description}.")
    return value.astype(np.float32)
