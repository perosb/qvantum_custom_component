"""Qvantum API."""

import aiohttp
import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
import logging
import struct
from typing import Any, Optional

from pymodbus.client.tcp import AsyncModbusTcpClient
from pymodbus.pdu.register_message import (
    ReadHoldingRegistersRequest,
    ReadInputRegistersRequest,
)
from pymodbus.exceptions import ModbusException

from .const import (
    FAN_SPEED_STATE_EXTRA,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_OFF,
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    TAP_WATER_CAPACITY_MAPPINGS,
    MODBUS_INPUT_REGISTER_MAP,
    MODBUS_HOLDING_REGISTER_MAP,
    RELAY_BIT_MAP,
    MODBUS_SPEC_TO_INTERNAL_MAP,
    MODBUS_INTERNAL_TO_SPEC_MAP,
    MODBUS_INPUT_TO_HTTP_MAP,
    MODBUS_HOLDING_TO_SETTINGS_MAP,
    RELAY_STAGE_POWER_MAP,
    BASE_SYSTEM_POWER_W,
)


_LOGGER = logging.getLogger(__name__)

# API Configuration
AUTH_URL = "https://identitytoolkit.googleapis.com"
TOKEN_URL = "https://securetoken.googleapis.com"
API_URL = "https://api.qvantum.com"
API_INTERNAL_URL = "https://internal-api.qvantum.com"

# Timeouts and buffers
DEFAULT_TOKEN_BUFFER_SECONDS = 60
DEFAULT_TOKEN_EXPIRY_SECONDS = 3540
METRICS_TIMEOUT_SECONDS = 12
VENTILATION_BOOST_MINUTES = 120

# Firebase API Key (consider moving to config if needed)
FIREBASE_API_KEY = "AIzaSyCLQ22XHjH8LmId-PB1DY8FBsN53rWTpFw"


