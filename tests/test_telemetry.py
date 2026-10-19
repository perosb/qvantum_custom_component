"""Tests for the telemetry consent prompt (no network code)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum import repairs as qvantum_repairs
from custom_components.qvantum import config_flow as qvantum_config_flow
from custom_components.qvantum import telemetry
from custom_components.qvantum.repairs import TelemetryConsentFlow, async_create_fix_flow


class FakeStore:
    """Minimal Store stand-in."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data
        self.saved: list[dict] = []
        self.removed = False

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = dict(data)
        self.saved.append(dict(data))

    async def async_remove(self):
        self.data = None
        self.removed = True


def _hass():
    return MagicMock()


@pytest.mark.asyncio
async def test_first_contact_is_stamped_and_nothing_is_asked():
    """The clock starts on the first start with this version."""
    store = FakeStore()
    with (
        patch.object(telemetry, "_store", return_value=store),
        patch.object(telemetry.ir, "async_create_issue") as create_issue,
    ):
        raised = await telemetry.async_maybe_prompt(_hass(), "entry", now=1000.0)

    assert raised is False
    create_issue.assert_not_called()
    assert store.saved[-1]["first_seen"] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_existing_installs_are_asked_after_the_grace_period():
    """An install that predates the feature is asked too, a week in."""
    first_seen = 1000.0
    store = FakeStore({"first_seen": first_seen})
    with (
        patch.object(telemetry, "_store", return_value=store),
        patch.object(telemetry.ir, "async_create_issue") as create_issue,
    ):
        too_soon = await telemetry.async_maybe_prompt(
            _hass(), "entry", now=first_seen + 3 * 86400
        )
        raised = await telemetry.async_maybe_prompt(
            _hass(), "entry", now=first_seen + 8 * 86400
        )

    assert too_soon is False
    assert raised is True
    kwargs = create_issue.call_args.kwargs
    assert kwargs["is_fixable"] is True
    assert kwargs["translation_key"] == "telemetry_consent"
    assert create_issue.call_args.args[2] == telemetry.telemetry_issue_id("entry")


@pytest.mark.asyncio
async def test_a_decision_is_never_asked_again():
    for consent in (telemetry.CONSENT_GRANTED, telemetry.CONSENT_DECLINED):
        store = FakeStore({"first_seen": 1000.0, "consent": consent})
        with (
            patch.object(telemetry, "_store", return_value=store),
            patch.object(telemetry.ir, "async_create_issue") as create_issue,
            patch.object(telemetry.ir, "async_delete_issue") as delete_issue,
        ):
            raised = await telemetry.async_maybe_prompt(_hass(), "entry", now=1e9)

        assert raised is False
        create_issue.assert_not_called()
        delete_issue.assert_called_once()


@pytest.mark.asyncio
async def test_grant_creates_a_random_token_and_closes_the_issue():
    store = FakeStore({"first_seen": 1000.0})
    with (
        patch.object(telemetry, "_store", return_value=store),
        patch.object(telemetry.ir, "async_delete_issue") as delete_issue,
    ):
        data = await telemetry.async_set_consent(_hass(), "entry", True)

    assert data["consent"] == telemetry.CONSENT_GRANTED
    assert len(data["token"]) == 32  # 16 random bytes as hex
    int(data["token"], 16)  # hex, so not a serial-like string
    delete_issue.assert_called_once()


@pytest.mark.asyncio
async def test_decline_keeps_no_token():
    store = FakeStore({"first_seen": 1000.0, "token": "deadbeef"})
    with (
        patch.object(telemetry, "_store", return_value=store),
        patch.object(telemetry.ir, "async_delete_issue"),
    ):
        data = await telemetry.async_set_consent(_hass(), "entry", False)

    assert data["consent"] == telemetry.CONSENT_DECLINED
    assert "token" not in data


@pytest.mark.asyncio
async def test_revoke_withdraws_consent():
    store = FakeStore({"consent": telemetry.CONSENT_GRANTED, "token": "abc"})
    with (
        patch.object(telemetry, "_store", return_value=store),
        patch.object(telemetry.ir, "async_delete_issue"),
    ):
        data = await telemetry.async_revoke(_hass(), "entry")

    assert data["consent"] == telemetry.CONSENT_DECLINED
    assert "token" not in data


