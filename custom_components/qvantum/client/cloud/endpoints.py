"""Qvantum cloud HTTP endpoints and timeouts."""

from __future__ import annotations

import aiohttp

AUTH_URL = "https://identitytoolkit.googleapis.com"
TOKEN_URL = "https://securetoken.googleapis.com"
API_URL = "https://api.qvantum.com"
API_INTERNAL_URL = "https://internal-api.qvantum.com"

FIREBASE_API_KEY = "AIzaSyCLQ22XHjH8LmId-PB1DY8FBsN53rWTpFw"

DEFAULT_TOKEN_BUFFER_SECONDS = 60
DEFAULT_TOKEN_EXPIRY_SECONDS = 3540
METRICS_TIMEOUT_SECONDS = 12
VENTILATION_BOOST_MINUTES = 120

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
