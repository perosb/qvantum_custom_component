"""Transport-agnostic errors for the Qvantum client.

Exceptions store an optional HTTP/protocol ``status`` and a message. They
never hold an ``aiohttp`` response object so the library stays free of
session/lifecycle coupling.
"""

from __future__ import annotations


class AuthError(Exception):
    """Authentication failed (login, token refresh, or HTTP 401/403)."""

    def __init__(
        self,
        status: int | None = None,
        message: str = "Authentication failed",
    ) -> None:
        self.status = status
        if status is not None:
            super().__init__(f"{message}: {status}")
        else:
            super().__init__(message)


class ConnectionError(Exception):
    """Transport or remote API failure."""

    def __init__(
        self,
        status: int | None = None,
        message: str = "API request failed",
    ) -> None:
        self.status = status
        if status is not None:
            super().__init__(f"{message}: {status}")
        else:
            super().__init__(message)


class RateLimitError(Exception):
    """Remote API returned HTTP 429."""

    def __init__(
        self,
        status: int | None = None,
        message: str = "Rate limit exceeded",
    ) -> None:
        self.status = status
        if status is not None:
            super().__init__(f"{message}: {status}")
        else:
            super().__init__(message)


# Compatibility aliases used by the Home Assistant integration and tests.
APIAuthError = AuthError
APIConnectionError = ConnectionError
APIRateLimitError = RateLimitError
