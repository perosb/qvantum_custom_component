# MQTT transport — proof of concept

A third transport beside cloud HTTP and local Modbus, on the same
`QvantumClient` contract. It exists to answer one question: what does a third
transport cost, and does the existing architecture absorb it? ~640 lines of
production code, and yes.

**Not a shipped feature, and not on `main`.** The first 1.13.0 build of
Qvantum's own MQTT API was a command proxy — signed commands forwarded to the
cloud API, and a test message as its only publish. Later builds publish a
telemetry feed (see the last section) and, since `1848e2e43`, the control
parameters as an event. The protocol below is still **ours**: it assumes the
pump carries the whole snapshot — metrics, settings and commands — which is what
the feed does not do.

## Protocol

Everything rides one envelope, matching the shape the display's own MQTT API
uses (`{"hpid": ..., "p": ...}`):

```
qvantum/<serial>/state         retained   {"metrics": {...}, "settings": {...}}
qvantum/<serial>/cmd           write      {"set": {name: value, ...}}
                                          {"cmd": {"name": ..., "args": {...}}}
qvantum/<serial>/cmd-res       ack        {"ok": true, "applied": {...}}
                                          {"ok": true, "result": {...}}
qvantum/<serial>/availability  retained   online / offline (last will, from the pump)
```

The three verbs are the three things a transport has to carry: **metrics**
(read), **settings** (read and write) and **commands** (operations that are
neither, such as the setpoint list or the compressor speed cap). Qvantum signs
their responses with a certificate in the display; we cannot, so the ack is a
plain `p` and the difference is documented rather than faked.

Reads are served from the last pushed snapshot. Writes and commands are
request/response with a timeout, one at a time. A new snapshot asks the
coordinator for a refresh, so derived metrics still go through the one path they
always did.

**The transport does not poll.** `resolve_poll_interval` returns `None` for MQTT,
so the coordinator runs no timer at all: freshness comes from the broker and
liveness comes from the availability topic. That is the point of a push
transport, and it is why the availability handling is not optional — without it
a dead pump would serve its last snapshot forever, and nothing would say so.

## How the code is arranged

- `client/mqtt/topics.py` — topic layout and envelope.
- `client/mqtt/client.py` — `QvantumMqttClient`. Home-Assistant-free: the broker
  is reached through two injected coroutines.
- `client/protocol.py` — `QvantumMqttClientProtocol` narrows the shared contract
  to what only MQTT can do (`writable`, `send_command`), the way
  `QvantumModbusClientProtocol` does for holding registers.
- The HA layer (`__init__.py`) wires those two coroutines to
  `mqtt.async_subscribe` / `mqtt.async_publish`.
- **The adaptive curve runs here too**, for the same reason it does on the
  cloud: its writes are settings, and `write_access` asks the link's own flag.
  Left out for now: the external-room-temperature feed, which writes holding 14
  on a Modbus timer.
- `config_flow.py` — the transport is chosen with `CONF_TRANSPORT`, which falls
  back to `CONF_MODBUS_TCP`, so no entry needs migrating. Both the setup menu and
  reconfigure offer it: an existing entry can be moved onto the broker without
  being deleted and recreated (which would also revert the curve to Auto on the
  way out).

## Trying it in a Home Assistant instance

You need a broker (Home Assistant's MQTT integration must be configured) and,
to stand in for the pump, the simulator in `tools/`.

1. **Get a snapshot of a real installation.** Download the integration's
   diagnostics (Settings → Devices & Services → Qvantum → Download diagnostics)
   and turn it into the simulator's snapshot:

   ```sh
   python3 tools/snapshot_from_diagnostics.py diagnostics.json -o snapshot.json
   ```

2. **Install this branch** into the instance's `custom_components/qvantum/`
   (HACS → custom repository → this repo → this branch, or copy the folder).

3. **Run the simulator** against the same broker:

   ```sh
   uv pip install paho-mqtt
   python3 tools/mqtt_pump_sim.py --snapshot snapshot.json \
       --host <broker> --serial POC0001 --verbose
   ```

4. **Add the entry**: Settings → Devices & Services → Add integration → Qvantum
   → **MQTT**, topic prefix `qvantum`, serial `POC0001`, writes on.

The entry loads and its entities fill from the retained snapshot. A write goes
`cmd` → simulator → `cmd-res` → republished state, so the entity ends up holding
what the "pump" applied.

**`single_config_entry` is still on.** If the instance already has a Qvantum
entry, the flow aborts; remove it, or test on an instance without one.

**Entity ids get a suffix.** A second entry on an instance that already has one
produces `sensor.qvantum_…_2` for every entity whose name collides.

The simulator answers `read_all_setpoints` (from the snapshot) and
`limit_max_compressor_speed` (with the firmware's six-step ladder and its own
wording for a rejected `limitStep`). Those two demonstrate the command verb; they
are not the pump's real behaviour.

## Following a live installation instead of replaying a file

A still image is enough to populate entities, but not to watch the transport
work. The simulator can follow a running installation instead, which is what
lets one Home Assistant keep the real pump on Modbus while another instance
consumes the same readings over MQTT:

```sh
python3 tools/mqtt_pump_sim.py \
    --follow http://homeassistant.local:8123 \
    --token-file ~/.ha-token \
    --entry <config_entry_id> \
    --serial POC0001 --interval 30
```

It polls the source's config-entry diagnostics (the same download that seeds a
snapshot), splits it, and republishes **only when something moved**. Settings the
dev instance writes are kept: a real pump reports a setpoint back rather than
overwriting it with the previous value, and a test that silently reverted every
write would be lying.

