# AI COUNCIL

**Collective reasoning across independent AI models.**

One question goes to every configured provider at the same time. Each answers on
its own. Then each one reads what the others said, accepts what holds up,
rejects what does not — and has to say *why*. A deterministic consensus engine
decides when they are actually finished, and a separate neutral synthesis stage
compiles the final answer without letting any single model crown itself the
winner.

This is not "send the same prompt to N APIs and concatenate the replies."

Nine providers ship configured out of the box — five paid, four with a usable
free tier:

| Paid | Free tier |
|---|---|
| `OPENAI`, `CLAUDE`, `GROK`, `DEEPSEEK` | `GEMINI`, `GROQCLOUD`, `CEREBRAS`, `MISTRAL`, `LOCAL` |

A provider without a key simply sits the debate out — you need at least two
working ones. See [§3](#3-getting-api-keys) for which are actually free.

---

Ships in two forms on top of one engine:

* a **desktop program** (native window, no browser) — see [§6.1](#61-desktop-program);
* a **web app** (FastAPI + React) — see [§6.2](#62-web-app).

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Getting API keys](#3-getting-api-keys)
4. [Configuring `.env`](#4-configuring-env)
5. [Installation](#5-installation)
6. [Running](#6-running)
7. [Running the tests](#7-running-the-tests)
8. [Choosing models](#8-choosing-models)
9. [Tuning the debate](#9-tuning-the-debate)
10. [Adding a new AI provider](#10-adding-a-new-ai-provider)
11. [API reference](#11-api-reference)
12. [Costs](#12-costs)
13. [Security notes](#13-security-notes)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. What it does

### Round 0 — independent answers

All configured agents receive the user's question **simultaneously** and answer in
isolation. No agent sees any other agent's output at this stage. Each returns a
validated object:

```json
{
  "answer": "...",
  "key_points": ["..."],
  "confidence": 0.0,
  "assumptions": ["..."]
}
```

### Rounds 1..N — a round table with roles

From round 1 on, this is a debate with assigned seats, not N parallel opinions.
Each agent gets a role that shapes *how* it argues — never *what* it is allowed
to conclude:

| Role | Job |
|---|---|
| `ADVOCATE` | owns convergence: picks the best-supported answer and works the others toward it |
| `SKEPTIC` | attacks the weakest link in the emerging position |
| `EVIDENCE` | separates what is known from what is assumed |
| `PRAGMATIST` | tests every answer against cost, effort and what breaks first |
| `BRIDGE` | hunts for the wording everyone could sign — and names the gaps that cannot be bridged |
| `ANALYST` | maps the structure of the disagreement and finds the one question that settles it |

There is exactly one `ADVOCATE` — two agents both dragging the table toward
"one answer" would just pull in opposite directions. Roles repeat only once the
rotation is exhausted (9 providers, 6 roles). Assignment is deterministic, and
`ADVOCATE_AGENT` pins it to a specific provider.

**Round 0 stays role-free on purpose.** Telling an agent "you are the skeptic"
before it has seen anything would distort the independent baseline the whole
debate is measured against.

Every agent must now do the part that actually ends debates: address the agents
who disagree by name, answer their objection in their own terms, say what it
would concede in exchange, and state what would change its own mind.

> **The tension this creates, and how it is resolved.** "Argue until they reach
> one answer" pulls against this project's other rule — never manufacture
> agreement. The resolution is a hard line drawn in code, not in the prompt:
> **persuading is the job, capitulating is not.** An agent may change position
> only if it names the agent and the specific argument that moved it. A switch
> with an empty `persuaded_by`, or with no stated reason, is recorded as
> `capitulation`, excluded from the agreement tally, and shown in the UI. So the
> council can converge — but only by actually convincing each other.

### Rounds 1..N — what each agent returns

Each agent now receives the original question, **its own previous contributions**,
and **every other agent's latest position**, plus the result of the previous
consensus check. It must engage specifically:

```json
{
  "position": "...",
  "accepted_arguments": [{ "agent": "CLAUDE", "argument": "...", "reason": "..." }],
  "rejected_arguments": [{ "agent": "GEMINI", "argument": "...", "reason": "..." }],
  "uncertain_arguments": [{ "agent": "GROK", "argument": "...", "reason": "..." }],
  "persuasion": [
    { "agent": "GROK", "their_objection": "...", "my_counter": "...", "concession": "..." }
  ],
  "persuaded_by": ["CLAUDE"],
  "what_would_change_my_mind": "...",
  "proposed_common_answer": "...",
  "changed_my_position": true,
  "why_changed": "...",
  "confidence": 0.0
}
```

Agents are free to agree, partly agree, disagree, point out an error, demand
proof, change position, or hold position. The system prompt explicitly forbids
agreeing without a stated reason and forbids following the majority.

### Consensus check — deterministic, in code

After each debate round every agent casts a structured `ConsensusVote`. The
**Consensus Engine** (`backend/debate/consensus.py`) — not a model — decides
whether the council is done. Consensus requires *all* of:

* at least `MIN_ROUNDS` debate rounds have happened;
* at least two agents are still participating;
* the weighted agreement score ≥ `CONSENSUS_THRESHOLD`;
* and one of these rules fires:

| Rule | Condition |
|---|---|
| `unanimous_conclusion` | every participant agrees, and nobody holds a material objection |
| `supermajority` | at least *n−1* participants agree (min. 80% of them) and no dissenter has a material, evidence-backed objection |
| `formulation_alignment` | everyone agrees on the *wording* of the answer while their reasoning differs — the score bar is relaxed 10% for this case |

**Protection against automatic agreement.** An agent's "yes" only counts if it
restated the conclusion in its own words *and* produced at least one specific
accepted/rejected/uncertain argument about a named other agent in the same round.
A bare "I agree with everyone" is recorded as `unsubstantiated`, excluded from
the agreement tally, and clamped to neutral in the score.

**Protection against capitulation.** An agent that flips its position without
naming who persuaded it (`persuaded_by` empty, or no stated reason) is recorded
as `capitulated` and does not count towards agreement either. This is what keeps
the round table from converting persuasion pressure into fake consensus.

The UI shows exactly which agreements were discarded and why, plus a
"who persuaded whom" list built from the `persuaded_by` fields.

**Protection against the majority effect.** A minority is never asked to fall in
line. If four agents say X and one says Y with evidence, the dissenting position
is carried into the final report — and re-injected automatically if the
synthesiser omits it.

### Final synthesis — a separate stage

Not one of the five picking a winner:

* every quantitative claim (per-agent agreement, consensus score, who agreed, who
  dissented, which objections were material, which arguments were rejected and by
  whom) is computed in code and passed to the synthesiser as authoritative fact;
* a model writes only the prose, addressed as a neutral `SYNTHESIS ENGINE`, not
  as a council member;
* minority positions and per-agent positions are re-injected from the
  deterministic record afterwards;
* if the elected synthesiser fails, the next candidate is tried; if every model
  fails, a fully deterministic synthesis is assembled from the stored rounds.

The final report contains: **Consensus**, **Main reasoning**, **Disagreements**,
**Strongest arguments**, **Rejected arguments (and why)**, **Confidence 0–100%**,
**Individual positions**, and **Minority opinion with an assessment**.

---

## 2. Architecture

```
  desktop/  (окно Qt)  ─┐
                        ├─→  общее ядро: agents · debate · models · services
  backend/api (веб)    ─┘

                     ┌──────── OPENAI    (openai SDK)
                     ├──────── CLAUDE    (anthropic SDK)
  USER ──▶ Orchestrator ─────── GEMINI    (google-genai SDK)     ← all in parallel
                     ├──────── GROK      (openai SDK → api.x.ai)
                     └──────── DEEPSEEK  (openai SDK → api.deepseek.com)

  DebateOrchestrator
        ├── Round 0 ................ independent answers
        ├── Round 1 ................ debate  →  consensus votes  →  Consensus Engine
        ├── Round 2 ... MAX_ROUNDS   (stops early the moment consensus is reached)
        └── Final Synthesis ........ neutral stage + deterministic facts
```

```
backend/
├── main.py                  FastAPI app, CORS, rate limiting, static SPA mount
├── config.py                all settings, read from env / .env
├── pricing.json             editable token-price table (cost estimates only)
├── models/
│   ├── enums.py             AgentName, AgentStatus, DebateStatus, EventType…
│   ├── schemas.py           strict Pydantic contracts for every model reply
│   └── db.py                SQLAlchemy 2.0 async ORM (SQLite → PostgreSQL ready)
├── agents/
│   ├── base.py              BaseAgent: retries, JSON repair, validation, usage
│   ├── prompts.py           system prompts, identities, all prompt construction
│   ├── registry.py          the council roster — one entry per provider
│   ├── openai_agent.py      OpenAI + shared OpenAI-compatible client
│   ├── claude_agent.py      Anthropic
│   ├── gemini_agent.py      Google
│   ├── grok_agent.py        xAI
│   └── deepseek_agent.py    DeepSeek
├── debate/
│   ├── orchestrator.py      the loop, persistence, events, hard round bound
│   ├── round_manager.py     parallel fan-out per phase, per-agent failure isolation
│   ├── consensus.py         deterministic Consensus Engine
│   └── synthesis.py         neutral synthesis + deterministic fallback
├── services/
│   ├── retry.py             timeout + classified retry policy
│   ├── json_repair.py       JSON extraction / repair
│   ├── events.py            in-process SSE event bus
│   ├── storage.py           repository over the ORM
│   ├── pricing.py           token accounting + cost estimation
│   ├── rate_limit.py        sliding-window limiter
│   └── logging.py           logging setup (never logs prompts or keys)
├── api/
│   ├── routes.py            REST + SSE endpoints
│   └── service.py           background task management, validation, MD export
└── tests/                   64 tests, mock providers only — no keys, no network

frontend/
├── src/
│   ├── App.tsx              page composition
│   ├── lib/api.ts           typed fetch + EventSource client
│   ├── lib/useDebate.ts     live state machine driven by SSE
│   ├── lib/types.ts         TypeScript mirrors of the backend schemas
│   ├── styles.css           dark theme
│   └── components/          status bar, question form, settings, rounds,
│                            consensus strip, final report, tokens, history, log
└── nginx.conf               production proxy (SSE-safe: buffering off)
```

### Design guarantees

| Guarantee | Where it is enforced |
|---|---|
| Every round runs in parallel, never sequentially | `debate/round_manager.py` (`asyncio.gather`) |
| One failing provider never breaks a round | each agent returns an outcome; failures are data, not exceptions |
| A failed agent can rejoin in a later round | the orchestrator re-invites every agent each round |
| The debate can never loop forever | the loop is a bounded `range(1, max_rounds + 1)` |
| No control flow reads free text | every reply is validated against a Pydantic schema first |
| Internal chain-of-thought is never surfaced | thinking blocks are dropped in `claude_agent.py` / `gemini_agent.py` |
| API keys never reach the browser | `/api/settings` returns a `configured` boolean only |

---

## 3. Getting API keys

### Paid — billed per token, no free tier for the API

| Agent | Provider | Where to get the key | Env var |
|---|---|---|---|
| `OPENAI` | OpenAI | <https://platform.openai.com/api-keys> | `OPENAI_API_KEY` |
| `CLAUDE` | Anthropic | <https://console.anthropic.com/settings/keys> | `ANTHROPIC_API_KEY` |
| `GROK` | xAI | <https://console.x.ai/> | `XAI_API_KEY` |
| `DEEPSEEK` | DeepSeek | <https://platform.deepseek.com/api_keys> | `DEEPSEEK_API_KEY` |

### Free tier — no card required at signup

| Agent | Provider | Where to get the key | Env var |
|---|---|---|---|
| `GEMINI` | Google AI Studio | <https://aistudio.google.com/apikey> | `GOOGLE_API_KEY` |
| `GROQCLOUD` | Groq Cloud | <https://console.groq.com/keys> | `GROQ_API_KEY` |
| `CEREBRAS` | Cerebras | <https://cloud.cerebras.ai/> | `CEREBRAS_API_KEY` |
| `MISTRAL` | Mistral | <https://console.mistral.ai/api-keys/> | `MISTRAL_API_KEY` |
| `LOCAL` | your own machine | <https://ollama.com/download> | `OLLAMA_BASE_URL` |

`LOCAL` needs no key at all — set `OLLAMA_BASE_URL` to your server
(`http://localhost:11434/v1` for Ollama, `http://localhost:1234/v1` for
LM Studio). It is the only participant whose traffic never leaves the machine,
and the only one with no rate limit.

Free-tier limits change constantly — check the provider's own page before
relying on a number. As a scale reference, one debate here measured **~40k
tokens and ~36 requests** (5 agents × 3 rounds).

You need **at least two** working providers — the app refuses to start a
one-agent "debate", and if only one survives round 0 it stops there instead of
burning rounds on a monologue.

> **Do not give two agents the same open-weights model.** Running Llama-70B on
> both `GROQCLOUD` and `CEREBRAS` puts one voice in the council twice: there is
> nobody to actually disagree, and the resulting "consensus" is an artefact.
> Pick different model families.

---

## 4. Configuring `.env`

```bash
cp .env.example .env
```

Then edit `.env`. The file is git-ignored; keys are read by the backend process
only and are never sent to the frontend. See `.env.example` for every option
with inline documentation.

---

## 5. Installation

### Requirements

* Python 3.11+ (developed and tested on 3.13)
* Node.js 20+ (developed and tested on 24)
* or just Docker

### Without Docker

```bash
# 1. backend
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r backend/requirements.txt

# 2. frontend
npm install --prefix frontend

# 3. configuration
cp .env.example .env      # then add your keys
```

---

## 6. Running

### 6.1 Desktop program

A real application window — no server, no browser, no HTTP inside. It imports
the debate engine directly and runs it on a background asyncio thread.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r desktop\requirements.txt
```

Then double-click **`AI Council.bat`** (make a shortcut to it on the desktop or
in the Start menu). It uses `pythonw.exe`, so no console window appears.

From a terminal, either of these works:

```bash
python ai_council.py
```

```bash
python -m desktop
```

`ai_council.py` runs from any working directory; `python -m desktop` only works
from the project root, because `-m` resolves the module before any code in it
can adjust `sys.path`.

Keys can be entered in the program itself: **Настройки → Ключи**. They are
written to the local `.env` and never leave the machine. A key already supplied
through an environment variable is shown as such and is never overwritten with
a blank.

The interface is deliberately quiet — one question, one answer:

```text
┌─────────────────────────────────────────────────────┐
│  AI COUNCIL                    История   Настройки  │
│  ┌───────────────────────────────────────────────┐  │
│  │ Ваш вопрос…                                   │  │
│  └───────────────────────────────────────────────┘  │
│  Ctrl+Enter — спросить              [ Спросить ]    │
├─────────────────────────────────────────────────────┤
│  ● OPENAI  ● CLAUDE  ● GEMINI  ● GROK  ● DEEPSEEK   │
│  Раунд 2 из 5  ──────────────────────               │
├─────────────────────────────────────────────────────┤
│  ОТВЕТ                                              │
│  Начните с модульного монолита…                     │
│  Согласие: 78% · 4 из 5 моделей · возражает: CLAUDE │
│                                                     │
│  ▸ Показать ход обсуждения (2 раунда)               │
│  ▸ Расхождения и мнение меньшинства (1)             │
│  ▸ Расход токенов и стоимость (42 546 токенов)      │
│              [Копировать ответ]  [Сохранить отчёт…] │
└─────────────────────────────────────────────────────┘
```

Everything the web version shows on the page is still here — one click deeper,
behind a label that says what is inside. A provider that fails turns its dot
red; the reason is inside *Показать ход обсуждения*.

Layout:

```text
desktop/
├── __main__.py        точка входа (python -m desktop)
├── app.py             главное окно
├── controller.py      мост asyncio → сигналы Qt, прямой вызов оркестратора
├── settings_dialog.py ключи, модели, правила обсуждения
├── env_file.py        чтение и запись .env с сохранением комментариев
├── report.py          тексты для скрытых блоков
├── widgets.py         индикаторы агентов, раскрывающиеся секции
└── theme.py           тёмная тема
```

A standalone `.exe` (PyInstaller) is the planned next step — the program is
run from the shortcut first so the packaging step happens once the UI has
settled.

### 6.2 Web app

#### Docker (simplest)

```bash
docker compose up
```

Then open <http://localhost:8080>. The API is on <http://localhost:8000>
(interactive docs at `/docs`). The SQLite database lives in the `council-data`
volume and survives restarts.

#### Without Docker — two terminals

Terminal 1 (backend, from the repository root):

```bash
uvicorn backend.main:app --reload --port 8000
```

Terminal 2 (frontend):

```bash
npm run dev --prefix frontend
```

Open <http://localhost:5173>. The Vite dev server proxies `/api` to
`http://127.0.0.1:8000` (override with `VITE_BACKEND_URL`).

#### Single-process (backend serves the built UI)

```bash
npm run build --prefix frontend
# copy the build to where the backend looks for it
cp -r frontend/dist backend/static           # PowerShell: Copy-Item -Recurse frontend\dist backend\static
uvicorn backend.main:app --port 8000
```

Open <http://localhost:8000>.

---

## 7. Running the tests

```bash
python -m pytest              # from the repository root
python -m pytest -v           # verbose
python -m pytest backend/tests/test_consensus.py
```

64 tests, no API keys and no network access required — every provider is replaced
by `MockAgent`, which subclasses the real `BaseAgent`, so the retry policy, JSON
repair, schema validation and token accounting under test are the production ones.

Covered:

| # | Scenario | Test |
|---|---|---|
| 1 | all five providers succeed | `test_all_five_agents_succeed_and_debate_completes` |
| 2 | one provider fails, debate continues with four | `test_one_failing_provider_does_not_break_the_round` |
| 3 | timeout | `test_timeout_is_reported_as_an_error_with_a_reason` |
| 4 | retry recovers a transient failure | `test_retry_recovers_from_a_transient_failure` |
| 5 | malformed JSON → repair → re-ask → failure | `test_malformed_json_is_repaired_without_a_second_call`, `test_unrepairable_json_triggers_one_reask_then_succeeds`, `test_permanently_invalid_output_marks_agent_invalid` |
| 6 | consensus reached, debate stops early | `test_consensus_stops_the_debate_early` |
| 7 | no consensus | `test_no_consensus_runs_to_max_rounds_and_stops` |
| 8 | `MAX_ROUNDS` respected, no infinite loop | same test — asserts exactly `max_rounds` debate rounds |
| 9 | minority opinion preserved | `test_minority_position_survives_a_synthesis_that_omits_it` |
| 10 | token usage accounting | `test_token_usage_is_tracked_per_agent`, `test_cost_is_computed_when_pricing_is_configured` |

Plus: independence of round 0, cross-visibility in debate rounds, anti-sycophancy,
`MIN_ROUNDS`, agent identity isolation, parallel execution, SSE persistence and
replay, rate limiting, export, and the full HTTP surface.

---

## 8. Choosing models

Nothing in the architecture is bound to a specific model. Set them in `.env`:

```env
OPENAI_MODEL=gpt-4o
ANTHROPIC_MODEL=claude-opus-5
GEMINI_MODEL=gemini-2.5-flash
XAI_MODEL=grok-3
DEEPSEEK_MODEL=deepseek-chat
```

You can also override models per debate in the **Settings** panel in the UI, or
per request via the `models` field on `POST /api/debate`.

**Structured output is negotiated, not assumed.** Each agent starts in the
richest mode its provider supports (JSON Schema for OpenAI/xAI/Anthropic, JSON
mode for Gemini/DeepSeek) and automatically downgrades if the configured model
rejects it. The same mechanism drops `temperature`, `max_tokens` vs
`max_completion_tokens`, the system role, and the thinking configuration when a
model rejects them — so switching to a model with a different parameter surface
does not require code changes.

---

## 9. Tuning the debate

| Setting | Default | Meaning |
|---|---|---|
| `MIN_ROUNDS` | `1` | consensus is never declared before this many debate rounds |
| `MAX_ROUNDS` | `5` | hard upper bound; the loop cannot exceed it |
| `CONSENSUS_THRESHOLD` | `0.8` | weighted agreement score required |
| `REQUEST_TIMEOUT` | `60` | seconds per individual model call |
| `MAX_RETRIES` | `2` | extra attempts after the first, for retryable errors only |
| `TEMPERATURE` | `0.7` | sent only to providers that accept it |
| `MAX_TOKENS` | `4000` | per model call |
| `MAX_QUESTION_LENGTH` | `4000` | request validation |
| `SYNTHESIS_AGENT` | `auto` | who writes the synthesis prose (`auto`, or an agent name) |
| `DEBATE_ROLES` | `true` | round table with roles; `false` restores plain parallel opinions |
| `ADVOCATE_AGENT` | `auto` | who plays the ADVOCATE that drives convergence |
| `ANTHROPIC_THINKING` | `disabled` | `disabled` / `adaptive` / `default` |

All of these are overridable per debate from the Settings panel or the API.

**Cost note.** Each debate round costs two calls per agent (debate + consensus
vote), plus one call per agent in round 0 and one for the synthesis. A five-agent,
three-round debate is roughly `5 + 5·2·3 + 1 = 36` calls. Lower `MAX_ROUNDS`
first if you want to spend less.

---

## 10. Adding a new AI provider

Three steps, no changes to the debate engine.

**1. Add the identity** in `backend/models/enums.py`:

```python
class AgentName(str, Enum):
    ...
    MISTRAL = "MISTRAL"
```

and an identity line in `backend/agents/prompts.py::IDENTITY`.

**2. Implement the agent.** If the provider is OpenAI-compatible, this is the
whole file:

```python
# backend/agents/mistral_agent.py
from ..config import get_settings
from ..models.enums import AgentName
from .openai_agent import OpenAICompatibleAgent


class MistralAgent(OpenAICompatibleAgent):
    name = AgentName.MISTRAL
    provider = "mistral"
    structured_output_mode = "json_object"
    default_base_url = "https://api.mistral.ai/v1"

    @classmethod
    def from_settings(cls, *, model=None, **overrides):
        s = get_settings()
        return cls(
            api_key=s.mistral_api_key,
            model=model or s.mistral_model,
            base_url=cls.default_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
```

For a provider with its own SDK, subclass `BaseAgent` and implement the single
`_complete()` coroutine — returning `RawCompletion(text, usage)`. Retries,
timeouts, JSON repair, schema validation, status reporting and token accounting
are all inherited.

**3. Register it** in `backend/agents/registry.py`: add it to `AGENT_CLASSES`,
`ENV_HINTS`, `_key_for` and `default_model_for`; add the two settings fields in
`backend/config.py`. Add a colour for the new agent in
`frontend/src/components/AgentStatusBar.tsx` and the frontend `AgentName` union
in `frontend/src/lib/types.ts`.

---

## 11. API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness |
| `GET` | `/api/settings` | roster, defaults, limits — **never** returns keys |
| `POST` | `/api/debate` | start a debate → `202` with `debate_id` |
| `GET` | `/api/debate/{id}` | full debate: config, rounds, synthesis, cost |
| `GET` | `/api/debate/{id}/rounds` | rounds only |
| `GET` | `/api/debate/{id}/events` | **SSE** live stream (replays history first) |
| `POST` | `/api/debate/{id}/cancel` | stop a running debate |
| `GET` | `/api/debate/{id}/export?fmt=markdown\|json` | download the report |
| `DELETE` | `/api/debate/{id}` | delete one debate |
| `GET` | `/api/debates` | history |
| `DELETE` | `/api/debates` | clear all history |

Interactive docs: <http://localhost:8000/docs>.

### Live events

`GET /api/debate/{id}/events` is a Server-Sent Events stream. Events are
persisted, so a client that connects late — or reconnects — replays the whole
run before following live updates.

```
debate_started · round_started · agent_started · agent_finished · agent_failed
consensus_check · round_finished · synthesis_started · debate_finished · debate_error
```

Example:

```bash
curl -N http://localhost:8000/api/debate/dbt_abc123/events
```

### Database

SQLite by default. Tables: `conversations`, `debates`, `rounds`,
`agent_responses`, `consensus_results`, `token_usage`, `debate_events`.
To move to PostgreSQL:

```bash
pip install asyncpg
# .env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/aicouncil
```

Every column type used maps cleanly to PostgreSQL; no code changes are needed.

---

## 12. Costs

Token usage is recorded per agent per call and shown in the UI:

```
TOKENS
OPENAI     6 643
GROK       5 053
DEEPSEEK   4 948
TOTAL     16 644
```

**Prices are not hardcoded and are not invented.** They live in
`backend/pricing.json` (path overridable with `PRICING_FILE`) as USD per
1,000,000 tokens. Any model with no entry — or a `null` entry — is reported as
*pricing not configured* and is **never** silently counted as $0; the UI names
the models that need prices. Look the current numbers up on each provider's
pricing page and edit the file; only a backend restart is required.

```json
{
  "currency": "USD",
  "models": {
    "gpt-4o": { "input_per_1m": 2.5, "output_per_1m": 10.0 }
  }
}
```

Keys match exactly first, then by longest prefix, so `gpt-4o` also covers
`gpt-4o-2024-11-20`.

---

## 13. Security notes

* **Keys stay on the backend.** They are read from the environment, never
  serialised into any response, and never logged. `/api/settings` exposes a
  boolean `configured` flag per provider and nothing else.
* **CORS** is an explicit allow-list (`CORS_ORIGINS`), not `*`.
* **Rate limiting**: sliding window per client IP on `POST /api/debate`
  (`RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`).
* **Input validation**: question length, agent selection, round bounds, threshold,
  timeout and retry counts are all clamped server-side — the client cannot ask
  for a 500-round debate.
* **Timeouts and retries** are bounded per call; deterministic failures
  (auth, bad request, unknown model) are never retried.
* **Container** runs as a non-root user.
* The app is designed for local / trusted-network use. There is no authentication
  layer — put it behind one before exposing it to the internet.

---

## 14. Troubleshooting

**"None of the selected AI providers has an API key configured"**
`.env` is missing or has no keys. It must sit next to `docker-compose.yml`
(the repository root). Restart the backend after editing it.

**An agent shows `ERROR: model_not_found`**
The model name in `.env` does not exist for that provider or your key lacks
access. Model names change often — check the provider's model list and update
the corresponding `*_MODEL` variable.

**An agent shows `INVALID_OUTPUT`**
The model returned something that could not be repaired into valid JSON even
after an explicit re-ask. The debate continues without it for that round and it
is re-invited next round. Very small or heavily quantised models do this;
switch that agent to a stronger model.

**Live updates arrive all at once at the end**
Something is buffering the SSE stream. In Docker this is handled
(`proxy_buffering off`); if you added your own reverse proxy, disable buffering
for `/api/`.

**Consensus is never reached**
That is a legitimate outcome — the engine will not fake it. Check the `reason`
on each round's consensus strip: it names exactly what is missing (too few
agreeing agents, a material objection, a score below threshold, or discarded
unsubstantiated agreement). Raise `MAX_ROUNDS`, or lower
`CONSENSUS_THRESHOLD` if your threshold is unrealistic for the question.

**Cost shows `$0.0000` / "cost is partial"**
No prices are configured for those models — see [Costs](#12-costs). This is
deliberate: the app will not guess a price.

---

## License

Provided as-is for you to use and modify.
