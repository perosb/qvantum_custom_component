/**
 * Qvantum adaptive heating-curve card.
 *
 * Visualizes the custom heating-curve module of the Qvantum integration:
 * frozen baseline, computed shadow curve, the seven points actually written
 * to the pump, the shared adjustment term breakdown and the control state.
 *
 * Loaded from `/qvantum/qvantum-curve-card.js` (registered by the
 * integration) or copied to `config/www/` and referenced as
 * `/local/qvantum-curve-card.js`. No build step, no dependencies.
 */

const CARD_VERSION = "1.0.0";

/** Metric keys (unique_id suffix) → outdoor temperature for the seven points. */
const POINT_METRICS = [
  { metric: "adaptive_curve_30", outdoor: 30 },
  { metric: "adaptive_curve_20", outdoor: 20 },
  { metric: "adaptive_curve_10", outdoor: 10 },
  { metric: "adaptive_curve_0", outdoor: 0 },
  { metric: "adaptive_curve_minus_10", outdoor: -10 },
  { metric: "adaptive_curve_minus_20", outdoor: -20 },
  { metric: "adaptive_curve_minus_30", outdoor: -30 },
];

const ADJUSTMENT_METRIC = "adaptive_curve_adjustment";
const DEVIATION_METRIC = "adaptive_curve_deviation";
const SOLAR_METRIC = "adaptive_curve_solar_model";
const SWITCH_METRIC = "adaptive_curve_control";
const SELECT_METRIC = "curve_type_heating";

const KNOWN_METRICS = [
  ...POINT_METRICS.map((p) => p.metric),
  ADJUSTMENT_METRIC,
  DEVIATION_METRIC,
  SOLAR_METRIC,
  SWITCH_METRIC,
  SELECT_METRIC,
];

const STRINGS = {
  en: {
    baseline: "Baseline (frozen)",
    shadow: "Computed (shadow)",
    pump: "Pump table",
    band: (n) => `±${n} °C write band`,
    operating: "Operating point",
    active: "Active",
    shadowMode: "Shadow",
    ready: "Ready",
    notReady: "Not ready",
    noData: "No data",
    blocker: "blocker",
    pumpUser: "Pump: User defined",
    pumpAuto: "Pump: Auto",
    clamped: "Clamped to supply limits",
    capped: "Indoor cap",
    trust: "Solar trust",
    learned: "Baseline learned",
    tOutdoor: "Outdoor",
    tNight: "Night/day",
    tSolar: "Solar",
    tLoad: "Load",
    tTrims: "Trims",
    tTotal: "Total",
    outdoorAxis: "Outdoor temperature (°C)",
    waiting: "Waiting for curve data…",
    notFound:
      "Qvantum adaptive curve entities not found. This card is Modbus-only.",
    hours: "h",
  },
  sv: {
    baseline: "Baslinje (fryst)",
    shadow: "Beräknad (skugga)",
    pump: "Pumpens tabell",
    band: (n) => `±${n} °C skrivband`,
    operating: "Driftpunkt",
    active: "Aktiv",
    shadowMode: "Skugga",
    ready: "Redo",
    notReady: "Inte redo",
    noData: "Ingen data",
    blocker: "blockerare",
    pumpUser: "Pump: User defined",
    pumpAuto: "Pump: Auto",
    clamped: "Klämd mot gränser",
    capped: "Inomhustak",
    trust: "Sol-tilltro",
    learned: "Baslinje inlärd",
    tOutdoor: "Ute",
    tNight: "Natt/dag",
    tSolar: "Sol",
    tLoad: "Last",
    tTrims: "Trimmar",
    tTotal: "Totalt",
    outdoorAxis: "Utetemperatur (°C)",
    waiting: "Väntar på kurvdata…",
    notFound:
      "Qvantum adaptiva kurventiteter hittades inte. Kortet är endast för Modbus.",
    hours: "h",
  },
};

const esc = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c],
  );

const num = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const attr = (state, key) => {
  if (!state || !state.attributes) return null;
  return state.attributes[key];
};

const fmt = (value, digits = 1) =>
  value === null || value === undefined ? "–" : Number(value).toFixed(digits);

const signed = (value, digits = 1) =>
  value === null || value === undefined
    ? "–"
    : `${value >= 0 ? "+" : ""}${Number(value).toFixed(digits)}`;