class QvantumAPI:
    """Class for Qvantum API."""

    def __init__(
        self,
        username: str,
        password: str,
        user_agent: str,
        session: Optional[aiohttp.ClientSession] = None,
        modbus_tcp: bool = False,
        modbus_host: str = "qvantum-hp",
        modbus_port: int = 502,
        modbus_unit_id: int = 1,
    ) -> None:
        """Initialise."""
        self._auth_url = AUTH_URL
        self._token_url = TOKEN_URL
        self._api_url = API_URL
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self.hass = None
        self._modbus_tcp = modbus_tcp
        self._modbus_host = modbus_host
        self._modbus_port = modbus_port
        self._modbus_unit_id = modbus_unit_id
        self._modbus_client = None
        self._modbus_lock = asyncio.Lock()
        # Accept an optional aiohttp session for easier testing. If not provided,
        # create one and mark it as owned so we can close it when `close()` is called.
        if session is not None:
            self._session = session
            self._session_owner = False
        else:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                }
            )
            self._session_owner = True
        self._reset_state()

    def _reset_state(self):
        """Reset authentication and cached API data."""
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data = {}
        self._settings_etag = None
        self._metrics_data = {}
        self._metrics_etag = None
        self._device_metadata = {}
        self._device_metadata_etag = None

    def _init_modbus_client(self):
        """Initialize Modbus TCP client if not already done."""
        if self._modbus_tcp and self._modbus_client is None:
            self._modbus_client = AsyncModbusTcpClient(
                host=self._modbus_host,
                port=self._modbus_port,
                timeout=10.0,  # Increased timeout
                retries=3,
            )

    async def _reset_modbus_client(self):
        """Close and clear Modbus client so reconnect starts from scratch."""
        if self._modbus_client:
            try:
                close_result = self._modbus_client.close()
                if inspect.isawaitable(close_result):
                    await close_result
            except Exception as exc:
                _LOGGER.debug("Error closing modbus client during reset: %s", exc)
            self._modbus_client = None

    async def close(self):
        """Close the session."""
        # Only close the session if we created it; externally-provided sessions
        # should be closed by their owner.
        if getattr(self, "_session_owner", False) and self._session:
            await self._session.close()
            self._session = None
        if self._modbus_client:
            close_result = self._modbus_client.close()
            if inspect.isawaitable(close_result):
                await close_result
            self._modbus_client = None

    def _normalize_modbus_value(self, value: float, scale: float) -> int | float:
        """Normalize a Modbus register value to the correct type and precision."""
        if scale == 1.0:
            return int(value)
        return round(value, 2)

    async def _read_modbus_registers(
        self,
        device_id: str,
        enabled_items: list[str],
        register_map: dict,
        use_input_registers: bool = True,
        handle_relay_bits: bool = False,
    ):
        """Generic method to read Modbus registers (input or holding)."""
        async with self._modbus_lock:
            self._init_modbus_client()
            if not self._modbus_client:
                raise APIConnectionError(None, "Modbus client not initialized")

            data = {}
            data["hpid"] = device_id

            try:
                if not self._modbus_client.connected:
                    await self._modbus_client.connect()
                    if not self._modbus_client.connected:
                        raise APIConnectionError(None, "Modbus client connection failed")

                # Collect all registers we need to read
                registers_to_read = {}
                relay_items = [] if handle_relay_bits else None

                for item_name in enabled_items:
                    if handle_relay_bits and item_name in RELAY_BIT_MAP:
                        relay_items.append(item_name)
                    elif item_name in register_map:
                        addr, data_type, scale = register_map[item_name]
                        if data_type == "float32":
                            # Float32 needs 2 registers
                            registers_to_read[addr] = (item_name, data_type, scale, 2)
                            registers_to_read[addr + 1] = (item_name, data_type, scale, 2)
                        else:
                            # Single register
                            registers_to_read[addr] = (item_name, data_type, scale, 1)

                # Group registers into contiguous blocks for efficient reading
                if registers_to_read:
                    sorted_addresses = sorted(registers_to_read.keys())
                    blocks = []
                    current_block_start = sorted_addresses[0]
                    current_block_end = sorted_addresses[0]

                    for addr in sorted_addresses[1:]:
                        if addr == current_block_end + 1:
                            current_block_end = addr
                        else:
                            blocks.append((current_block_start, current_block_end))
                            current_block_start = addr
                            current_block_end = addr
                    blocks.append((current_block_start, current_block_end))

                    # Read each block
                    for block_start, block_end in blocks:
                        count = block_end - block_start + 1
                        if use_input_registers:
                            request = ReadInputRegistersRequest(
                                dev_id=self._modbus_unit_id,
                                address=block_start,
                                count=count,
                            )
                        else:
                            request = ReadHoldingRegistersRequest(
                                dev_id=self._modbus_unit_id,
                                address=block_start,
                                count=count,
                            )
                        result = await self._modbus_client.execute(False, request)
                        if result is None:
                            raise APIConnectionError(
                                None,
                                "Modbus operation failed: no response received from device",
                            )
                        if result.isError():
                            register_type = "input" if use_input_registers else "holding"
                            _LOGGER.warning(
                                f"Failed to read {register_type} register block {block_start}-{block_end}"
                            )
                            continue

                        # Parse the block results
                        for i, addr in enumerate(range(block_start, block_end + 1)):
                            if addr in registers_to_read:
                                item_name, data_type, scale, reg_count = registers_to_read[
                                    addr
                                ]
                                if item_name in data:
                                    continue  # Already processed this item

                                if data_type == "float32":
                                    # Need both registers for float32
                                    if addr + 1 in registers_to_read and addr + 1 in range(
                                        block_start, block_end + 1
                                    ):
                                        reg1 = result.registers[i]
                                        reg2 = result.registers[i + 1]
                                        # Convert two 16-bit registers to 32-bit float (big-endian)
                                        raw_bytes = struct.pack(">HH", reg1, reg2)
                                        value = struct.unpack(">f", raw_bytes)[0] * scale

                                        data[item_name] = self._normalize_modbus_value(
                                            value, scale
                                        )
                                elif data_type in ("int16", "uint16"):
                                    value = result.registers[i]
                                    if data_type == "int16":
                                        if value > 32767:
                                            value -= 65536
                                    value *= scale

                                    data[item_name] = self._normalize_modbus_value(
                                        value, scale
                                    )

                # Handle relay bit extraction from relays_bitmask
                if handle_relay_bits and relay_items:
                    bitmask_addr = register_map[
                        "relays (l1, l2, l3, gp10, qm10, qn8_1, qn8_2, gp3, pump, ha12)"
                    ][0]
                    request = ReadInputRegistersRequest(
                        dev_id=self._modbus_unit_id, address=bitmask_addr, count=1
                    )
                    result = await self._modbus_client.execute(False, request)
                    if result is None:
                        raise APIConnectionError(
                            None,
                            "Modbus operation failed: no response received from device",
                        )
                    if result.isError():
                        _LOGGER.warning(
                            f"Failed to read relays bitmask from register {bitmask_addr}"
                        )
                    else:
                        bitmask = result.registers[0]
                        for item_name in relay_items:
                            bit = RELAY_BIT_MAP[item_name]
                            value = (bitmask >> bit) & 1
                            data[item_name] = value

            except ModbusException as e:
                error_msg = f"Modbus error reading {'input' if use_input_registers else 'holding'} registers: {e}"
                _LOGGER.error(error_msg)
                await self._reset_modbus_client()
                raise APIConnectionError(None, f"Modbus communication failed: {e}")
            except Exception as e:
                # Catch non-Modbus exceptions (e.g. NoneType on execute) and recover gracefully.
                _LOGGER.error(
                    "Unexpected error reading %s registers: %s",
                    'input' if use_input_registers else 'holding',
                    e,
                    exc_info=True,
                )
                await self._reset_modbus_client()
                raise APIConnectionError(None, f"Modbus communication failed: {e}")

            # Keep Modbus client open for subsequent reads to reuse the connection.
            return data

    async def _read_modbus_metrics(self, device_id: str, enabled_metrics: list[str]):
        """Read metrics from Modbus TCP."""
        # Convert enabled metrics to original Modbus names
        enabled_metrics_original = [
            MODBUS_INTERNAL_TO_SPEC_MAP.get(m, m) for m in enabled_metrics
        ]

        metrics = await self._read_modbus_registers(
            device_id=device_id,
            enabled_items=enabled_metrics_original,
            register_map=MODBUS_INPUT_REGISTER_MAP,
            use_input_registers=True,
            handle_relay_bits=True,
        )

        # Map keys back to our internal names
        metrics = {MODBUS_SPEC_TO_INTERNAL_MAP.get(k, k): v for k, v in metrics.items()}

        _LOGGER.debug("Raw Modbus metrics read: %s", sorted(metrics.items()))

        # Derive use_adaptive from smart control modes if both are available
        if "smart_dhw_mode" in metrics:
            _LOGGER.debug(
                "Deriving smart_sh_mode and use_adaptive from smart_dhw_mode: %s",
                metrics["smart_dhw_mode"],
            )
            # Both HTTP and Modbus use -1 to represent "smart control disabled".
            # Any non-negative value indicates a specific smart/adaptive mode.
            # Modbus specification is inconsistent here, so we standardize on -1 for disabled and 0/1/2 for modes.
            # Mirror smart_dhw_mode into smart_sh_mode and derive use_adaptive from it.
            metrics["smart_sh_mode"] = metrics["smart_dhw_mode"]
            metrics["use_adaptive"] = metrics["smart_dhw_mode"] != -1

        # Provide a combined power metric using compressor power and the three relay heat stages.
        # Relay stage values are boolean (0/1), with each stage representing fixed wattage.
        # power values are configured in const.py for maintainability and future model-specific changes.
        stage_power_map = RELAY_STAGE_POWER_MAP
        relay_sum = 0.0
        for key, wattage in stage_power_map.items():
            value = metrics.get(key, 0)
            try:
                relay_sum += float(value) * wattage
            except (TypeError, ValueError):
                continue

        compressor_power = float(metrics.get("compressor_power", 0.0))
        metrics.pop("compressor_power", None)

        # Add fixed base power overhead from system baseline consumption.
        # This includes circulation pumps, controls and other auxiliary constant loads.
        metrics["powertotal"] = round(
            BASE_SYSTEM_POWER_W + relay_sum + compressor_power, 2
        )

        # Compute energy from mwh/kwh components (preferred modbus format: kwh entry scaled x10).
        for prefix in ["compressor", "additional", "heating", "cooling", "dhw"]:
            mwh = metrics.get(f"{prefix}_mwh")
            kwh = metrics.get(f"{prefix}_kwh")
            mwh = float(mwh) if mwh is not None else 0.0
            kwh = float(kwh) if kwh is not None else 0.0

            # NOTE: kwh values in `metrics` are already normalized by the Modbus register map
            # (e.g. raw x10 register values have been de-scaled), so we can add them directly
            # to mwh * 1000.0 here without applying any additional scaling factor.
            metrics[f"{prefix}energy"] = round(mwh * 1000.0 + kwh, 2)
            metrics.pop(f"{prefix}_mwh", None)
            metrics.pop(f"{prefix}_kwh", None)

            _LOGGER.debug(
                "Computed energy for %s: mwh=%s, kwh=%s, total energy=%s",
                prefix,
                mwh,
                kwh,
                metrics.get(f"{prefix}energy"),
            )

        # Convert internal metric names to HTTP alias names using canonical mapping.
        normalized_metrics = dict(metrics)
        for internal_key, value in metrics.items():
            if value is None:
                continue
            http_key = MODBUS_INPUT_TO_HTTP_MAP.get(internal_key)
            if http_key and normalized_metrics.get(http_key) is None:
                normalized_metrics[http_key] = value
                normalized_metrics.pop(internal_key, None)

        return {"metrics": normalized_metrics}

    async def _read_modbus_settings(self, device_id: str, enabled_settings: list[str]):
        """Read settings from Modbus TCP holding registers."""
        settings = await self._read_modbus_registers(
            device_id=device_id,
            enabled_items=enabled_settings,
            register_map=MODBUS_HOLDING_REGISTER_MAP,
            use_input_registers=False,
            handle_relay_bits=False,
        )

        # Convert to the same format as HTTP API and include original modbus keys for compatibility.
        settings_dict: dict[str, Any] = {}
        for k, v in settings.items():
            if k == "hpid":
                continue
            settings_dict[k] = v
            http_key = MODBUS_HOLDING_TO_SETTINGS_MAP.get(k)
            if http_key and http_key != k:
                settings_dict[http_key] = v

        if "extra_tap_water" in settings_dict:
            settings_dict["extra_tap_water"] = (
                # If the value is 2, it means "on" (max tap water), otherwise "off" (normal tap water)
                "on" if settings_dict["extra_tap_water"] == 2 else "off"
            )

        if "fanspeedselector" in settings_dict:
            match settings_dict["fanspeedselector"]:
                case 0:
                    settings_dict["fanspeedselector"] = FAN_SPEED_STATE_OFF
                case 1:
                    settings_dict["fanspeedselector"] = FAN_SPEED_STATE_NORMAL
                case 2:
                    settings_dict["fanspeedselector"] = FAN_SPEED_STATE_EXTRA

        # hide internal dhw_* keys from public settings output
        for hide_key in ["dhw_start_normal", "dhw_stop_normal"]:
            settings_dict.pop(hide_key, None)

        return {"settings": [{"name": n, "value": v} for n, v in settings_dict.items()]}

    async def _handle_response(self, response: aiohttp.ClientResponse):
        """Handle API response, raising exceptions for errors."""
        if not response.ok:
            if response.status == 401:
                raise APIAuthError(response)
            elif response.status == 429:
                raise APIRateLimitError(response)
            else:
                raise APIConnectionError(response)

    async def unauthenticate(self):
        """Unauthenticate from the API."""
        self._reset_state()

    async def _request_json(
        self,
        method: str,
        url: str,
        payload: Optional[dict] = None,
        validate_status: bool = False,
    ) -> dict[str, Any]:
        """Send an authenticated request and return parsed JSON response.

        By default, this helper does not validate HTTP status codes and will try
        to parse JSON regardless of response status. Set ``validate_status=True``
        to enforce ``_handle_response`` before reading the response body.
        """
        await self._ensure_valid_token()
        request = getattr(self._session, method)
        kwargs: dict[str, Any] = {"headers": self._request_headers()}
        if payload is not None:
            kwargs["json"] = payload

        async with request(url, **kwargs) as response:
            if validate_status:
                await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)
            return data

    async def authenticate(self):
        """Authenticate with the API using username and password to retrieve a token."""
        payload = {
            "returnSecureToken": "true",
            "email": self._username,
            "password": self._password,
            "clientType": "CLIENT_TYPE_WEB",
        }

        async with self._session.post(
            f"{self._auth_url}/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}",
            json=payload,
        ) as response:
            match response.status:
                case 200:
                    _LOGGER.debug("Authentication successful: %s", response.status)
                    auth_data = await response.json()
                    self._token = auth_data.get("idToken")
                    self._refreshtoken = auth_data.get("refreshToken")
                    expires_in = auth_data.get(
                        "expiresIn", DEFAULT_TOKEN_EXPIRY_SECONDS
                    )
                    self._token_expiry = datetime.now() + timedelta(
                        seconds=int(expires_in) - DEFAULT_TOKEN_BUFFER_SECONDS
                    )
                    return True
                case _:
                    _LOGGER.error("Authentication failed: %s", response.status)
                    raise APIAuthError(response)

    async def _refresh_authentication_token(self):
        """Refresh the authentication token."""

        if not self._refreshtoken:
            return

        payload = {"grant_type": "refresh_token", "refresh_token": self._refreshtoken}

        self._token = None

        async with self._session.post(
            f"{self._token_url}/v1/token?key={FIREBASE_API_KEY}",
            json=payload,
        ) as response:
            match response.status:
                case 200:
                    _LOGGER.debug("Token refreshed successfully: %s", response.status)
                    auth_data = await response.json()
                    self._token = auth_data.get("access_token")
                    self._refreshtoken = auth_data.get("refresh_token")
                    expires_in = auth_data.get(
                        "expires_in", DEFAULT_TOKEN_EXPIRY_SECONDS
                    )
                    self._token_expiry = datetime.now() + timedelta(
                        seconds=int(expires_in) - DEFAULT_TOKEN_BUFFER_SECONDS
                    )
                case _:
                    _LOGGER.error("Token refresh failed: %s", response.status)
                    # Don't raise exception here, let _ensure_valid_token handle it

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
        if not self._token or datetime.now() >= self._token_expiry:
            try:
                await self._refresh_authentication_token()
                if not self._token:
                    await self.authenticate()
                    if not self._token:
                        raise APIAuthError(
                            None, "Failed to obtain authentication token"
                        )
            except APIAuthError:
                # If refresh fails, try fresh authentication
                await self.authenticate()
                if not self._token:
                    raise APIAuthError(None, "Failed to obtain authentication token")

    def _request_headers(self):
        """Get request headers for API calls."""
        return {
            "Authorization": f"Bearer {self._token}",
        }

    async def update_setting(self, device_id: str, name: str, value: any):
        """Update one setting."""

        payload = {"update_settings": {name: value}}

        return await self._send_command(device_id, payload)

    async def update_settings(self, device_id: str, settings: dict):
        """Update multiple settings from a dictionary."""

        payload = {"update_settings": settings}

        return await self._send_command(device_id, payload)

    async def _update_settings(self, device_id: str, payload: dict):
        """Update one or several settings."""

        _LOGGER.debug(json.dumps(payload))
        return await self._request_json(
            "patch",
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings?dispatch=false",
            payload,
        )

    async def _send_command(self, device_id: str, payload: dict):
        """Send a command to a device."""

        wrapped_payload = {"command": payload}
        _LOGGER.debug(json.dumps(wrapped_payload))
        return await self._request_json(
            "post",
            f"{self._api_url}/api/commands/v1/devices/{device_id}/commands?wait=true&use_internal_names=true",
            wrapped_payload,
        )

    async def elevate_access(self, device_id: str):
        """Elevate access for a device."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)

            expires_at = data.get("expiresAt")
            has_sufficient_access = data.get("writeAccessLevel", 0) >= 20
            if not has_sufficient_access and expires_at:
                try:
                    expires_at_dt = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                    if expires_at_dt < datetime.now(timezone.utc) + timedelta(days=1):
                        has_sufficient_access = True
                except ValueError:
                    pass
            if has_sufficient_access:
                return data

            # Access insufficient, elevate it
            code_data = await self._generate_code(device_id)
            if not code_data:
                return None
            access_code = code_data.get("accessCode")
            if not access_code:
                return None

            claim_status = await self._claim_grant(device_id, access_code)
            if not claim_status:
                return None

            approve_status = await self._approve_access(device_id, access_code)
            if not approve_status:
                return None

            # Get updated access level
            async with self._session.get(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
                headers=self._request_headers(),
            ) as response:
                await self._handle_response(response)
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data

    async def get_access_level(self, device_id: str):
        """Get current access level for a device."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug(
                "Access level response received %s: %s", response.status, data
            )
            return data

    async def _generate_code(self, device_id: str):
        """Generate an access code for a device."""

        await self._ensure_valid_token()

        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/generate-access-code?use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            if response.ok:
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data
            else:
                _LOGGER.error(
                    "Failed to generate access code for device %s, status: %s",
                    device_id,
                    response.status,
                )
                return None

    async def _claim_grant(self, device_id: str, access_code: str):
        """Claim a grant for a device."""

        await self._ensure_valid_token()

        _LOGGER.debug(
            "Claiming grant for device %s with access code %s.", device_id, access_code
        )

        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/claim-grant?access_code={access_code}&use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            if response.ok:
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return True
            else:
                _LOGGER.error(
                    "Failed to claim grant for device %s, status: %s",
                    device_id,
                    response.status,
                )
                return False

    async def _approve_access(self, device_id: str, access_code: str):
        """Approve an access grant for a device."""

        await self._ensure_valid_token()

        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/access-grants?access_code={access_code}&approve=true&use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            if response.ok:
                _LOGGER.debug("Access approved for device %s.", device_id)
            else:
                _LOGGER.error(
                    "Failed to approve access for device %s, status: %s",
                    device_id,
                    response.status,
                )

            return response.ok

    async def set_smartcontrol(self, device_id: str, sh: int, dhw: int):
        """Update smartcontrol setting."""

        use_adaptive = sh != -1 and dhw != -1
        if not use_adaptive:
            payload = {
                "use_adaptive": False,
            }
        else:
            payload = {
                "use_adaptive": use_adaptive,
                "smart_sh_mode": sh,
                "smart_dhw_mode": dhw,
            }

        return await self.update_settings(device_id, payload)

    async def set_extra_tap_water(self, device_id: str, minutes: int):
        """Update extra_tap_water setting."""

        # Capture current time once to ensure consistency across all code paths
        current_time = datetime.now()

        if minutes == 0:
            # Cancel extra tap water
            stop_time = int(current_time.timestamp())
            indefinite = False
            cancel = True
        elif minutes > 0:
            # Set specific duration
            stop_time = int((current_time + timedelta(minutes=minutes)).timestamp())
            indefinite = False
            cancel = False
        else:
            # Set indefinite (always on)
            stop_time = -1
            indefinite = True
            cancel = False

        payload = {
            "set_additional_hot_water": {
                "stopTime": stop_time,
                "indefinite": indefinite,
                "cancel": cancel,
            }
        }

        return await self._send_command(device_id, payload)

    async def set_indoor_temperature_offset(self, device_id: str, value: int):
        """Update indoor_temperature_offset setting."""

        payload = {"settings": [{"name": "indoor_temperature_offset", "value": value}]}

        return await self._update_settings(device_id, payload)

    async def set_fanspeedselector(self, device_id: str, preset_mode: str):
        """Update set_fanspeedselector setting."""

        # Capture current time once to ensure consistency across all code paths
        current_time = datetime.now()

        match preset_mode:
            case "off":
                payload = {"set_fan_mode": {"mode": 0}}
            case "normal":
                stop_time = int(current_time.timestamp())
                indefinite = False
                payload = {
                    "set_fan_mode": {"stopTime": stop_time, "indefinite": indefinite}
                }
            case "extra":
                stop_time = int(
                    (
                        current_time + timedelta(minutes=VENTILATION_BOOST_MINUTES)
                    ).timestamp()
                )
                indefinite = False
                payload = {
                    "set_fan_mode": {"stopTime": stop_time, "indefinite": indefinite}
                }
            case _:
                raise ValueError(f"Invalid preset_mode: {preset_mode}")

        return await self._send_command(device_id, payload)

    async def set_tap_water_capacity_target(self, device_id: str, capacity: int):
        """Update tap_water_capacity_target setting."""

        # Capacities 1, 6, and 7 are "custom" levels that the API does not accept
        # directly — they must be set by writing the corresponding stop/start temperatures.
        _CUSTOM_CAPACITIES = {1, 6, 7}

        if capacity in _CUSTOM_CAPACITIES:
            capacity_to_stop_start = {
                v: k for k, v in TAP_WATER_CAPACITY_MAPPINGS.items()
            }
            start, stop = capacity_to_stop_start[capacity]
            _LOGGER.debug(
                "Setting tap water capacity %s maps to stop %s and start %s.",
                capacity,
                stop,
                start,
            )
            return await self.set_tap_water(device_id, start=start, stop=stop)

        payload = {
            "settings": [{"name": "tap_water_capacity_target", "value": capacity}]
        }

        _LOGGER.debug("Setting tap water capacity target to %s.", capacity)
        return await self._update_settings(device_id, payload)

    async def set_tap_water(self, device_id: str, start: int = 0, stop: int = 0):
        """Update tap_water_start and tap_water_stop settings."""

        if stop == 0 and start == 0:
            _LOGGER.debug("No tap water settings to update, both stop and start are 0.")
            return

        payload = {"settings": []}

        if stop:
            payload["settings"].append({"name": "tap_water_stop", "value": stop})
        if start:
            payload["settings"].append({"name": "tap_water_start", "value": start})

        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_target(self, device_id: str, temperature: float):
        """Update indoor_temperature_target setting."""

        payload = {
            "settings": [{"name": "indoor_temperature_target", "value": temperature}]
        }

        return await self._update_settings(device_id, payload)

    async def get_device_metadata(self, device_id: str):
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._device_metadata_etag:
            headers["If-None-Match"] = self._device_metadata_etag

        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/status",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    self._device_metadata = await response.json()
                    self._device_metadata_etag = response.headers.get("ETag")
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("Device metadata not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        f"Failed to fetch device metadata, status: {response.status}"
                    )
                    self._device_metadata = {}

        _LOGGER.debug("Device metadata fetched: %s", self._device_metadata)
        return self._device_metadata

    async def get_metrics(
        self, device_id: str, method="now", enabled_metrics: Optional[list[str]] = None
    ):
        """Fetch data from the API or Modbus with authentication."""

        names = (
            enabled_metrics
            if enabled_metrics is not None
            else (
                DEFAULT_ENABLED_MODBUS_METRICS
                if self._modbus_tcp
                else DEFAULT_ENABLED_HTTP_METRICS
            )
        )

        if self._modbus_tcp:
            # Try Modbus first, then fall back to HTTP if Modbus is unavailable.
            try:
                modbus_start = asyncio.get_running_loop().time()
                self._metrics_data = await self._read_modbus_metrics(device_id, names)
                modbus_latency = int(
                    (asyncio.get_running_loop().time() - modbus_start) * 1000
                )
                if (
                    isinstance(self._metrics_data, dict)
                    and "metrics" in self._metrics_data
                ):
                    self._metrics_data["metrics"]["latency"] = modbus_latency
                return self._metrics_data
            except Exception as e:
                _LOGGER.warning(
                    "Failed to read metrics via Modbus, falling back to HTTP: %s",
                    e,
                )

        # Fall back to HTTP API if Modbus is not available or fails.
        http_values, etag, total_latency = await self._get_http_values(
            device_id, names, etag_header=self._metrics_etag
        )

        if http_values is not None:
            metrics: dict = {"hpid": device_id}
            metrics["latency"] = total_latency
            for metric_name in names:
                if metric_name in http_values:
                    metrics[metric_name] = http_values[metric_name]
                    if metric_name == "fan0_10v":
                        metrics[metric_name] = int(float(metrics[metric_name]) * 10)
                else:
                    _LOGGER.warning(f"Metric {metric_name} not found in response data.")
            self._metrics_data = {"metrics": metrics}
            self._metrics_etag = etag

        _LOGGER.debug("HTTP metrics read: %s", self._metrics_data)
        return self._metrics_data

    async def get_http_metrics(self, device_id: str, metric_names: list[str]) -> dict:
        """Fetch specific metrics from the HTTP API, bypassing Modbus."""
        http_values, _, _ = await self._get_http_values(device_id, metric_names)
        if not http_values:
            return {"metrics": {}}
        metrics = {
            name: http_values[name] for name in metric_names if name in http_values
        }
        return {"metrics": metrics}

    async def _get_http_values(
        self,
        device_id: str,
        metric_names: list[str],
        etag_header: Optional[str] = None,
    ) -> tuple[dict | None, str | None, int | None]:
        """Perform a raw HTTP values fetch and return (values_dict, etag, total_latency).

        Returns (None, None, None) on 304 Not Modified and on any other
        unhandled non-200 response status.
        Raises APIAuthError on 403, APIConnectionError on 500.
        """
        await self._ensure_valid_token()
        headers = self._request_headers()
        if etag_header:
            headers["If-None-Match"] = etag_header

        names_list = "".join(f"&names[]={name}" for name in metric_names)
        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/devices/{device_id}/values"
            f"?use_internal_names=true&timeout={METRICS_TIMEOUT_SECONDS}{names_list}",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    data = await response.json()
                    _LOGGER.debug("HTTP values fetched: %s", data)
                    return (
                        data.get("values", {}),
                        response.headers.get("ETag"),
                        data.get("total_latency"),
                    )
                case 403:
                    _LOGGER.error("Authentication failure: %s", response.status)
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("HTTP values not modified, using cached data.")
                    return None, None, None
                case 500:
                    _LOGGER.error("Internal server error: %s", response.status)
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch HTTP values, status: %s", response.status
                    )
                    return None, None, None

    async def get_settings(self, device_id: str):
        """Fetch settings from the API or Modbus."""

        if self._modbus_tcp:
            # Read settings from Modbus holding registers
            try:
                # Read all settings that have a corresponding holding register
                settings_to_read = [
                    setting_key
                    for setting_key in MODBUS_HOLDING_TO_SETTINGS_MAP.keys()
                    if setting_key in MODBUS_HOLDING_REGISTER_MAP
                ]
                result = await self._read_modbus_settings(device_id, settings_to_read)
                return result
            except Exception as e:
                _LOGGER.warning(
                    "Failed to read settings via Modbus, falling back to HTTP: %s", e
                )
                # Fall through to HTTP logic below

        # Original HTTP logic
        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._settings_etag:
            headers["If-None-Match"] = self._settings_etag

        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    self._settings_data = await response.json()
                    self._settings_etag = response.headers.get("ETag")
                    _LOGGER.debug("HTTP Settings fetched: %s", self._settings_data)
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("HTTP Settings not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch HTTP settings, status: %s", response.status
                    )
                    self._settings_data = {}

        _LOGGER.debug("HTTP Settings read: %s", self._settings_data)
        return self._settings_data

    async def get_primary_device(self):
        """Fetch device from the API with authentication."""

        devices = await self.get_devices()
        if not devices:
            _LOGGER.error("No devices found.")
            return None

        device = devices[0]

        metadata = await self.get_device_metadata(device.get("id"))
        if metadata:
            device = {**device, **metadata}

        _LOGGER.debug("Primary device fetched: %s", device)

        return device

    async def get_devices(self):
        """Fetch devices from the API with authentication."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{self._api_url}/api/inventory/v1/users/me/devices",
            headers=self._request_headers(),
        ) as response:
            match response.status:
                case 200:
                    devices_data = await response.json()
                    _LOGGER.debug("Devices fetched successfully: %s", devices_data)
                    return devices_data.get("devices") if devices_data else None
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch devices, status: %s", response.status
                    )
                    raise APIConnectionError(
                        response=response, message="Failed to fetch devices"
                    )


class APIAuthError(Exception):
    """Exception raised for authentication errors."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "Authentication failed",
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)


class APIConnectionError(Exception):
    """Exception raised for connection/API errors."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "API request failed",
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)


class APIRateLimitError(Exception):
    """Exception raised for rate limiting."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "Rate limit exceeded",
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)
