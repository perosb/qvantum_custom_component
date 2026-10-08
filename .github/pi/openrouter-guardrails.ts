import type { ExtensionAPI, ProviderConfig } from "@earendil-works/pi-coding-agent";

const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";
const MODEL_LIST_URL = `${OPENROUTER_BASE_URL}/models/user`;

// Provider routing (zdr, price sort, provider order, data_collection) lives in
// the OpenRouter account preset, so the extension only references it. Override
// locally with OPENROUTER_PRESET, or set it empty to disable.
const DEFAULT_OPENROUTER_PRESET = "perosb";
const DEFAULT_CONTEXT_WINDOW = 128_000;

// OpenRouter's advertised max_completion_tokens assumes a provider with the
// full advertised context (often 1M). Requesting that many output tokens
// filters out cheaper providers with smaller context windows before the
// provider `order` is even considered (e.g. Morph wins over Relace for
// z-ai/glm-*-flash). Cap it so mid-size providers stay eligible; override via
// OPENROUTER_MAX_COMPLETION_TOKENS.
const DEFAULT_MAX_COMPLETION_TOKENS_CAP = 131_072;

type OpenRouterModel = {
  id?: unknown;
  name?: unknown;
  context_length?: unknown;
  architecture?: {
    input_modalities?: unknown;
    output_modalities?: unknown;
  };
  pricing?: {
    prompt?: unknown;
    completion?: unknown;
    input_cache_read?: unknown;
    input_cache_write?: unknown;
  };
  top_provider?: {
    context_length?: unknown;
    max_completion_tokens?: unknown;
  };
  supported_parameters?: unknown;
};

function positiveNumber(value: unknown, fallback: number): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function pricePerMillion(value: unknown, fallback = 0): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number >= 0 ? number * 1_000_000 : fallback;
}

function toChatModels(payload: unknown): ProviderConfig["models"] {
  if (typeof payload !== "object" || payload === null || !Array.isArray((payload as { data?: unknown }).data)) {
    throw new Error("OpenRouter returned an invalid user-model catalog");
  }

  return ((payload as { data: OpenRouterModel[] }).data).flatMap((model) => {
    if (typeof model.id !== "string" || typeof model.name !== "string") return [];
    // This endpoint is the OpenRouter account's allowlist; do not filter by model ID prefix.
    const inputModalities = Array.isArray(model.architecture?.input_modalities)
      ? model.architecture.input_modalities
      : [];
    const outputModalities = Array.isArray(model.architecture?.output_modalities)
      ? model.architecture.output_modalities
      : [];
    if (!inputModalities.includes("text") || !outputModalities.includes("text")) return [];

    const contextWindow = positiveNumber(
      model.top_provider?.context_length ?? model.context_length,
      DEFAULT_CONTEXT_WINDOW,
    );
    // top_provider.max_completion_tokens is null for some models (e.g.
    // "openrouter/auto"). Falling back to the full context window makes pi send
    // a max_completion_tokens larger than the context of the endpoint the auto
    // router resolves to, which OpenRouter rejects with HTTP 400. Omit maxTokens
    // instead so pi falls back to its own safe default (16 384).
    const maxCompletionTokens = positiveNumber(model.top_provider?.max_completion_tokens, 0);
    const maxTokensCap = positiveNumber(
      process.env.OPENROUTER_MAX_COMPLETION_TOKENS,
      DEFAULT_MAX_COMPLETION_TOKENS_CAP,
    );
    const supportedParameters = Array.isArray(model.supported_parameters)
      ? model.supported_parameters
      : [];
    const input: ("text" | "image")[] = inputModalities.includes("image")
      ? ["text", "image"]
      : ["text"];

    const chatModel = {
      id: model.id,
      name: model.name,
      api: "openai-completions" as const,
      baseUrl: OPENROUTER_BASE_URL,
      input,
      reasoning: ["reasoning", "reasoning_effort", "include_reasoning"].some((parameter) =>
        supportedParameters.includes(parameter),
      ),
      contextWindow,
      ...(maxCompletionTokens > 0
        ? { maxTokens: Math.min(maxCompletionTokens, maxTokensCap, contextWindow) }
        : {}),
      cost: {
        input: pricePerMillion(model.pricing?.prompt),
        output: pricePerMillion(model.pricing?.completion),
        cacheRead: pricePerMillion(model.pricing?.input_cache_read, pricePerMillion(model.pricing?.prompt)),
        cacheWrite: pricePerMillion(model.pricing?.input_cache_write),
      },
    };

    return [chatModel];
  });
}

