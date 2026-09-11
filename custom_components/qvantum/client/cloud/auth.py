"""Firebase auth used by ``QvantumCloudClient``.

Sign-in and token refresh live on the client (``authenticate`` /
``_refresh_authentication_token``) so the session and token cache stay
together. This module holds the identity-platform URLs imported by tests
and documentation.
"""

from .endpoints import AUTH_URL, FIREBASE_API_KEY, TOKEN_URL

__all__ = ["AUTH_URL", "FIREBASE_API_KEY", "TOKEN_URL"]
