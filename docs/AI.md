# AI in PYFIX 360

PYFIX 360 is **offline-first**. The entire audit, repair and verification workflow
works with no network and no AI at all. AI is an optional assistant you configure, and
it is off by default.

This document describes exactly what the AI layer does, what it will refuse to do, and
what it will never claim. If something below is not true in the code, that is a bug —
the accompanying test suite (`tests/test_ai_privacy.py`, `tests/test_ai_discovery.py`,
`tests/test_ai_catalog.py`) is intended to catch it.

---

## 1. The three hard rules

1. **Nothing leaves the machine unless you say so.** Remote transmission requires all
   of: the provider enabled, a privacy mode that permits remote, and (for repository
   content) the "repository content" switch turned on *and* a remote-capable privacy
   mode.
2. **Redaction happens before the prompt is built.** Secrets are stripped first; then
   the prompt is assembled. There is no code path where raw source is sent and
   "redacted afterwards".
3. **AI is never the verification authority.** The test suite and the static analyzers
   decide whether a repair is *verified*. AI output is advisory and labelled as such.

---

## 2. Provider catalogue

`src/pyfix360/ai/catalog.py` is **data only**. Importing it performs no I/O and never
touches the network. It exists so the provider screen can offer known endpoints and
models instead of asking you to remember base URLs.

Twelve presets ship:

| Preset key | Endpoint | Key required | Notes |
|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` | no | Fully offline. |
| `lmstudio` | `http://localhost:1234/v1` | no | Fully offline. |
| `llamacpp` | `http://localhost:8080/v1` | no | Fully offline. |
| `vllm` | `http://localhost:8000/v1` | no | vLLM / text-generation-webui. |
| `openrouter` | `https://openrouter.ai/api/v1` | yes | One key, many vendors; hosts free `:free` ids. |
| `groq` | `https://api.groq.com/openai/v1` | yes | Fast free tier. |
| `cerebras` | `https://api.cerebras.ai/v1` | yes | Fast free tier. |
| `google` | Gemini via Google AI Studio | yes | Free tier. |
| `mistral` | Mistral La Plateforme | yes | Free tier + Codestral. |
| `deepseek` | `https://api.deepseek.com/v1` | yes | Very low cost. |
| `together` | Together AI | yes | Open-weight models. |
| `custom` | anything you type | depends | Any OpenAI-compatible endpoint. |

Every preset records its `signup_url` and a plain-language note, so you are never sent
hunting for a key location.

---

## 3. Free / low-cost models

Cost is recorded as one of `free`, `cheap`, `paid` and shown in the picker. The
catalogue is a **reviewed starting point** — `CATALOG_REVIEWED` records the date
(`2026-10-21`). Model ids change constantly, so treat them as things to verify.

### 3.1 The models explicitly requested

| Requested | Catalogue id(s) | Typical home | Cost label | Honest note |
|---|---|---|---|---|
| **Big Pickle** | `big-pickle` | Aggregator / self-hosted gateway | `free` | Availability and id vary by gateway. |
| **GLM Flash 5.3** | `z-ai/glm-4.6`, `z-ai/glm-4.5-air` | OpenRouter, Z.ai direct | `cheap` / `free` | The lightweight `-air` variants are the free tier; the exact `-flash` id is edition-dependent. |
| **LongCat 2.5 Preview** | `meituan/longcat` | Meituan → usually an aggregator | `free` | Meituan's model; generally reached through an aggregator or gateway rather than a first-party public API. |
| **MiniMax 3.1 Flash** | `minimax/minimax-m2` | MiniMax, OpenRouter | `cheap` | The `-flash`/preview variants are the cheaper ones. |
| **MiMo V2.6** | `xiaomi/mimo` | Xiaomi → usually an aggregator | `free` | Xiaomi's model; same aggregator caveat as LongCat. |

> These entries carry this caveat **in the app**, not only in this file. The provider
> screen shows the note for each model so the limitation is visible at the point of
> use.

### 3.2 Other genuinely useful free/low-cost models in the catalogue

Coding: `qwen/qwen-2.5-coder-32b-instruct`, `deepseek/deepseek-chat`,
`deepseek/deepseek-r1`, `Qwen/Qwen2.5-Coder-32B-Instruct` (Together),
`codestral-latest` (Mistral).

General/fast: `google/gemini-2.0-flash-exp:free`, `gemini-2.0-flash-lite`,
`mistralai/mistral-small`, `openai/gpt-oss-120b`, `llama-3.3-70b-versatile` (Groq),
`llama-3.3-70b` (Cerebras), `moonshotai/kimi-k2`, `x-ai/grok-3-mini`.

Local (no cost, no network): the entire Ollama / LM Studio / llama.cpp / vLLM model
list — Qwen2.5 Coder, DeepSeek Coder V2, Llama 3.1, Code Llama.

---

## 4. Live discovery (opt-in, metadata-only)

Providers rename and retire models; a static list is always slightly stale. **Discover
models** asks your endpoint directly.

