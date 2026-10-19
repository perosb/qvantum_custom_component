"""Repair flows.

Only one issue is fixable: the telemetry consent question. It is a two-choice
menu — "Bidra" or "Nej tack" — so answering it is a single click, which is the
difference between a 5 % and a 30 % response rate. The answer is stored (and
can be revoked later); the adaptive curve never depends on it.
"""

from __future__ import annotations

import logging

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .telemetry import async_set_consent, entry_id_from_issue_id

_LOGGER = logging.getLogger(__name__)


class TelemetryConsentFlow(RepairsFlow):
    """Ask for the anonymous-summary opt-in."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> data_entry_flow.FlowResult:
        """Offer the two answers; either one closes the issue."""
        return self.async_show_menu(step_id="init", menu_options=["grant", "decline"])

    async def async_step_grant(
        self, user_input: dict | None = None
    ) -> data_entry_flow.FlowResult:
        """Record a grant and create the local installation token."""
        await async_set_consent(self.hass, self._entry_id, True)
        _LOGGER.info("Anonymous summary sharing enabled for %s", self._entry_id)
        return self.async_create_entry(title="", data={})

    async def async_step_decline(
        self, user_input: dict | None = None
    ) -> data_entry_flow.FlowResult:
        """Record a refusal; the question is not asked again."""
        await async_set_consent(self.hass, self._entry_id, False)
        return self.async_create_entry(title="", data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """Create the flow for a telemetry consent issue."""
    entry_id = entry_id_from_issue_id(issue_id)
    if entry_id is None:
        raise ValueError(f"unknown repair issue: {issue_id!r}")
    return TelemetryConsentFlow(entry_id)
