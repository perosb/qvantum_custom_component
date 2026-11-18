"""Repair flows.

Two issues are fixable, and both are two-choice menus so answering is a single
click: the telemetry consent question ("Bidra" / "Nej tack") and the invitation
to activate a ready adaptive curve ("Activate" / "Not now"). The second one
exists because the curve writes to the pump and the integration never activates
it on its own — the user has no other way to know the gate has passed.
"""

from __future__ import annotations

import logging

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .telemetry import async_set_consent, entry_id_from_issue_id

_LOGGER = logging.getLogger(__name__)

#: Prefix of the "the curve is ready" issue id; the entry id follows it.
CURVE_READY_PREFIX = "curve_ready_"


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


class CurveReadyFlow(RepairsFlow):
    """Offer to activate the adaptive curve, or leave it in shadow."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    def _curve(self):
        """The curve coordinator for this entry, or ``None``."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != self._entry_id:
                continue
            runtime = getattr(entry, "runtime_data", None)
            return getattr(runtime, "curve_coordinator", None)
        return None

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> data_entry_flow.FlowResult:
        """Offer the two answers, with the numbers the gate used.

        A marginal pass (median 1.44 against 1.5) is not the same decision as a
        comfortable one, so the figures travel with the question.
        """
        curve = self._curve()
        snapshot = getattr(curve, "data", None)
        placeholders = {
            "median": f"{getattr(snapshot, 'median_abs_c', 0.0) or 0.0:.2f}",
            "p95": f"{getattr(snapshot, 'p95_abs_c', 0.0) or 0.0:.2f}",
            "hours": f"{getattr(snapshot, 'baseline_learned_hours', 0) or 0}",
        }
        return self.async_show_menu(
            step_id="init",
            menu_options=["activate", "decline"],
            description_placeholders=placeholders,
        )

    async def async_step_activate(
        self, user_input: dict | None = None
    ) -> data_entry_flow.FlowResult:
        """Turn the curve on — the same call the switch makes."""
        curve = self._curve()
        if curve is None:
            return self.async_abort(reason="not_loaded")
        await curve.async_set_control_mode("active")
        _LOGGER.info("Adaptive curve activated from the repair flow")
        return self.async_create_entry(title="", data={})

    async def async_step_decline(
        self, user_input: dict | None = None
    ) -> data_entry_flow.FlowResult:
        """Remember the answer so the question is not repeated this streak."""
        curve = self._curve()
        if curve is not None:
            curve.async_decline_ready_issue()
        return self.async_create_entry(title="", data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """Create the flow for a fixable issue."""
    # The curve-ready id carries the entry id after its own prefix; the
    # telemetry helper only knows the consent prefix.
    if issue_id.startswith(CURVE_READY_PREFIX):
        entry_id = issue_id[len(CURVE_READY_PREFIX) :] or None
        if entry_id is None:
            raise ValueError(f"unknown repair issue: {issue_id!r}")
        return CurveReadyFlow(entry_id)
    entry_id = entry_id_from_issue_id(issue_id)
    if entry_id is None:
        raise ValueError(f"unknown repair issue: {issue_id!r}")
    return TelemetryConsentFlow(entry_id)
