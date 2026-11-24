"""Transport-agnostic errors for the Qvantum client.

Exceptions store an optional HTTP/protocol ``status`` and a message. They
never hold an ``aiohttp`` response object so the library stays free of
session/lifecycle coupling.
"""

from __future__ import annotations


class QvantumError(Exception):
    """Base for everything the client raises.

    ``except QvantumError`` catches the client's whole vocabulary without
    catching the interpreter's, and the status/message formatting lives here so
    a subclass only has to name its default message.
    """

    default_message = "Qvantum client error"

    def __init__(
        self,
        status: int | None = None,
        message: str | None = None,
    ) -> None:
        self.status = status
        self.message = message if message is not None else self.default_message
        if status is not None:
            super().__init__(f"{self.message}: {status}")
        else:
            super().__init__(self.message)


class AuthError(QvantumError):
    """Authentication failed (login, token refresh, or HTTP 401/403)."""

    default_message = "Authentication failed"


class TransportError(QvantumError):
    """Transport or remote API failure."""

    default_message = "API request failed"


class RateLimitError(QvantumError):
    """Remote API returned HTTP 429."""

    default_message = "Rate limit exceeded"

    def __init__(
        self,
        status: int | None = None,
        message: str | None = None,
        *,
        retry_after: float | None = None,
    ) -> None:
        # Seconds to wait before retrying, parsed from a ``Retry-After``
        # header when the server supplied one. None when unknown.
        self.retry_after = retry_after
        super().__init__(status, message)


# Compatibility aliases used by the Home Assistant integration and tests.
APIAuthError = AuthError
APIConnectionError = TransportError
APIRateLimitError = RateLimitError