`--entry` is the config entry id of the Qvantum entry on the *source* instance —
`GET /api/config/config_entries/entry` lists it. The token needs read access to
the diagnostics endpoint.

## What this branch adds to `main`

`main` already carries the transport vocabulary (`transport_kind`, `is_cloud`,
`is_full_payload`, `write_access`) and runs the adaptive curve on every transport
that can write. This branch adds only the transport itself:

- `client/mqtt/` — the client, topics and envelope;
- `CONF_TRANSPORT = "mqtt"` and the `CONF_MQTT_*` options, with `transport_kind`
  and `resolve_transport` accepting the third value;
- no poll interval (`resolve_poll_interval` returns `None`), plus the one-shot
  offline retry that replaces it when an update fails;
- the config flow's MQTT step and reconfigure step, and the options dialog's
  curve terms without an interval;
- `tools/` (the pump simulator and the diagnostics→snapshot converter).

## What is proven, and what is not

Proven against a real broker and a real Home Assistant: 99 metrics and 31
settings read from the retained snapshot, `curve_10` 30 → 31 round-tripped, an
unknown key rejected, the entry loading with 129 entities, and a
`number.set_value` 15 → 16 from the UI through the broker to the simulator and
back.

Proven as well, in both directions: killing the simulator flips the client to
offline within the same second and the next read fails with *"The pump is
offline on qvantum/…/availability"*, so the entities go unavailable instead of
freezing — and starting it again brings them back, because the `online` message
wakes the same listener. No timer is involved either way.

That places two requirements on a real pump: it must set a retained `offline`
last will (or its death is invisible) and republish a retained `online` on
connect (or its return is).

Because a lost `online` would otherwise strand the entities until Home
Assistant restarts, a **failed** update arms a single retry five minutes out
(`_OFFLINE_RETRY_SECONDS`). It exists only in the failure state: the first
success cancels it, and it is never armed on a transport that has a poll
interval to retry on its own. So working installations still run no timer —
the timer is what happens *instead of* polling only when something is already
broken.

Not proven: that Qvantum's feed can replace our own snapshot. It publishes a
named telemetry list to a configured broker and pushes the control parameters as
an event, but it carries no thermal power and no COP (last section), and
`send_command` is still shaped from the display's own command set
(`read_all_setpoints`, `set_setpoint`, `limit_max_compressor_speed`) rather than
from a response we have seen. Until that is answered this is a transport looking
for a pump.

## The 1.13.0 builds: a telemetry feed, and what it changes here

Firmware `QEWTTRV1_1130-898953566` (2026-11-24) replaced the display's MQTT
client from a command proxy into a configured API; `QEWTTRV1_1130-1848e2e43`
(2026-11-27) added a fourth section and the first push channel for settings. The
configuration document is `{connection, telemetry, command, events}` — **all four
objects are required**, and the three-object document of the earlier build is
rejected:

- `connection` — `enabled`, `host`, `port`, `tls`, and `auth_type`
  (`username` with `username`/`password`, or `cert` with a client certificate);
- **`telemetry`** — `enabled`, `frequency` (seconds, **minimum 5**) and `items`
  (`"default"`, **`"all"`**, or an explicit list). It publishes on a topic under
  the prefix **`qvantum/`**, and `get_telemetry_inventory` returns the catalogue.
- `command` — allowlisted commands with `heatpump_command_id`, version 1 and an
  `arguments` object; responses carry a `status_code`. `enabled` is a required
  boolean here, so commands are opt-in.
- **`events`** — `enabled`, and its topics. `events/control_parameters` publishes
  the control parameters — the same 49 names the parameters API reads
  (`source_pump`, `desired_room_temp`, `curve_at_minus_30` … `smart_grid_enabled`)
  — together with a `%Y-%m-%dT%H:%M:%SZ` timestamp and `uptime_us`.

The catalogue is a list of **named items**, not the whole snapshot this POC's
stand-in publishes — 60 items in `1848e2e43`, six more than `898953566`. The
additions are the ones a heat meter would want and the Modbus map does not have:
`bt24` crank-case temperature, `bp1_pressure`/`bp2_pressure` (low- and high-side
refrigerant pressure, unitless in the catalogue) and `compressor_current`, beside
`bt12` (external heating supply) and `dm` (degree minutes). A renamed
description is not a renamed item: `bt14`/`bt15` are now "heat-source
inlet/outlet", the heater relays carry their wattage, and
`compressormeasuredspeed` is documented as the **maximum** of two units rather
than their sum.

`client/mqtt/inventory.py` maps the items whose names are **not** the canonical
ones (`compressor_energy_kwh` → `compressorenergy`, `qn8_position` →
`qn8position`, `dm` → `degree_minute`, …) and passes anything it does not know
straight through, so a firmware that adds an item needs no release here. The
client accepts both payload shapes: this POC's `{"metrics", "settings"}` snapshot
and the firmware's flat item document.

**What the feed does not carry: a thermal power or a COP.** It repeats
`compressor_power` (input 94, validated against a whole-house meter at a 0.95
ratio over 135 defrosts) but there is no heating-output item, so the
heating-energy question — the counter reads below the compressor's own input —
is untouched by this transport.

Still blocked: QT (Qvantum's own) configuration cannot be read or written
through the third-party MQTT API, advanced settings need `AL30`/`AL20` with a
service-access token, and some commands require `developer_mode`.
