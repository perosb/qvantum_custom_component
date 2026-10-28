# Alarm codes and their friendly names

**Version:** 2026-10-28  
**Scope:** the alarm code slots the pump reports in Modbus input 150-155 and the
names the integration attaches to them.  
**Purpose:** say where the names come from, why they are English, and what an
unknown code does.

**Source tags** used below:

| Tag | Meaning |
|---|---|
| `[R]` | Qvantum public alarm list, <https://www.qvantum.com/alarms/>. |
| `[L]` | Live observation: the Qvantum app on a QG-14, reported in issue #459. |
| `[I]` | Home Assistant integration code and semantics. |

---

## 1. What the pump reports

The Modbus input map exposes `active_alarms` (the number of active alarms) and
five raw slots, `alarm_1_code` … `alarm_5_code` (input 151-155). The integration
creates a diagnostic integer sensor per slot; it does not decode, filter or
reorder them. `[R][I]`

Since 2026.10.28 each code sensor also carries a **`description` attribute**:
Qvantum's friendly name for that code. The state remains the bare number, so
existing automations that compare against the code keep working. `[I]`

```yaml
{{ state_attr('sensor.qvantum_alarm_1_code', 'description') }}
```

The count sensor (`active_alarms`) has no description attribute: which slot
holds which alarm is the pump's choice, and pairing the count with a name would
mean guessing. `[I]`

## 2. Where the names come from

[`alarm_codes.py`](../custom_components/qvantum/alarm_codes.py) holds the
table. Its source is the public Qvantum alarm list,
<https://www.qvantum.com/alarms/> (fetched 2026-10-28: 101 numeric codes), plus
three entries that page does not cover: `[R]`

| Code | Name | Source |
|---|---|---|
| 252 | Source water low temperature protection | Qvantum app, issue #459 (locking; must be acknowledged) |
| 391 | QGM2 fault of water flow switch on the ground source side | Qvantum app, issue #459 |
| 279 | Fault of water flow switch on the ground source side. | public list; the app words it as the QGM1 flow switch |

The public list has no 252 and no 391, and `279` is the generic flow-switch
text. The app shows the two units separately (QGM1 → 279, QGM2 → 391), which is
why 391 exists in the table anyway. `[L]`

## 3. Rules

- **Do not invent a name.** An unknown code gets no `description` attribute; a
  bare number is honest, a guessed name is not. Add an entry only with a source
  (the public list, the app, or a Qvantum document) named in the commit.
- **Names stay English.** The public list publishes one language and the
  integration has no translated alarm texts. They are data values in an
  attribute, not UI strings, so they deliberately do not live in
  `translations/*.json`; the entity names around them are translated as usual.
- **Codes are product-agnostic.** The same number means the same fault across
  QE, QA+QH and QG where the fault exists; model-specific codes simply never
  appear on the other products. Do not add a model prefix to the table.
- **A refresh is a code change.** Re-fetch the page, update the table, and run
  the tests — they assert that the app-only codes survive and that every name
  is a non-empty string.

The per-compressor-unit context for codes 252, 279 and 391 (two units, the
flow-switch alarms) is in [compressor-units.md](compressor-units.md).

---

*The table is only as good as its source. When Qvantum publishes a newer list,
the newer list wins; this document carries the fetch date so a stale table is
recognisable.*