async function fetchAllowedModels(apiKey: string, signal?: AbortSignal) {
  const timeout = AbortSignal.timeout(12_000);
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const response = await fetch(MODEL_LIST_URL, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: requestSignal,
  });
  if (!response.ok) {
    throw new Error(`OpenRouter model access lookup failed (HTTP ${response.status})`);
  }
  return toChatModels(await response.json());
}

export default async function (pi: ExtensionAPI) {
  const apiKey = process.env.OPENROUTER_API_KEY?.trim();
  let allowedModels: ProviderConfig["models"] = [];

  if (apiKey) {
    try {
      allowedModels = await fetchAllowedModels(apiKey);
    } catch (error) {
      console.warn(
        `[openrouter-guardrails] Could not load the OpenRouter allowlist: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  const provider: ProviderConfig = {
    name: "OpenRouter",
    baseUrl: OPENROUTER_BASE_URL,
    api: "openai-completions",
    apiKey: "$OPENROUTER_API_KEY",
    models: allowedModels,
    refreshModels: async (context) => {
      if (!context.allowNetwork) return allowedModels;

      const key = process.env.OPENROUTER_API_KEY?.trim() ||
        (context.credential?.type === "api_key" ? context.credential.key : undefined);
      if (!key) {
        allowedModels = [];
        return [];
      }

      try {
        allowedModels = await fetchAllowedModels(key, context.signal);
        return allowedModels;
      } catch (error) {
        // Keep the previously loaded catalog on transient failures — wiping it
        // (combined with session_start emptying every other provider) would
        // leave zero selectable models.
        console.warn(
          `[openrouter-guardrails] Could not refresh the OpenRouter allowlist: ${error instanceof Error ? error.message : String(error)}`,
        );
        return allowedModels;
      }
    },
  };

  pi.registerProvider("openrouter", provider);

  // Apply the OpenRouter account preset (routing + privacy policy) to every
  // request, regardless of the selected allowlisted model.
  pi.on("before_provider_request", (event, ctx) => {
    if (
      ctx.model?.provider !== "openrouter" ||
      typeof event.payload !== "object" ||
      event.payload === null ||
      Array.isArray(event.payload)
    ) {
      return;
    }
    const preset = (process.env.OPENROUTER_PRESET ?? DEFAULT_OPENROUTER_PRESET).trim();
    if (!preset) return;
    const payload = event.payload as Record<string, unknown>;
    if (process.env.PI_DEBUG_OR_PAYLOAD) {
      console.error(
        `[openrouter-guardrails] model=${ctx.model?.id} payload-model=${payload.model} preset=${preset}`,
      );
    }
    return {
      ...payload,
      preset,
    };
  });

  // Pi's default catalog includes models for providers even when you have no
  // credentials for them. Replace those catalogs with empty lists so /model
  // only offers the OpenRouter allowlist.
  pi.on("session_start", (_event, ctx) => {
    const otherProviders = new Set(
      ctx.modelRegistry.getAll()
        .map((model) => model.provider)
        .filter((providerId) => providerId !== "openrouter"),
    );
    for (const providerId of otherProviders) {
      ctx.modelRegistry.registerProvider(providerId, { models: [] });
    }
  });
}
