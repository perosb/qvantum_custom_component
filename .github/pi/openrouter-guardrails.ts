import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Provider routing (zdr, price sort, provider order, data_collection) is
// fetched from the OpenRouter account preset and injected INLINE into each
// request (`provider` object). OpenRouter no longer honors a `preset` slug in
// the chat-completions body (removed from ChatRequest; silently ignored), so
// inlining is the only reliable way to apply the preset's routing rules.
// Override locally with OPENROUTER_PRESET, or set it empty to disable.
const DEFAULT_OPENROUTER_PRESET = "perosb";

// OpenRouter's advertised max_completion_tokens assumes a provider with the
// full advertised context (often 1M). Clamp the outgoing request; override via
// OPENROUTER_MAX_COMPLETION_TOKENS.
const DEFAULT_MAX_COMPLETION_TOKENS_CAP = 131_072;

const BASE_URL = "https://openrouter.ai/api/v1";

interface ProviderConfig {
  order?: string[];
  sort?: unknown;
  zdr?: boolean;
  data_collection?: string;
  allow_fallbacks?: boolean;
  preferred_min_throughput?: unknown;
  [k: string]: unknown;
}

function positiveNumber(value: unknown, fallback: number): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

async function fetchPresetProvider(preset: string): Promise<ProviderConfig | undefined> {
  const key = process.env.OPENROUTER_API_KEY;
  if (!key) return undefined;
  const res = await fetch(`${BASE_URL}/presets/${encodeURIComponent(preset)}`, {
    headers: { Authorization: `Bearer ${key}` },
  });
  if (!res.ok) throw new Error(`preset fetch ${res.status}`);
  const j = (await res.json()) as {
    data?: { designated_version?: { config?: { provider?: ProviderConfig } } };
  };
  return j.data?.designated_version?.config?.provider;
}

export default function (pi: ExtensionAPI) {
  let providerConfig: ProviderConfig | undefined;
  let providerFetch: Promise<ProviderConfig | undefined> | undefined;

  // Hämta preset-config en gång per process (lazily, vid första anropet).
  function getProviderConfig(preset: string): Promise<ProviderConfig | undefined> {
    providerFetch ??= fetchPresetProvider(preset)
      .then((cfg) => {
        providerConfig = cfg;
        return cfg;
      })
      .catch(() => undefined);
    return providerFetch;
  }

  pi.on("before_provider_request", async (event, ctx) => {
    if (ctx.model?.provider !== "openrouter") return;
    if (
      typeof event.payload !== "object" ||
      event.payload === null ||
      Array.isArray(event.payload)
    ) {
      return;
    }

    const payload = { ...(event.payload as Record<string, unknown>) };
    const preset = (process.env.OPENROUTER_PRESET ?? DEFAULT_OPENROUTER_PRESET).trim();
    const cap = positiveNumber(
      process.env.OPENROUTER_MAX_COMPLETION_TOKENS,
      DEFAULT_MAX_COMPLETION_TOKENS_CAP,
    );

    let routing = "(inline: none)";
    if (preset) {
      const cfg = providerConfig ?? (await getProviderConfig(preset));
      if (cfg) {
        payload.provider = cfg;
        delete payload.preset; // ignoreras av OR numera; skicka inte död konfig
        routing = `inline(${(cfg.order ?? []).slice(0, 3).join(",")}${(cfg.order?.length ?? 0) > 3 ? ",…" : ""})`;
      } else {
        // Fallback: skicka slugen (nyttlös idag, men bättre än inget om OR
        // återinför stödet).
        payload.preset = preset;
        routing = `preset:${preset} (fetch misslyckades – slug-fallback)`;
      }
    }

    for (const field of ["max_completion_tokens", "max_tokens"] as const) {
      const value = payload[field];
      if (typeof value === "number" && value > cap) payload[field] = cap;
    }

    if (process.env.PI_DEBUG_OR_PAYLOAD) {
      console.error(
        `[openrouter-guardrails] model=${ctx.model?.id} routing=${routing} cap=${cap} max=${payload.max_completion_tokens ?? payload.max_tokens ?? "(unset)"}`,
      );
    }
    return payload;
  });
}
