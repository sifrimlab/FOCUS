"""Numeric assertion preserving the original suite's decimal comparison semantics."""


def assert_close(
    first: float,
    second: float,
    places: int = 7,
    msg: str | None = None,
    delta: float | None = None,
) -> None:
    """Compare numbers using the original decimal rounding or absolute delta.

    Parameters
    ----------
    first, second
        Values under comparison, evaluated once by the caller.
    places
        Decimal places used when ``delta`` is absent.
    msg
        Optional assertion message.
    delta
        Maximum absolute difference, overriding ``places``.
    """
    if first == second:
        return
    difference = abs(first - second)
    if delta is not None:
        assert difference <= delta, msg
    else:
        assert round(difference, places) == 0, msg
