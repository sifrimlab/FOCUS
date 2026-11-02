"""Reflection-based string choices used by configuration and GUI declarations."""


class _AbstractEnum:
    """Non-instantiable string-choice namespace with ordered reflection."""

    def __init__(self) -> None:
        """Reject instantiation of a string-choice namespace."""
        raise Exception("Enum Classes can't be concrete!")

    @classmethod
    def list(cls) -> list[str]:
        """Return declared string values in their original declaration order."""
        names = [
            attr
            for attr in vars(cls)
            if not callable(getattr(cls, attr)) and not attr.startswith("__")
        ]
        return [vars(cls)[elem] for elem in names]