* It issues **exactly one** request: `GET {base_url}/models`, the documented
  OpenAI-compatible metadata route.
* The request body is empty. No prompt, no source, no repository content. Only your
  credential travels, and only to the endpoint you configured.
* Nothing runs on startup, on a timer, or in the background. You press the button.
* Results are merged with the catalogue for annotation only. **Prices are never
  invented**: an id we do not recognise is labelled `available` (or the provider's
  `owned_by`), not guessed at.
* A failed refresh leaves the previously known list intact — a transient error does not
  wipe your model list.
* **Discovery obeys the same policy as sending a prompt.** It is a remote call, so it
  is refused unless the privacy mode is `full_remote_approved` and the
  `remote_ai` capability permits it. Discovery never gets a weaker rule than the chat
  it would precede.

The merge annotation values are:

| Annotation | Meaning |
|---|---|
| `known free / low cost` | The id appears in the catalogue with a `free`/`cheap` label. |
| `unknown price` | Discovered live but not in the catalogue — price deliberately not asserted. |
| `<owner>` | The provider reported an `owned_by` value. |
| `available` | Known to exist, nothing else claimed. |

---

## 5. Privacy modes

| Mode | Behaviour |
|---|---|
| `local_only` | Only local endpoints are allowed. Any remote provider is refused. This is the default. |
| `redacted_remote` | Remote permitted **only** when combined with explicit full approval. Secrets are always redacted first. |
| `full_remote_approved` | You have explicitly approved sending source to a remote endpoint. |

**Fails safe.** A provider whose URL is not clearly a local runtime is treated as
**remote**, even if the provider *type* says otherwise. A mislabelled "local_runtime"
pointing at a public host gets the stricter checks, never a free pass. Known remote
hosts (`openrouter.ai`, `api.groq.com`, `api.deepseek.com`, `api.mistral.ai`,
`api.together.xyz`, `generativelanguage.googleapis.com`, `api.cerebras.ai`, …) are
recognised explicitly, and anything unrecognised still defaults to remote.

**The host is compared exactly, never as a substring.** `localhost` appearing
*somewhere* in a URL is not the same thing as the host being `localhost`, so
`http://localhost.evil.com/v1` is classified **remote**, not local. The same holds
for `127.0.0.1` and `::1`: they are recognised as loopback addresses, but
`127.0.0.1.evil.com` is not one of them. A local-looking prefix earns nothing.

---

## 6. Redaction

`src/pyfix360/ai/redaction.py` strips, before any prompt is built:

* PEM private keys
* AWS access keys (`AKIA…`)
* GitHub tokens (`ghp_`/`gho_`/`ghs_`/`ghr_`/`ghu_`)
* `sk-…` / `pk-…` / `rk-…` API tokens
* Slack tokens (`xox…`)
* JWTs and `authorization:` / `bearer` headers
* Connection-string passwords, and high-entropy strings in credential-looking names

Each substitution is counted. The **count** is recorded in AI request metadata so you
can see how much sensitive material was stripped; the placeholder text is what reaches
the model.

**Redaction cannot be switched off.** The gateway redacts unconditionally, and the
`redact_before_ai` setting is locked on: the UI shows the switch disabled and the
settings store refuses a write that would turn it off. A stored `false` would be a
lie the code could not honour, so the state simply does not exist.

---

## 7. Logging and history

* AI request rows store a **prompt SHA-256**, the model, the purpose, the redaction
  count, the latency and whether the destination was remote — **never the prompt**.
* Chat history stores **redacted text only**, with the same SHA-256 convention. It is
  browsable and purgeable.
* **There is no telemetry.** The app collects nothing about how it is used — no
  usage counters, no crash reports, no analytics — and sends nothing except the
  AI requests you explicitly make.

---

## 8. Credentials

* API keys are **never** written to SQLite.
* Preferred backend: the OS credential store (Windows Credential Manager / macOS
  Keychain / SecretService) via `keyring`.
* Fallback: an encrypted file vault keyed from machine data. When this weaker fallback
  is in use, **the UI says so** — it is not hidden. `cred_store.store_info()` reports
  the active backend.
* Removing a provider deletes its stored credential.

---

## 9. What AI is and is not allowed to do

* It may **explain** a finding, **propose** a repair, and **chat** about the project.
* It may **not** mark anything verified. Verification requires a passing baseline and a
  passing post-repair test run, full stop.
* It may **not** be trusted as instructions. Repository content is framed as untrusted
  data; no text from a prompt is ever executed.

If you have not configured a provider, the UI says **"configured but key absent"** or
"No AI provider is configured" — it never pretends a model answered. `verified_live` is
`False` for every provider until a real call actually succeeds.

---

## See also

* [`../README.md`](../README.md) — *AI providers, models and privacy*.
* [`DATA_MODEL.md`](DATA_MODEL.md) — AI request and chat tables.
* [`PACKAGING.md`](PACKAGING.md) — the frozen build keeps these guarantees unchanged.
