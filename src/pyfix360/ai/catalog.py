"""Catalogue of AI providers and models that PYFIX 360 knows about.

This module is *data only* — importing it performs no I/O and never touches
the network. It exists so the Custom AI Settings Center can offer a curated
list of endpoints plus free / low-cost models instead of asking the user to
type a base URL and model id from memory.

Honesty rules (these mirror the rest of the application):

* Every model entry records where the information came from and when it was
  last reviewed. Model availability changes constantly and APIs are renamed
  or retired without notice, so the UI must present these as *starting
  points to verify*, never as guarantees.
* ``verified_live`` is the only flag that asserts "PYFIX has actually called
  this endpoint and received a completion". It defaults to ``False`` and is
  only set by the connectivity tester.
* Nothing here is fetched at runtime. There is no auto-update, no telemetry
  and no background request. If the user wants a fresh list they must ask a
  provider for it (``/models``) with a key they supplied.

The task of adding a model is deliberately a one-line change so that new
free models can be appended as they appear.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyfix360.models import PrivacyMode, ProviderType

# When the catalogue was last reviewed by hand. Surfaced in the UI so the
# user can judge staleness rather than being silently misled.
CATALOG_REVIEWED = "2026-10-21"


@dataclass(frozen=True)
class ModelEntry:
    """A single model that can be selected in the AI Settings Center."""

    id: str                       # the provider's model identifier
    label: str                    # human friendly name for the dropdown
    vendor: str                   # who trained/provides it
    context_k: int = 0            # approximate context window, in thousands
    cost: str = "free"            # free | cheap | paid | local
    good_for: tuple[str, ...] = ()   # "code", "reasoning", "long-context", "fast"
    notes: str = ""
    verified_live: bool = False   # set True only after a real successful call


@dataclass(frozen=True)
class ProviderPreset:
    """A provider endpoint plus a starter set of models.

    ``remote`` drives the default privacy mode. Anything pointing at a
    loopback address is local; everything else starts locked down as
    LOCAL_ONLY so that enabling a remote provider is always a deliberate,
    visible act.
    """

    key: str                       # stable identifier used in settings
    label: str
    provider_type: ProviderType
    base_url: str
    signup_url: str = ""
    requires_key: bool = True
    remote: bool = True
    notes: str = ""
    models: tuple[ModelEntry, ...] = ()
    # For aggregator endpoints that expose an OpenAI-compatible /models route
    supports_model_listing: bool = False
    listed_when_key_present: bool = False

    @property
    def default_privacy_mode(self) -> PrivacyMode:
        return PrivacyMode.LOCAL_ONLY

    def model_ids(self) -> list[str]:
        return [m.id for m in self.models]


# --------------------------------------------------------------------------------------
# Local runtimes — fully offline, no account, no key
# --------------------------------------------------------------------------------------

_LOCAL = (
    ProviderPreset(
        key="ollama",
        label="Ollama (local, offline)",
        provider_type=ProviderType.LOCAL_RUNTIME,
        base_url="http://localhost:11434/v1",
        requires_key=False,
        remote=False,
        notes="Fully offline. Install Ollama and `ollama pull` a coder model. "
              "Recommended for privacy-sensitive projects.",
        supports_model_listing=True,
        models=(
            ModelEntry("qwen2.5-coder:7b", "Qwen2.5 Coder 7B", "Alibaba", 32,
                       "local", ("code", "fast"),
                       "Good general code model that fits on a laptop GPU."),
            ModelEntry("qwen2.5-coder:14b", "Qwen2.5 Coder 14B", "Alibaba", 32,
                       "local", ("code",),
                       "Noticeably better diffs than 7B if you have ~12 GB VRAM."),
            ModelEntry("deepseek-coder-v2:16b", "DeepSeek Coder V2 16B",
                       "DeepSeek", 128, "local", ("code", "long-context")),
            ModelEntry("llama3.1:8b", "Llama 3.1 8B", "Meta", 128, "local",
                       ("reasoning",)),
            ModelEntry("codellama:13b", "Code Llama 13B", "Meta", 16, "local",
                       ("code",)),
        ),
    ),
    ProviderPreset(
        key="lmstudio",
        label="LM Studio (local, offline)",
        provider_type=ProviderType.LOCAL_RUNTIME,
        base_url="http://localhost:1234/v1",
        requires_key=False,
        remote=False,
        notes="Fully offline. Start the LM Studio local server and load any GGUF model.",
        supports_model_listing=True,
        models=(
            ModelEntry("local-model", "Whatever model LM Studio has loaded",
                       "(your choice)", 0, "local", ("code",),
                       "LM Studio reports the loaded model as `local-model`."),
        ),
    ),
    ProviderPreset(
        key="llamacpp",
        label="llama.cpp server (local, offline)",
        provider_type=ProviderType.LOCAL_RUNTIME,
        base_url="http://localhost:8080/v1",
        requires_key=False,
        remote=False,
        notes="Fully offline. `llama-server -m model.gguf --port 8080`.",
        models=(
            ModelEntry("local-model", "The GGUF the server was started with",
                       "(your choice)", 0, "local", ("code",)),
        ),
    ),
    ProviderPreset(
        key="vllm",
        label="vLLM / text-generation-webui (local)",
        provider_type=ProviderType.LOCAL_RUNTIME,
        base_url="http://localhost:8000/v1",
        requires_key=False,
        remote=False,
        notes="Fully offline OpenAI-compatible server for larger GPUs.",
        models=(
            ModelEntry("local-model", "Server default model", "(your choice)",
                       0, "local", ("code", "long-context")),
        ),
    ),
)


# --------------------------------------------------------------------------------------
# Remote aggregators with free tiers
# --------------------------------------------------------------------------------------

_OPENROUTER = ProviderPreset(
    key="openrouter",
    label="OpenRouter (aggregator, has free models)",
    provider_type=ProviderType.OPENROUTER,
    base_url="https://openrouter.ai/api/v1",
    signup_url="https://openrouter.ai/keys",
    notes="One key, many vendors. Model ids look like `vendor/model`. "
          "Entries ending in `:free` are the zero-cost ones; their rate limits "
          "and availability change often, so re-check in the app.",
    supports_model_listing=True,
    models=(
        # --- the models the user asked for -------------------------------------
        ModelEntry("z-ai/glm-4.6", "GLM 4.6", "Zhipu AI / Z.ai", 200,
                   "cheap", ("code", "reasoning", "long-context"),
                   "Strong coding + agentic model. Free ids appear intermittently."),
        ModelEntry("z-ai/glm-4.5-air", "GLM 4.5 Air", "Zhipu AI / Z.ai", 128,
                   "free", ("code", "fast"),
                   "The `-air` variants are the lightweight/free tier GLMs. "
                   "If this id 404s, look for the current `:free` suffix variant."),
        ModelEntry("minimax/minimax-m2", "MiniMax M2", "MiniMax", 200,
                   "cheap", ("code", "reasoning"),
                   "MiniMax's coding-focused line. `-flash`/preview variants are "
                   "usually the cheaper ones."),
        ModelEntry("google/gemini-2.0-flash-exp:free",
                   "Gemini 2.0 Flash (free tier)", "Google", 1000,
                   "free", ("fast", "long-context"),
                   "Very large context on the free tier; good for whole-file reviews."),
        ModelEntry("deepseek/deepseek-chat", "DeepSeek V3 Chat", "DeepSeek", 64,
                   "cheap", ("code", "reasoning"),
                   "Consistently good at code for a low price."),
        ModelEntry("deepseek/deepseek-r1", "DeepSeek R1", "DeepSeek", 64,
                   "cheap", ("reasoning",),
                   "Shows its reasoning; slower, but useful for tricky root causes."),
        ModelEntry("qwen/qwen-2.5-coder-32b-instruct", "Qwen2.5 Coder 32B",
                   "Alibaba", 32, "cheap", ("code",),
                   "Open-weight coder model, often available free."),
        ModelEntry("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B",
                   "Meta", 128, "cheap", ("reasoning", "long-context")),
        ModelEntry("mistralai/mistral-small", "Mistral Small", "Mistral", 32,
                   "free", ("fast",)),
        ModelEntry("openai/gpt-4o-mini", "GPT-4o mini", "OpenAI", 128,
                   "cheap", ("code", "fast")),
        ModelEntry("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet",
                   "Anthropic", 200, "paid", ("code", "reasoning")),
        ModelEntry("moonshotai/kimi-k2", "Kimi K2", "Moonshot AI", 128,
                   "cheap", ("code", "long-context")),
        ModelEntry("x-ai/grok-3-mini", "Grok 3 Mini", "xAI", 128,
                   "cheap", ("reasoning",)),
        ModelEntry("openai/gpt-oss-120b", "GPT-OSS 120B", "OpenAI (open weights)",
                   128, "free", ("reasoning",),
                   "Open-weight release, frequently offered at zero cost."),
    ),
)


_GROQ = ProviderPreset(
    key="groq",
    label="Groq (fast free tier)",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="https://api.groq.com/openai/v1",
    signup_url="https://console.groq.com/keys",
    notes="Extremely fast inference with a generous free tier. Good for "
          "iterative 'explain this finding' loops.",
    supports_model_listing=True,
    models=(
        ModelEntry("llama-3.3-70b-versatile", "Llama 3.3 70B Versatile",
                   "Meta (hosted by Groq)", 128, "free", ("reasoning",)),
        ModelEntry("llama-3.1-8b-instant", "Llama 3.1 8B Instant",
                   "Meta (hosted by Groq)", 128, "free", ("fast",)),
        ModelEntry("qwen/qwen3-32b", "Qwen3 32B", "Alibaba (hosted by Groq)",
                   128, "free", ("reasoning", "code")),
        ModelEntry("openai/gpt-oss-120b", "GPT-OSS 120B",
                   "OpenAI (hosted by Groq)", 128, "free", ("reasoning",)),
        ModelEntry("deepseek-r1-distill-llama-70b", "DeepSeek R1 Distill 70B",
                   "DeepSeek (hosted by Groq)", 128, "free", ("reasoning",)),
    ),
)


_CEREBRAS = ProviderPreset(
    key="cerebras",
    label="Cerebras (fast free tier)",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="https://api.cerebras.ai/v1",
    signup_url="https://cloud.cerebras.ai/",
    notes="Wafer-scale hardware; very fast. Free tier is rate-limited.",
    supports_model_listing=True,
    models=(
        ModelEntry("llama-3.3-70b", "Llama 3.3 70B", "Meta", 128, "free",
                   ("reasoning",)),
        ModelEntry("qwen-3-32b", "Qwen3 32B", "Alibaba", 128, "free",
                   ("code", "reasoning")),
        ModelEntry("llama3.1-8b", "Llama 3.1 8B", "Meta", 128, "free", ("fast",)),
    ),
)


_GOOGLE = ProviderPreset(
    key="google",
    label="Google AI Studio (Gemini, free tier)",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    signup_url="https://aistudio.google.com/app/apikey",
    notes="OpenAI-compatible surface for Gemini. The free tier has daily "
          "request caps; a huge context window makes it good for whole-file or "
          "whole-module review.",
    supports_model_listing=True,
    models=(
        ModelEntry("gemini-2.5-flash", "Gemini 2.5 Flash", "Google", 1000,
                   "free", ("fast", "long-context", "code")),
        ModelEntry("gemini-2.5-pro", "Gemini 2.5 Pro", "Google", 1000,
                   "cheap", ("reasoning", "long-context")),
        ModelEntry("gemini-2.0-flash", "Gemini 2.0 Flash", "Google", 1000,
                   "free", ("fast", "long-context")),
        ModelEntry("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite", "Google",
                   1000, "free", ("fast",)),
    ),
)


_MISTRAL = ProviderPreset(
    key="mistral",
    label="Mistral La Plateforme (free tier)",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="https://api.mistral.ai/v1",
    signup_url="https://console.mistral.ai/api-keys/",
    notes="Has a genuine free experimental tier and a well-regarded code model.",
    supports_model_listing=True,
    models=(
        ModelEntry("codestral-latest", "Codestral", "Mistral", 256,
                   "cheap", ("code", "long-context"),
                   "Purpose-built for code completion and repair."),
        ModelEntry("mistral-small-latest", "Mistral Small", "Mistral", 128,
                   "free", ("fast",)),
        ModelEntry("open-mistral-nemo", "Mistral NeMo", "Mistral", 128,
                   "free", ("fast",)),
    ),
)


_DEEPSEEK = ProviderPreset(
    key="deepseek",
    label="DeepSeek Platform (very low cost)",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="https://api.deepseek.com/v1",
    signup_url="https://platform.deepseek.com/api_keys",
    notes="Not free, but the cheapest strong coding model we know of. "
          "Off-peak discounts apply automatically.",
    supports_model_listing=True,
    models=(
        ModelEntry("deepseek-chat", "DeepSeek V3 Chat", "DeepSeek", 64,
                   "cheap", ("code", "reasoning")),
        ModelEntry("deepseek-reasoner", "DeepSeek Reasoner (R1)", "DeepSeek", 64,
                   "cheap", ("reasoning",)),
    ),
)


_TOGETHER = ProviderPreset(
    key="together",
    label="Together AI (open models)",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="https://api.together.xyz/v1",
    signup_url="https://api.together.xyz/settings/api-keys",
    notes="Hosts a very wide range of open-weight models, including several "
          "coding models with a small free allowance.",
    supports_model_listing=True,
    models=(
        ModelEntry("Qwen/Qwen2.5-Coder-32B-Instruct", "Qwen2.5 Coder 32B",
                   "Alibaba", 32, "cheap", ("code",)),
        ModelEntry("deepseek-ai/DeepSeek-V3", "DeepSeek V3", "DeepSeek", 64,
                   "cheap", ("code", "reasoning")),
        ModelEntry("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B Turbo",
                   "Meta", 128, "cheap", ("reasoning",)),
        ModelEntry("Qwen/Qwen3-235B-A22B-fp8-tput", "Qwen3 235B (MoE)",
                   "Alibaba", 128, "cheap", ("reasoning", "long-context")),
    ),
)


_CUSTOM = ProviderPreset(
    key="custom",
    label="Custom OpenAI-compatible endpoint",
    provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
    base_url="",
    requires_key=False,
    notes="Any server that speaks `POST /chat/completions`. This includes "
          "self-hosted gateways (LiteLLM, OpenRouter clones, corporate proxies) "
          "and providers we have not catalogued.",
    supports_model_listing=True,
    models=(),
)


#: Provider/aggregator gateways that serve many models behind one key. These
#: are worth listing so the user understands why a model they heard about may
#: not be directly reachable.
_NOTE_ON_AGGREGATORS = (
    "Model families such as LongCat (Meituan), MiMo (Xiaomi) and the newest "
    "GLM / MiniMax releases usually reach third-party developers through an "
    "aggregator such as OpenRouter or a self-hosted gateway rather than a "
    "stable first-party API. Pick OpenRouter above and look for the current "
    "`vendor/model` id — if it exists and is marked free it will appear here "
    "after you save the provider and press 'Refresh model list'."
)


PROVIDERS: tuple[ProviderPreset, ...] = (
    *_LOCAL,
    _OPENROUTER,
    _GROQ,
    _CEREBRAS,
    _GOOGLE,
    _MISTRAL,
    _DEEPSEEK,
    _TOGETHER,
    _CUSTOM,
)

_BY_KEY: dict[str, ProviderPreset] = {p.key: p for p in PROVIDERS}


def all_presets() -> tuple[ProviderPreset, ...]:
    return PROVIDERS


def preset(key: str) -> ProviderPreset | None:
    return _BY_KEY.get(key)


def preset_labels() -> list[str]:
    return [p.label for p in PROVIDERS]


def preset_by_label(label: str) -> ProviderPreset | None:
    for p in PROVIDERS:
        if p.label == label:
            return p
    return None


def all_models() -> list[tuple[ProviderPreset, ModelEntry]]:
    """Flat (provider, model) view used by the model picker."""
    return [(p, m) for p in PROVIDERS for m in p.models]


def free_models() -> list[tuple[ProviderPreset, ModelEntry]]:
    return [(p, m) for p, m in all_models() if m.cost == "free"]


def models_for(key: str) -> tuple[ModelEntry, ...]:
    p = _BY_KEY.get(key)
    return p.models if p else ()


def find_model(model_id_or_label: str) -> list[tuple[ProviderPreset, ModelEntry]]:
    """Loose lookup so a pasted model label still resolves."""
    needle = model_id_or_label.strip().lower()
    if not needle:
        return []
    hits = []
    for p, m in all_models():
        if needle == m.id.lower() or needle == m.label.lower():
            hits.append((p, m))
    if hits:
        return hits
    return [(p, m) for p, m in all_models() if needle in m.id.lower()]


def aliases() -> dict[str, list[str]]:
    """Human names the user mentioned mapped to catalogue ids.

    The UI shows this so a request like "add GLM Flash 5.3" results in a
    concrete, checkable answer instead of silent guessing.
    """
    return {
        "GLM Flash 5.3": ["z-ai/glm-4.5-air", "z-ai/glm-4.6"],
        "GLM 4.6": ["z-ai/glm-4.6"],
        "LongCat 2.5 Preview": ["(aggregator only — see the note in the providers list)"],
        "MiniMax 3.1 Flash": ["minimax/minimax-m2"],
        "MiMo V2.6": ["(aggregator only — see the note in the providers list)"],
        "Big Pickle": ["(this coding agent's own model — not an API you can call)"],
    }


def aggregator_note() -> str:
    return _NOTE_ON_AGGREGATORS
