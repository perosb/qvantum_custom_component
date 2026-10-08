import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Provider routing (zdr, price sort, provider order, data_collection) lives in
// the OpenRouter account preset, so this extension only references it. Override
// locally with OPENROUTER_PRESET, or set it empty to disable.
const DEFAULT_OPENROUTER_PRESET = "perosb";

// OpenRouter's advertised max_completion_tokens assumes a provider with the
// full advertised context (often 1M). Requesting that many output tokens
// filters out cheaper providers with smaller context windows before the
// provider order in the preset is even considered (Morph wins over Relace for
// z-ai/glm-*-flash). Clamp the outgoing request instead; override via
// OPENROUTER_MAX_COMPLETION_TOKENS.
const DEFAULT_MAX_COMPLETION_TOKENS_CAP = 131_072;

function positiveNumber(value: unknown, fallback: number): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

export default function (pi: ExtensionAPI) {
  pi.on("before_provider_request", (event, ctx) => {
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

    if (preset) payload.preset = preset;
    for (const field of ["max_completion_tokens", "max_tokens"] as const) {
      const value = payload[field];
      if (typeof value === "number" && value > cap) payload[field] = cap;
    }

    if (process.env.PI_DEBUG_OR_PAYLOAD) {
      console.error(
        `[openrouter-guardrails] model=${ctx.model?.id} preset=${preset || "(none)"} cap=${cap} max=${payload.max_completion_tokens ?? payload.max_tokens ?? "(unset)"}`,
      );
    }
    return payload;
  });
}