@pytest.mark.asyncio
async def test_forget_removes_state_and_issue():
    store = FakeStore({"consent": telemetry.CONSENT_GRANTED})
    with (
        patch.object(telemetry, "_store", return_value=store),
        patch.object(telemetry.ir, "async_delete_issue") as delete_issue,
    ):
        await telemetry.async_forget(_hass(), "entry")

    assert store.removed is True
    delete_issue.assert_called_once()


@pytest.mark.asyncio
async def test_state_is_in_memory_only_without_storage():
    """A host without a config dir must not crash the prompt."""
    with patch.object(telemetry, "_store", return_value=None):
        assert await telemetry.async_load_state(_hass(), "entry") == {}
        raised = await telemetry.async_maybe_prompt(_hass(), "entry", now=1.0)

    assert raised is False


def test_issue_id_round_trip():
    issue_id = telemetry.telemetry_issue_id("abc123")
    assert issue_id == "telemetry_consent_abc123"
    assert telemetry.entry_id_from_issue_id(issue_id) == "abc123"
    assert telemetry.entry_id_from_issue_id("other") is None


@pytest.mark.asyncio
async def test_fix_flow_offers_two_choices_and_records_a_grant():
    flow = await async_create_fix_flow(
        MagicMock(), telemetry.telemetry_issue_id("entry"), None
    )
    assert isinstance(flow, TelemetryConsentFlow)
    flow.hass = MagicMock()

    menu = await flow.async_step_init()
    assert menu["type"] == "menu"
    assert menu["menu_options"] == ["grant", "decline"]

    with patch.object(
        qvantum_repairs, "async_set_consent", AsyncMock()
    ) as set_consent:
        result = await flow.async_step_grant()

    set_consent.assert_awaited_once_with(flow.hass, "entry", True)
    assert result["type"] == "create_entry"


@pytest.mark.asyncio
async def test_fix_flow_records_a_decline():
    flow = TelemetryConsentFlow("entry")
    flow.hass = MagicMock()

    with patch.object(
        qvantum_repairs, "async_set_consent", AsyncMock()
    ) as set_consent:
        result = await flow.async_step_decline()

    set_consent.assert_awaited_once_with(flow.hass, "entry", False)
    assert result["type"] == "create_entry"


@pytest.mark.asyncio
async def test_unknown_issue_is_rejected():
    with pytest.raises(ValueError):
        await async_create_fix_flow(MagicMock(), "not_ours", None)


def _options_flow():
    from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {}
    entry.options = {}
    flow = QvantumOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    return flow


@pytest.mark.asyncio
async def test_options_box_reflects_the_stored_consent():
    """The checkbox must show the decision, not a silent default."""
    flow = _options_flow()
    with patch.object(
        qvantum_config_flow, "async_load_state",
        AsyncMock(return_value={"consent": telemetry.CONSENT_GRANTED}),
    ):
        form = await flow.async_step_init()

    schema = form["data_schema"].schema
    key = next(k for k in schema if getattr(k, "schema", None) == "telemetry")
    assert key.default() is True


@pytest.mark.asyncio
async def test_untouched_box_is_not_a_decision():
    """Opening the options for the scan interval must not decline for you."""
    flow = _options_flow()
    with (
        patch.object(
            qvantum_config_flow, "async_load_state", AsyncMock(return_value={})
        ),
        patch.object(
            qvantum_config_flow, "async_set_consent", AsyncMock()
        ) as set_consent,
    ):
        await flow._async_apply_telemetry_choice(False)

    set_consent.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_box_records_the_decision():
    flow = _options_flow()
    with (
        patch.object(
            qvantum_config_flow, "async_load_state", AsyncMock(return_value={})
        ),
        patch.object(
            qvantum_config_flow, "async_set_consent", AsyncMock()
        ) as set_consent,
    ):
        await flow._async_apply_telemetry_choice(True)

    set_consent.assert_awaited_once_with(flow.hass, "entry", True)


@pytest.mark.asyncio
async def test_turning_the_box_off_revokes():
    flow = _options_flow()
    with (
        patch.object(
            qvantum_config_flow,
            "async_load_state",
            AsyncMock(return_value={"consent": telemetry.CONSENT_GRANTED}),
        ),
        patch.object(
            qvantum_config_flow, "async_set_consent", AsyncMock()
        ) as set_consent,
    ):
        await flow._async_apply_telemetry_choice(False)

    set_consent.assert_awaited_once_with(flow.hass, "entry", False)
