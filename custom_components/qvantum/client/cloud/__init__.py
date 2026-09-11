"""Qvantum cloud HTTP client."""

from . import auth as _auth  # noqa: F401
from .client import QvantumCloudClient
from .endpoints import (
    API_INTERNAL_URL,
    API_URL,
    AUTH_URL,
    FIREBASE_API_KEY,
    TOKEN_URL,
    VENTILATION_BOOST_MINUTES,
)

__all__ = [
    "API_INTERNAL_URL",
    "API_URL",
    "AUTH_URL",
    "FIREBASE_API_KEY",
    "QvantumCloudClient",
    "TOKEN_URL",
    "VENTILATION_BOOST_MINUTES",
]