/** Evenly spaced, human-friendly ticks. */
function niceTicks(min, max, count) {
  if (!(max > min) || count < 2) return [min];
  const raw = (max - min) / (count - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + 1e-9; v += step)
    ticks.push(Math.round(v * 100) / 100);
  return ticks;
}

class QvantumCurveCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = undefined;
    this._sig = "";
    this._width = 520;
    this._observer = null;
  }

  static get version() {
    return CARD_VERSION;
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = {
      title: "Värmekurva",
      entity: "",
      min_outdoor: -30,
      max_outdoor: 30,
      min_supply: null,
      max_supply: null,
      band: 1,
      show_baseline: true,
      show_shadow: true,
      show_pump: true,
      show_band: true,
      show_grid: true,
      operating_point: null,
      entities: null,
      language: "auto",
      ...config,
    };
    this._sig = "";
  }

  set hass(hass) {
    this._hass = hass;
    this._maybeRender();
  }

  connectedCallback() {
    if (!this._observer && typeof ResizeObserver !== "undefined") {
      this._observer = new ResizeObserver((entries) => {
        const width = entries[0]?.contentRect?.width;
        if (width && Math.abs(width - this._width) > 2) {
          this._width = width;
          this._render();
        }
      });
      this._observer.observe(this);
    }
  }

  disconnectedCallback() {
    if (this._observer) {
      this._observer.disconnect();
      this._observer = null;
    }
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _strings() {
    const lang = this._config.language;
    const active =
      lang === "auto" ? (this._hass?.language || "en").slice(0, 2) : lang;
    return STRINGS[active] || STRINGS.en;
  }

  _registryEntries() {
    const entities = this._hass?.entities;
    if (!entities) return [];
    return Object.values(entities);
  }

  _metricOf(entry) {
    const uid = entry?.unique_id;
    if (!uid || !uid.startsWith("qvantum_")) return null;
    for (const metric of KNOWN_METRICS) {
      if (uid.startsWith(`qvantum_${metric}_`)) return metric;
    }
    return null;
  }

  /** Resolve the anchor entity and the sibling map of metric → entity_id. */
  _resolveEntities() {
    const hass = this._hass;
    if (!hass) return { anchor: null, map: {} };
    const entries = this._registryEntries();

    let anchor = this._config.entity || "";
    if (anchor && !hass.states[anchor]) {
      const byConfig = entries.find((e) => e.entity_id === anchor);
      if (!byConfig) anchor = "";
    }
    if (!anchor) {
      const hit = entries.find((e) => this._metricOf(e) === ADJUSTMENT_METRIC);
      if (hit) anchor = hit.entity_id;
    }
    if (!anchor) {
      anchor =
        Object.keys(hass.states).find((id) =>
          id.includes("adaptive_curve_adjustment"),
        ) || null;
    }
    if (!anchor) return { anchor: null, map: {} };

    const anchorEntry = entries.find((e) => e.entity_id === anchor);
    const hpid = anchorEntry?.unique_id?.startsWith(
      `qvantum_${ADJUSTMENT_METRIC}_`,
    )
      ? anchorEntry.unique_id.slice(`qvantum_${ADJUSTMENT_METRIC}_`.length)
      : null;
    const deviceId = anchorEntry?.device_id || null;

    const map = {};
    // Explicit overrides win.
    if (this._config.entities) {
      for (const [metric, entityId] of Object.entries(this._config.entities)) {
        if (entityId) map[metric] = entityId;
      }
    }
    for (const entry of entries) {
      const metric = this._metricOf(entry);
      if (!metric) continue;
      const sameDevice =
        !hpid && !deviceId
          ? true // single config entry: a metric match is unambiguous
          : (hpid && entry.unique_id.endsWith(`_${hpid}`)) ||
            (deviceId && entry.device_id === deviceId);
      if (sameDevice && !map[metric]) map[metric] = entry.entity_id;
    }
    return { anchor, map };
  }

  _signature(anchor, map) {
    const hass = this._hass;
    const parts = [anchor || "", this._config.language, this._width];
    for (const metric of Object.keys(map)) {
      const state = hass.states[map[metric]];
      if (!state) {
        parts.push(`${metric}:missing`);
        continue;
      }
      parts.push(
        `${metric}:${state.state}:${JSON.stringify(state.attributes)}`,
      );
    }
    return parts.join("|");
  }

  _maybeRender() {
    if (!this._hass) return;
    const { anchor, map } = this._resolveEntities();
    const sig = this._signature(anchor, map);
    if (sig === this._sig) return;
    this._sig = sig;
    this._renderModel(anchor, map);
  }

  _render() {
    if (!this._hass) return;
    const { anchor, map } = this._resolveEntities();
    this._renderModel(anchor, map);
  }

  _renderModel(anchor, map) {
    const t = this._strings();
    const root = this.shadowRoot;

    if (!anchor) {
      root.innerHTML = `
        <style>${this._style()}</style>
        <ha-card>
          <div class="pad muted notfound">${esc(t.notFound)}</div>
        </ha-card>`;
      return;
    }

    const model = this._collect(map);
    const chart = model.pointCount
      ? this._svg(model)
      : `<div class="pad muted waiting">${esc(t.waiting)}${
          model.blocker
            ? ` <span class="blocker">(${esc(t.blocker)}: ${esc(model.blocker)})</span>`
            : ""
        }</div>`;

    root.innerHTML = `
      <style>${this._style()}</style>
      <ha-card>
        <div class="content">
          <div class="header">
            <div class="title">${esc(this._config.title)}</div>
            ${this._legend(model, t)}
          </div>
          ${chart}
          ${this._status(model, t)}
          ${this._terms(model, t)}
        </div>
      </ha-card>`;
  }

  _collect(map) {
    const hass = this._hass;
    const state = (metric) =>
      map[metric] ? hass.states[map[metric]] : undefined;

    const points = POINT_METRICS.map((p) => {
      const s = state(p.metric);
      return {
        outdoor: p.outdoor,
        value: num(s?.state),
        baseline: num(attr(s, "baseline")),
        trim: num(attr(s, "trim")) ?? 0,
      };
    }).sort((a, b) => a.outdoor - b.outdoor);

    const sel = state(SELECT_METRIC);
    const pump = Array.isArray(attr(sel, "points"))
      ? attr(sel, "points")
          .filter((p) => Array.isArray(p) && p.length >= 2)
          .map((p) => ({ outdoor: Number(p[0]), value: Number(p[1]) }))
          .sort((a, b) => a.outdoor - b.outdoor)
      : [];

    const adj = state(ADJUSTMENT_METRIC);
    const dev = state(DEVIATION_METRIC);
    const solar = state(SOLAR_METRIC);
    const sw = state(SWITCH_METRIC);

    const op = this._operatingPoint();

    const shadowValues = points.map((p) => p.value).filter((v) => v !== null);
    const pointCount = shadowValues.length;
    const known = (metric) => {
      const s = map[metric] ? hass.states[map[metric]] : undefined;
      return !!s && s.state !== "unavailable" && s.state !== "unknown";
    };

    return {
      points: points.filter((p) => p.value !== null),
      allPoints: points,
      pump,
      pointCount,
      known: {
        switch: known(SWITCH_METRIC),
        deviation: known(DEVIATION_METRIC),
        adjustment: known(ADJUSTMENT_METRIC),
        select: known(SELECT_METRIC),
        solar: known(SOLAR_METRIC),
      },
      adjustment: {
        total: num(adj?.state),
        outdoor: num(attr(adj, "outdoor_c")),
        night: num(attr(adj, "night_day_c")),
        solar: num(attr(adj, "solar_c")),
        load: num(attr(adj, "load_c")),
        trims: attr(adj, "trims"),
        clamped: attr(adj, "clamped") === true,
        capped: attr(adj, "capped_by_indoor") === true,
      },
      ready: attr(dev, "ready") === true,
      shadow: attr(dev, "shadow") === true,
      blocker: attr(dev, "blocker"),
      baselineAuto: attr(dev, "baseline_auto") === true,
      learnedHours: num(attr(dev, "baseline_learned_hours")),
      learnedMin: num(attr(dev, "baseline_outdoor_min_c")),
      learnedMax: num(attr(dev, "baseline_outdoor_max_c")),
      trust: num(solar?.state),
      active: sw?.state === "on",
      operating: op,
    };
  }

  _operatingPoint() {
    const conf = this._config.operating_point;
    if (!conf || !conf.outdoor || !conf.supply) return null;
    const outdoor = num(this._hass.states[conf.outdoor]?.state);
    const supply = num(this._hass.states[conf.supply]?.state);
    if (outdoor === null || supply === null) return null;
    return { outdoor, supply };
  }

  _style() {
    return `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .content { padding: 12px 16px 16px; }
      .pad { padding: 24px 16px; }
      .muted { color: var(--secondary-text-color); }
      .waiting, .notfound { text-align: center; }
      .header {
        display: flex; align-items: baseline; justify-content: space-between;
        flex-wrap: wrap; gap: 6px 16px; margin-bottom: 6px;
      }
      .title { font-size: 1.05rem; font-weight: 500; color: var(--primary-text-color); }
      .legend {
        display: flex; flex-wrap: wrap; gap: 4px 12px;
        font-size: 0.75rem; color: var(--secondary-text-color);
      }
      .legend span { display: inline-flex; align-items: center; gap: 5px; }
      .swatch { width: 14px; height: 0; border-top-width: 3px; border-top-style: solid; }
      .swatch.dash { border-top-style: dashed; }
      .swatch.dot { width: 9px; height: 9px; border: 0; border-radius: 50%; }
      svg { display: block; width: 100%; height: auto; }
      .axis { fill: var(--secondary-text-color); font-size: 11px; }
      .axis-title { fill: var(--secondary-text-color); font-size: 11px; }
      .status {
        display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px;
        font-size: 0.8rem;
      }
      .chip {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 2px 8px; border-radius: 12px;
        background: var(--secondary-background-color, rgba(127,127,127,0.12));
        color: var(--primary-text-color); white-space: nowrap;
      }
      .chip.ok { background: rgba(67,160,71,0.18); }
      .chip.warn { background: rgba(251,192,45,0.24); }
      .chip.bad { background: rgba(229,57,53,0.18); }
      .chip .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
      .terms { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
      .term {
        display: inline-flex; flex-direction: column; align-items: center;
        min-width: 54px; padding: 4px 8px; border-radius: 8px;
        background: var(--secondary-background-color, rgba(127,127,127,0.12));
      }
      .term .label { font-size: 0.68rem; color: var(--secondary-text-color); }
      .term .value { font-size: 0.85rem; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
      .term.total { background: color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent); }
      .footer {
        margin-top: 8px; font-size: 0.72rem; color: var(--secondary-text-color);
        display: flex; flex-wrap: wrap; gap: 4px 14px;
      }
    `;
  }

  _legend(model, t) {
    const items = [];
    if (this._config.show_baseline)
      items.push(
        `<span><i class="swatch dash" style="border-color:var(--secondary-text-color)"></i>${esc(
          t.baseline,
        )}</span>`,
      );
    if (this._config.show_shadow)
      items.push(
        `<span><i class="swatch" style="border-color:var(--primary-color,#03a9f4)"></i>${esc(
          t.shadow,
        )}</span>`,
      );
    if (this._config.show_pump && model.pump.length)
      items.push(
        `<span><i class="swatch dot" style="background:var(--error-color,#db4437)"></i>${esc(
          t.pump,
        )}</span>`,
      );
    if (this._config.show_band && model.pointCount)
      items.push(
        `<span><i class="swatch" style="border-color:color-mix(in srgb, var(--primary-color,#03a9f4) 40%, transparent)"></i>${esc(
          t.band(this._config.band),
        )}</span>`,
      );
    if (model.operating)
      items.push(
        `<span><i class="swatch dot" style="background:var(--accent-color,#ff9800)"></i>${esc(
          t.operating,
        )}</span>`,
      );
    return `<div class="legend">${items.join("")}</div>`;
  }

  _yDomain(model) {
    const values = [];
    for (const p of model.allPoints) {
      if (p.value !== null) values.push(p.value);
      if (p.baseline !== null) values.push(p.baseline);
    }
    for (const p of model.pump) values.push(p.value);
    if (model.operating) values.push(model.operating.supply);
    if (this._config.min_supply !== null) values.push(this._config.min_supply);
    if (this._config.max_supply !== null) values.push(this._config.max_supply);
    if (!values.length) return [10, 80];
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    lo -= 3;
    hi += 3;
    return [lo, hi];
  }

  _svg(model) {
    const W = Math.max(280, Math.round(this._width || 520));
    const H = 260;
    const pad = { l: 44, r: 14, t: 12, b: 34 };
    const plotW = W - pad.l - pad.r;
    const plotH = H - pad.t - pad.b;
    const xmin = num(this._config.min_outdoor) ?? -30;
    const xmax = num(this._config.max_outdoor) ?? 30;
    const [ymin, ymax] = this._yDomain(model);
    const X = (o) => pad.l + ((o - xmin) / (xmax - xmin)) * plotW;
    const Y = (v) => pad.t + ((ymax - v) / (ymax - ymin)) * plotH;

    const parts = [];
    parts.push(
      `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">`,
    );

    const grid = this._config.show_grid;
    const yTicks = niceTicks(ymin, ymax, 6);
    for (const v of yTicks) {
      const y = Y(v);
      if (y < pad.t - 1 || y > pad.t + plotH + 1) continue;
      if (grid)
        parts.push(
          `<line x1="${pad.l}" y1="${y.toFixed(
            1,
          )}" x2="${pad.l + plotW}" y2="${y.toFixed(
            1,
          )}" stroke="var(--divider-color, rgba(127,127,127,0.3))" stroke-width="1"/>`,
        );
      parts.push(
        `<text class="axis" x="${pad.l - 6}" y="${(y + 3.5).toFixed(
          1,
        )}" text-anchor="end">${v}</text>`,
      );
    }
    const xTicks = niceTicks(xmin, xmax, 7);
    for (const v of xTicks) {
      const x = X(v);
      if (x < pad.l - 1 || x > pad.l + plotW + 1) continue;
      if (grid)
        parts.push(
          `<line x1="${x.toFixed(1)}" y1="${pad.t}" x2="${x.toFixed(
            1,
          )}" y2="${pad.t + plotH}" stroke="var(--divider-color, rgba(127,127,127,0.3))" stroke-width="1"/>`,
        );
      parts.push(
        `<text class="axis" x="${x.toFixed(1)}" y="${
          pad.t + plotH + 15
        }" text-anchor="middle">${v}</text>`,
      );
    }
    parts.push(
      `<text class="axis-title" x="${pad.l + plotW / 2}" y="${
        H - 4
      }" text-anchor="middle">${esc(this._strings().outdoorAxis)}</text>`,
    );

    const line = (pts, accessor) =>
      pts
        .filter((p) => accessor(p) !== null)
        .map(
          (p, i) =>
            `${i ? "L" : "M"}${X(p.outdoor).toFixed(1)},${Y(
              accessor(p),
            ).toFixed(1)}`,
        )
        .join(" ");

    // Write band around the shadow curve.
    if (this._config.show_band && model.points.length) {
      const band = num(this._config.band) ?? 1;
      const upper = model.points.map(
        (p) => `${X(p.outdoor).toFixed(1)},${Y(p.value + band).toFixed(1)}`,
      );
      const lower = [...model.points]
        .reverse()
        .map(
          (p) => `${X(p.outdoor).toFixed(1)},${Y(p.value - band).toFixed(1)}`,
        );
      parts.push(
        `<polygon points="${upper.concat(lower).join(" ")}" fill="color-mix(in srgb, var(--primary-color, #03a9f4) 14%, transparent)" stroke="none"/>`,
      );
    }

    // Frozen baseline.
    if (this._config.show_baseline) {
      parts.push(
        `<path d="${line(model.allPoints, (p) => p.baseline)}" fill="none" stroke="var(--secondary-text-color)" stroke-width="1.6" stroke-dasharray="5 4"/>`,
      );
    }

    // Pump's written table.
    if (this._config.show_pump) {
      for (const p of model.pump) {
        parts.push(
          `<rect x="${(X(p.outdoor) - 3).toFixed(1)}" y="${(
            Y(p.value) - 3
          ).toFixed(1)}" width="6" height="6" transform="rotate(45 ${X(
            p.outdoor,
          ).toFixed(1)} ${Y(p.value).toFixed(
            1,
          )})" fill="var(--error-color, #db4437)"/>`,
        );
      }
    }

    // Computed shadow curve.
    if (this._config.show_shadow && model.points.length) {
      parts.push(
        `<path d="${line(model.points, (p) => p.value)}" fill="none" stroke="var(--primary-color, #03a9f4)" stroke-width="2.4" stroke-linejoin="round"/>`,
      );
      for (const p of model.points) {
        parts.push(
          `<circle cx="${X(p.outdoor).toFixed(1)}" cy="${Y(p.value).toFixed(
            1,
          )}" r="3.4" fill="var(--primary-color, #03a9f4)"/>`,
        );
      }
    }

    // Operating point.
    if (model.operating) {
      const ox = X(model.operating.outdoor);
      const oy = Y(model.operating.supply);
      parts.push(
        `<circle cx="${ox.toFixed(1)}" cy="${oy.toFixed(
          1,
        )}" r="5.5" fill="none" stroke="var(--accent-color, #ff9800)" stroke-width="2.4"/>`,
        `<circle cx="${ox.toFixed(1)}" cy="${oy.toFixed(
          1,
        )}" r="1.8" fill="var(--accent-color, #ff9800)"/>`,
      );
    }

    parts.push("</svg>");
    return `<div class="chart">${parts.join("")}</div>`;
  }

  _status(model, t) {
    const chips = [];
    chips.push(
      model.known.switch
        ? `<span class="chip ${model.active ? "ok" : ""}"><i class="dot"></i>${esc(
            model.active ? t.active : t.shadowMode,
          )}</span>`
        : `<span class="chip">${esc(t.noData)}</span>`,
    );
    if (model.known.deviation) {
      chips.push(
        `<span class="chip ${model.ready ? "ok" : "warn"}">${esc(
          model.ready ? t.ready : t.notReady,
        )}${model.blocker ? `: ${esc(t.blocker)} ${esc(model.blocker)}` : ""}</span>`,
      );
      chips.push(
        `<span class="chip ${model.shadow ? "" : "ok"}">${esc(
          model.shadow ? t.pumpAuto : t.pumpUser,
        )}</span>`,
      );
    }
    if (model.known.adjustment && model.adjustment.clamped)
      chips.push(`<span class="chip warn">${esc(t.clamped)}</span>`);
    if (model.known.adjustment && model.adjustment.capped)
      chips.push(`<span class="chip warn">${esc(t.capped)}</span>`);
    if (model.known.solar && model.trust !== null)
      chips.push(
        `<span class="chip">${esc(t.trust)} ${fmt(model.trust, 0)} %</span>`,
      );
    return `<div class="status">${chips.join("")}</div>`;
  }

  _terms(model, t) {
    const a = model.adjustment;
    const trimSum =
      a.trims && typeof a.trims === "object"
        ? Object.values(a.trims).reduce((acc, v) => acc + (num(v) ?? 0), 0)
        : 0;
    const term = (label, value, cls = "") =>
      `<span class="term ${cls}"><span class="label">${esc(
        label,
      )}</span><span class="value">${esc(signed(value))} K</span></span>`;
    const terms = [
      term(t.tOutdoor, a.outdoor),
      term(t.tNight, a.night),
      term(t.tSolar, a.solar),
      term(t.tLoad, a.load),
      term(t.tTrims, trimSum),
      term(t.tTotal, a.total, "total"),
    ];
    const footer = [];
    if (model.learnedHours !== null) {
      let learned = `${esc(t.learned)}: ${fmt(model.learnedHours, 0)} ${esc(
        t.hours,
      )}`;
      if (model.learnedMin !== null && model.learnedMax !== null) {
        learned += ` (${fmt(model.learnedMin, 0)}…${fmt(
          model.learnedMax,
          0,
        )} °C)`;
      }
      footer.push(learned);
    }
    return `<div class="terms">${terms.join("")}</div>${
      footer.length ? `<div class="footer">${footer.join("")}</div>` : ""
    }`;
  }
}

if (!customElements.get("qvantum-curve-card")) {
  customElements.define("qvantum-curve-card", QvantumCurveCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "qvantum-curve-card")) {
  window.customCards.push({
    type: "qvantum-curve-card",
    name: "Qvantum Curve Card",
    description:
      "Adaptive Qvantum heating curve: baseline, shadow, pump table and adjustment terms.",
    preview: false,
  });
}

console.info(
  `%c QVANTUM-CURVE-CARD %c v${CARD_VERSION} `,
  "color: white; background: #03a9f4; font-weight: 700;",
  "color: #03a9f4; background: white; font-weight: 700;",
);
