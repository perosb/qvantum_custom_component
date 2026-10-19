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

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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
    elif not granted:
        data.pop("token", None)
    await async_save_state(hass, entry_id, data)
    ir.async_delete_issue(hass, DOMAIN, telemetry_issue_id(entry_id))
    return data


async def async_revoke(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Withdraw consent and drop the token."""
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
