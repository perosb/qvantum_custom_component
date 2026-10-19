"""Opt-in telemetry consent state.

This module stores **only** the consent decision and a locally generated
installation token — it never sends anything. A granted consent is what a
future uploader may use; the decision must stay revocable and the token must be
random (never derived from the serial number or any other identifier) so the
pool can deduplicate and delete without the token becoming an identifier.

The prompt is a repair issue with a two-choice fix flow ("Bidra" / "Nej tack"),
not a config option: an option is only found by users who already open the
options dialog, while the issue is the surface everybody sees. It is raised
once, after ``PROMPT_AFTER_DAYS`` of use — by then the summary has something to
contribute and the user has had the feature working first. Consent is therefore
never a condition for the adaptive curve, and "no" is remembered so the
question is not asked again.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
#: Days of use before the question is asked: enough history for the summary.
PROMPT_AFTER_DAYS = 7

CONSENT_GRANTED = "granted"
CONSENT_DECLINED = "declined"

#: Repair issue id prefix; the config entry id is appended.
ISSUE_PREFIX = "telemetry_consent_"

#: Where the anonymous summary is posted. Hard-coded rather than configurable:
#: the payload contract and the intake live in the same repository, and an URL
#: option would invite sending this data somewhere else without the prompt.
ENDPOINT = "https://qvantum-summaries.osbeck.workers.dev/ingest"
ERASE_ENDPOINT = "https://qvantum-summaries.osbeck.workers.dev/install"

#: Weekly: the review question is a rate over weeks, not a live stream.
UPLOAD_INTERVAL = timedelta(days=7)
#: A check runs more often than that so a missed week is retried soon.
UPLOAD_CHECK_INTERVAL = timedelta(hours=6)
REQUEST_TIMEOUT_SECONDS = 30


def telemetry_store_key(entry_id: str) -> str:
    """Storage key for one config entry's telemetry state."""
    return f"{DOMAIN}.telemetry.{entry_id}"


def telemetry_issue_id(entry_id: str) -> str:
    """Repair issue id for one config entry's consent question."""
    return f"{ISSUE_PREFIX}{entry_id}"


def entry_id_from_issue_id(issue_id: str) -> str | None:
    """Recover the config entry id from a consent issue id."""
    if not issue_id.startswith(ISSUE_PREFIX):
        return None
    return issue_id[len(ISSUE_PREFIX) :] or None


def _store(hass: HomeAssistant, entry_id: str) -> Store | None:
    """Return a store when the host has a real config dir, else ``None``.

    A test harness (or any host without storage) keeps the state in memory
    only; consent is optional and must never fail setup.
    """
    if not isinstance(getattr(getattr(hass, "config", None), "config_dir", None), str):
        return None
    return Store(hass, STORAGE_VERSION, telemetry_store_key(entry_id))


