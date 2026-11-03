"""Shared response-header policy for the main and alignment Flask applications."""

from flask import Response


def cors_response(response: Response) -> Response:
    """Apply the existing response headers before returning the HTTP response."""
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
    return response