async def async_load_state(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Load the stored consent state (best effort)."""
    store = _store(hass, entry_id)
    if store is None:
        return {}
    try:
        data = await store.async_load()
    except Exception as err:  # noqa: BLE001 — consent state is best effort
        _LOGGER.debug("Could not load telemetry state: %s", err)
        return {}
    return dict(data) if isinstance(data, dict) else {}


async def async_save_state(
    hass: HomeAssistant, entry_id: str, data: dict[str, Any]
) -> None:
    """Persist the consent state (best effort)."""
    store = _store(hass, entry_id)
    if store is None:
        return
    try:
        await store.async_save(data)
    except Exception as err:  # noqa: BLE001 — a failed save must not break setup
        _LOGGER.debug("Could not save telemetry state: %s", err)


async def async_set_consent(
    hass: HomeAssistant, entry_id: str, granted: bool
) -> dict[str, Any]:
    """Record the answer, create the token on a grant, and close the issue."""
    data = await async_load_state(hass, entry_id)
    data["consent"] = CONSENT_GRANTED if granted else CONSENT_DECLINED
    data["decided_at"] = time.time()
    if granted and not data.get("token"):
        # Random, generated locally, never derived from an identifier.
        data["token"] = secrets.token_hex(16)
    # The token is deliberately *kept* when consent is withdrawn: it is the
    # only capability that can erase rows already sent, and the prompt promises
    # deletion. Only removing the entry forgets it.
    await async_save_state(hass, entry_id, data)
    ir.async_delete_issue(hass, DOMAIN, telemetry_issue_id(entry_id))
    return data


async def async_revoke(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Withdraw consent; the token stays so the rows can still be erased."""
    return await async_set_consent(hass, entry_id, False)


async def async_maybe_prompt(
    hass: HomeAssistant, entry_id: str, *, now: float | None = None
) -> bool:
    """Raise the consent issue once the installation has enough history.

    Existing installs are covered too: ``first_seen`` is stamped on the first
    start with this version, so the question arrives a week later rather than
    never. Returns ``True`` when the issue was raised.
    """
    issued = telemetry_issue_id(entry_id)
    data = await async_load_state(hass, entry_id)
    if data.get("consent") in (CONSENT_GRANTED, CONSENT_DECLINED):
        ir.async_delete_issue(hass, DOMAIN, issued)
        return False
    current = time.time() if now is None else float(now)
    first_seen = data.get("first_seen")
    if not isinstance(first_seen, (int, float)) or isinstance(first_seen, bool):
        data["first_seen"] = current
        await async_save_state(hass, entry_id, data)
        return False
    if current - float(first_seen) < PROMPT_AFTER_DAYS * 86400:
        return False
    ir.async_create_issue(
        hass,
        DOMAIN,
        issued,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="telemetry_consent",
    )
    return True


def _due(state: dict[str, Any], now: float) -> bool:
    """True when a week has passed since the last successful upload."""
    last = state.get("last_upload")
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return True
    return now - float(last) >= UPLOAD_INTERVAL.total_seconds()


async def async_upload_summary(
    hass: HomeAssistant,
    entry_id: str,
    *,
    now: float | None = None,
    force: bool = False,
) -> bool:
    """Post the summary once a week, and only with a granted consent.

    Returns ``True`` when a row was accepted. The counters are reset only after
    the worker accepted the payload, so a failed upload retries next cycle
    instead of losing a week of evidence.
    """
    state = await async_load_state(hass, entry_id)
    if state.get("consent") != CONSENT_GRANTED:
        return False
    token = state.get("token")
    if not isinstance(token, str) or len(token) != 32:
        return False
    current = time.time() if now is None else float(now)
    if not force and not _due(state, current):
        return False

    # Imported here: the summary module imports Home Assistant HTTP helpers and
    # this keeps the consent path importable on its own.
    from .summary import async_build_summary

    payload = await async_build_summary(hass)
    if not isinstance(payload, dict):
        return False
    # The token travels in the request only: the exported file stays
    # identifier-free.
    body = {**payload, "install_token": token}

    session = async_get_clientsession(hass)
    try:
        async with session.post(
            ENDPOINT,
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "qvantum-hacs-telemetry"},
        ) as response:
            if response.status != 200:
                _LOGGER.debug(
                    "Summary upload rejected (%s); will retry next cycle",
                    response.status,
                )
                return False
    except Exception as err:  # noqa: BLE001 — telemetry must never break a poll
        _LOGGER.debug("Summary upload failed: %s", err)
        return False

    state["last_upload"] = current
    await async_save_state(hass, entry_id, state)
    await _reset_counters(hass)
    return True


async def _reset_counters(hass: HomeAssistant) -> None:
    """Start a fresh counting period; the uploaded row covers the old one."""
    from .const import DOMAIN

    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        curve = getattr(runtime, "curve_coordinator", None)
        reset = getattr(curve, "async_reset_counters", None)
        if reset is not None:
            await reset()


async def async_delete_telemetry(hass: HomeAssistant, entry_id: str) -> bool:
    """Erase every row for this installation, using its own token."""
    state = await async_load_state(hass, entry_id)
    token = state.get("token")
    if not isinstance(token, str) or len(token) != 32:
        return False
    session = async_get_clientsession(hass)
    try:
        async with session.delete(
            f"{ERASE_ENDPOINT}/{token}",
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "qvantum-hacs-telemetry"},
        ) as response:
            return response.status == 200
    except Exception as err:  # noqa: BLE001 — best effort
        _LOGGER.debug("Could not erase telemetry rows: %s", err)
        return False


async def async_schedule_uploads(hass: HomeAssistant, entry_id: str):
    """Upload on a six-hour check while a week is due; returns the unsubscriber."""
    from homeassistant.helpers.event import async_track_time_interval

    async def _tick(_now: datetime) -> None:
        try:
            await async_upload_summary(hass, entry_id)
        except Exception as err:  # noqa: BLE001 — never break the scheduler
            _LOGGER.debug("Scheduled summary upload failed: %s", err)

    return async_track_time_interval(hass, _tick, UPLOAD_CHECK_INTERVAL)


async def async_forget(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the stored state and the issue when the entry is removed."""
    ir.async_delete_issue(hass, DOMAIN, telemetry_issue_id(entry_id))
    store = _store(hass, entry_id)
    if store is None:
        return
    try:
        await store.async_remove()
    except Exception as err:  # noqa: BLE001 — removal is best effort
        _LOGGER.debug("Could not remove telemetry state: %s", err)
