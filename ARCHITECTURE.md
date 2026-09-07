# Daily Meal Planner — Architecture

> **Live document.** Updated alongside every significant code change.
> Last updated: 2026-09-07 (web search via Tavily/SearXNG; prewarmed suggestions; streaming suggest)

---

## Overview

A locally-hosted, LAN-accessible weekly meal-planning web app. An AI agent backed by local Ollama ingests pantry stock from receipts, photos, and manual entry, then suggests meals on demand. No cloud inference. No accounts. Single household.

---

## System topology

```
Phone / tablet on LAN
        │  HTTP
        ▼
┌─────────────────────────┐
│  App machine (this PC)  │
│  ─────────────────────  │
│  React PWA  :5173       │
│      │  /api/v1         │
│  FastAPI    :8000       │
│      │  SQLite          │
│  daily_meal.db          │
└──────────┬──────────────┘
           │  LAN  (OpenAI-protocol REST)
           ▼
┌──────────────────────────────────────┐
│  Ollama (:11434 OpenAI-compatible)   │
│  ────────────────────────────────    │
│  qwen3.6:35b     reasoning / tools   │
│  qwen3-vl:8b     vision + OCR        │
│  nomic-embed-text  embeddings        │
│  SearXNG :8080 (optional, off by default)
└──────────────────────────────────────┘
```

---

## Model allocation (Ollama)

| Model | Role |
|---|---|
| `qwen3.6:35b` | Agent tool-calling / meal planning |
| `qwen3-vl:8b` | Ingredient photos + receipt OCR |
| `nomic-embed-text` | Ingredient canonicalization embeddings |

All roles are swappable via `config/models.yaml` — the app speaks plain OpenAI protocol against Ollama (`:11434/v1`).

**Trade-off:** Dropped specialized PaddleOCR-VL in favor of one VL model under Ollama — simpler ops, slightly weaker dense thermal receipt OCR.

---

## Key classes and functions

### Backend — `app/`

| Module | Key class / function | Purpose |
|---|---|---|
| `core/config.py` | `Settings`, `get_settings()` | Config singleton loaded from `models.yaml` |
| `db/session.py` | `get_session()`, `engine` | SQLAlchemy session factory (FastAPI dependency) |
| `db/seed.py` | `seed()` | Seeds canonical ingredient table with aliases, units, densities |
| `models/canonical_ingredient.py` | `CanonicalIngredient`, `IngredientAlias` | Ingredient identity; aliases map OCR output → canonical name |
| `models/stock.py` | `StockLot` | One row per grocery haul line; FIFO depletion |
| `models/meal.py` | `Dish`, `MealPlan`, `PlannedMeal` | Weekly plan structure; audience (main/toddler) |
| `models/session.py` | `PlanningSession` | Holds clarification budget (`questions_asked`, `questions_max=3`) |
| `services/llm_client.py` | `LLMClient` | Async OpenAI-protocol client; `chat()`, `chat_json()`, `chat_tools()`, `embed()`, `ocr()`, `vision_chat()` |
| `services/canonicalize.py` | `canonicalize()`, `write_alias()` | 3-stage pipeline: exact alias → embedding NN → LLM fallback |
| `services/unit_utils.py` | `convert()`, `display()` | Unit arithmetic via `pint`; volume↔mass via density |
| `services/receipt_parser.py` | `parse_receipt()` | OCR → classify → canonicalize → ReceiptLine rows |
| `services/pantry.py` | `get_pantry_summary()`, `add_stock_*()` | Pantry CRUD; lot-aware quantity aggregation |
| `services/cook.py` | `cook_meal()`, `confirm_exhaustion()` | FIFO deduction, exhaustion candidates, user confirmation |
| `services/prewarm.py` | `prewarm_loop()`, `generate()`, `fingerprint()` | Background pre-generation + cache for the current slot |
| `agents/planner_graph.py` | `get_planner_graph()`, `planner_mermaid[_png]()` | Compiled LangGraph + Mermaid/PNG export |
| `agents/planner_nodes.py` | `load_context`, `shortlist_dishes`, `allocate_inventory`, … | Graph node implementations |
| `agents/planner.py` | `MealPlannerAgent.suggest()`, `.astream_suggest()` | Facade: `ainvoke` / `astream` the planner graph with db/llm config |
| `agents/inventory.py` | `InventoryAllocator.allocate()` | Deterministic Python stock reservation (no LLM arithmetic) |
| `agents/safety.py` | `validate_toddler_dish()` | Hard-coded CDC/NHS toddler safety rules; never delegated to LLM |
| `agents/tools.py` | `ToolExecutor`, `get_active_tools()` | Tool definitions + code-enforced `ClarificationBudget(max=3)` |

### Frontend — `src/`

| File | Purpose |
|---|---|
| `pages/PantryPage.tsx` | Pantry list, manual add, photo upload, lot removal |
| `pages/MealsPage.tsx` | Suggest button, pending questions, meal cards, cook+deplete flow |
| `pages/ReceiptsPage.tsx` | Receipt photo upload, line-by-line review, confirm to pantry |
| `pages/HouseholdPage.tsx` | Member profiles (kind, age, dietary notes) |
| `components/layout/BottomNav.tsx` | Mobile-first bottom navigation |
| `lib/utils.ts` | `apiFetch()` helper; `API_BASE` constant |

---

## Domain model (ERD summary)

```
CanonicalIngredient ──< IngredientAlias
       │
       └──< StockLot ──< CookDeduction ──> CookEvent ──> PlannedMeal ──> Dish
                │                                                │
                └── (receipt_line_id) ──> ReceiptLine ──> Receipt
                
HouseholdMember   (standalone; loaded into planner context)
PlanningSession   (per suggest() call; holds clarification budget)
MealPlan ──< PlannedMeal
SuggestionCache   (one row per slot; prewarmed plan + input fingerprint)
```

---

## Core design decisions and trade-offs

### LLM proposes, Python decides
The model emits dish candidates with ingredient quantities. `InventoryAllocator` does all stock arithmetic in Python. This is the most important correctness boundary — local 32B models reliably make arithmetic mistakes.

**Trade-off:** The model can propose ingredients it doesn't know the canonical names for. The canonicalization pipeline bridges this with a 3-stage lookup (exact → embedding → LLM), with alias write-back on user confirmation.

### Three-stage planning instead of one big prompt
Shortlist → allocate → write recipes. Each stage is a small, focused prompt. Failures retry one stage. This keeps token counts low and avoids a single mega-prompt that degrades quality on 32B models.

**Trade-off:** Three round-trips to the GPU box instead of one. At ~30 tok/s for a 32B model this costs 15–60s of generation. Acceptable for an on-demand suggestion flow.

### Toddler safety is hard-coded
`validate_toddler_dish()` enforces CDC/NHS rules without asking the model. The model is prompted to follow safety rules, but Python validates the output and drops non-compliant dishes.

**Trade-off:** The rule list in `agents/safety.py` must be maintained manually. This is the correct trade-off: a hard-coded set of 15 rules is maintainable; trusting a local 32B model with a child's safety is not.

### SQLite over Postgres
One household, one writer, local disk. SQLAlchemy abstracts the connection string so upgrading to Postgres is a config change.

**Trade-off:** No concurrent writes. Acceptable for a single-household app; becomes a constraint if this is ever multi-tenant.

### Clarification budget enforced in code
`ToolExecutor._ask_ingredient()` rejects calls that would exceed `PlanningSession.questions_max`. Prompt instructions supplement this but are not relied upon.

**Trade-off:** The budget is session-scoped, not global. A user can start a new session and get 3 more questions. This is the intended behavior — each suggestion request is fresh.

### Receipt confirmation is mandatory
No receipt line ever auto-commits to the pantry. All lines go through a review screen. A silent OCR error (e.g. `BTRSCCH` read as "butterscotch" instead of "broccoli") corrupts stock and then corrupts every downstream suggestion.

### Web search off by default
`features.web_search: false` in `models.yaml`. This is the only feature that sends data off the machine, so enabling it knowingly trades the local-only property for better dish variety. See "Web search" below for the providers and how results are constrained.

---

## Not in scope (first pass)

- Nutrition tracking beyond energy-based portion scaling
- Allergy management beyond toddler rules
- Multi-household / login
- Cloud deployment
- Push notifications for low stock
- Barcode scanning

---


## Meal planner graph (LangGraph)

```
START → load_context → search_web → shortlist ─┬─→ rank_by_cuisine → allocate ─┬─→ write_recipes → END
                                               │                               └─→ ask_clarifications → END
                                               └─→ END
```

**Cuisine priority.** `preferences.cuisine_priority` in `config/models.yaml` lists cuisines
most-preferred first (default: indian, mexican, italian). The model tags each shortlisted
dish with a `cuisine`; `rank_by_cuisine` then re-orders deterministically in Python, so
ranking never depends on the model obeying an instruction. Main and toddler dishes are
ranked independently so a toddler dish is not pushed out by better-ranked adult dishes.
Unlisted cuisines rank last, and ties keep the model's original order.
The chosen tag is persisted on `Dish.cuisine` (migration `0002_add_dish_cuisine`)
and returned in the `/meals/suggest` response.

- **Trade-off:** Graph topology is explicit and exportable (Mermaid); node I/O stays in Python with the existing `LLMClient` (Ollama) rather than LangChain chat models — fewer moving parts, same local inference path.
- **Export:** `GET /api/v1/meals/graph/mermaid`, `GET /api/v1/meals/graph/mermaid.png`, or `python scripts/export_planner_graph.py` → `docs/planner_graph.{mmd,png}`.

### Streaming progress (SSE)

`write_recipes` issues **one LLM call per feasible dish, sequentially**, so a plan
costs `1 + N` generations. On the local 35B model that is roughly a minute per
dish — several minutes before a blocking request returns anything.

`POST /api/v1/meals/suggest/stream` runs the same graph via `astream` and reports
progress as it goes. Nodes call `_emit()` in `planner_nodes.py`, which wraps
LangGraph's `get_stream_writer()`; outside a streaming run it is a no-op, so the
identical node code still serves the blocking `POST /meals/suggest`.

Event types, one JSON object per SSE frame:

| `type` | Payload | Emitted by |
|---|---|---|
| `status` | `step`, human-readable `message` | every node |
| `meal` | one finished `PlannedMeal` | `write_recipes`, per dish |
| `safety` | report for a dropped toddler dish | `write_recipes` |
| `done` | the full `SuggestResult` | facade, after the graph ends |
| `error` | `message` | route, on an unhandled exception |

- **Trade-off — perceived vs. actual latency.** Nothing got faster; the first
  recipe simply renders at ~1 min instead of ~4. Real speedups would mean running
  the per-dish calls concurrently (needs `OLLAMA_NUM_PARALLEL` and a smaller
  `MAX_SHORTLIST_DISHES`), which is not done yet.
- **Trade-off — per-dish commit.** `write_recipes` commits after each dish rather
  than once at the end, because a streamed card is clickable immediately and its
  row must be durable if the client disconnects. A cancelled run therefore leaves
  a partial `MealPlan` instead of rolling back.
- **Trade-off — own DB session.** The route opens `Session(engine)` inside the
  generator instead of using the `get_session` dependency: FastAPI tears down
  yield-dependencies before a streaming body finishes, so the request-scoped
  session would already be closed.
- **Client:** `apiStream()` in `frontend/src/lib/utils.ts` parses SSE by hand
  (`EventSource` is GET-only and planning needs a POST body).

### Prewarmed suggestions

Streaming improved *perceived* latency but the wait was still real. The plan for
the current slot is therefore generated **before** the user asks:

- `preferences.max_suggestions` (default 3) caps the plan. Each dish is a full
  generation, so this is the single biggest lever on planning time — it was
  previously fixed at 10.
- A background worker (`prewarm_loop`, started in the app lifespan) keeps the
  current slot warm, re-checking every `features.prewarm_interval_s`. The
  interval catches both the clock crossing into a new slot and a pantry change.
- The slot comes from the **local** hour, matching how the client picks its
  default. This was previously `datetime.utcnow().hour`, which chose the wrong
  meal on any machine not on UTC.
- `POST /meals/suggest` returns the cached plan when valid; measured at ~5 ms
  against ~172 s for a live generation. `GET /meals/suggest/status` reports
  readiness so the UI can say whether pressing Suggest is instant.
- `POST /meals/suggest/stream` replays a cached plan as `meal` events followed
  by `done`, so the client has one code path either way.

**Freshness is a fingerprint, not a TTL.** `fingerprint()` hashes pantry stock,
active members, cuisine priority and plan size. Cooking a meal changes stock and
invalidates the cache immediately; an untouched pantry keeps it valid
indefinitely. A time-based TTL would do both jobs badly — expiring plans that
are still perfectly good while serving plans for food already eaten.

- **Trade-off — durable cache.** `SuggestionCache` is a table, not an in-memory
  dict (migration `0003`). A plan costs minutes of GPU time, so a restart or dev
  reload must not discard it. Cost: a migration and a row that duplicates data
  already in `MealPlan`.
- **Trade-off — single-flight.** One `asyncio.Lock` serializes generation, so a
  user request arriving mid-prewarm waits for it and then reads the cache rather
  than queueing a second identical run. The local model serves requests serially
  anyway. Empty plans are *not* cached (a clarification prompt is
  session-specific), so that path does re-run per caller.
- **Trade-off — speculative work.** The worker generates plans nobody may ask
  for, spending idle GPU time and writing `MealPlan` rows that go unused. That
  is the trade being made: idle GPU time is free, user waiting time is not.
- **Escape hatch:** `force: true` on either suggest endpoint bypasses the cache
  ("Suggest something else" in the UI) and takes the full generation time.

### Web search

Without it, recipes are bounded by what the local model already knows. The
`search_web` node fetches recipe ideas before shortlisting and passes them to
the shortlist prompt as inspiration.

Controlled entirely by `features` in `config/models.yaml`:

| Key | Purpose |
|---|---|
| `web_search` | Master on/off. Off by default. |
| `web_search_provider` | `tavily` (hosted) or `searxng` (self-hosted) |
| `tavily_search_depth` | `basic` or `advanced` (more credits) |
| `searxng_url` | Instance URL; needs JSON output enabled |
| `web_search_max_results` | Results fed to the prompt |
| `web_search_timeout_s` | Per-request budget |
| `web_search_daily_limit` | Hard cap on searches per day (0 = never search) |

The Tavily key is a secret, so it comes from `TAVILY_API_KEY` in the
environment rather than the committed YAML. `web_search_ready` is the flag the
code actually checks: enabled *and* usable, so a `tavily` provider with no key
is treated as off instead of failing at request time.

- **Trade-off — leaving the machine.** Everything else in this app is local.
  Tavily sends the query (slot, top cuisine, pantry ingredient names) to a third
  party. `searxng` exists as the provider for anyone who wants the feature
  without that; it is self-hosted and keeps queries on the LAN.
- **Trade-off — inspiration, not instruction.** Search results routinely mention
  ingredients that are not in stock. The prompt marks them as inspiration only
  and repeats that dishes must come from the pantry, and `InventoryAllocator`
  still rejects anything unaffordable afterwards. The model is never given a
  path to invent stock.
- **Failure is not an error.** Missing key, unreachable host, timeout, unknown
  provider and non-JSON responses all return `[]` and log a warning; planning
  proceeds on the model's own knowledge. A recipe suggestion is not worth
  failing a request over.
- **Note:** the older `search_recipes` *tool* (in `agents/tools.py`, for
  `chat_tools` tool-calling) is still unused. The graph node calls
  `search_recipes_web()` directly, which is deterministic — Python decides when
  to search rather than hoping a local model emits a correct tool call.
- **Tests:** `backend/tests/test_web_search.py` covers the flag, provider
  dispatch, bearer auth, snippet truncation and the failure fallback with a
  stubbed HTTP layer (no network, no key).

### Spending controls

Tavily bills per search and trial keys are small, so an unattended background
loop is a financial risk, not just a performance one. Two mechanisms bound it.

**Daily cap** (`services/search_budget.py`). Every search claims one unit via
`try_consume()` before any HTTP request; when the day's allowance is gone the
search is skipped and planning proceeds without it. The counter lives in a JSON
file under `data_dir`, *not* in memory — a restart or dev reload must not hand
out a fresh quota, since restarts are exactly when a runaway loop resets itself.
`GET /meals/suggest/status` reports `used_today` / `daily_limit`.

**Failure cooldown** (`prewarm._failures`). Empty plans are deliberately not
cached, which created a leak: an unplannable slot — a pantry too bare for the
model to find `max_suggestions` feasible dishes — was retried every
`prewarm_interval_s` indefinitely, spending a search and minutes of GPU on each
attempt. At the 300s default that is up to 288 searches/day. After a failed run
the slot is now left alone for `prewarm_failure_cooldown_s` (1 hour).

Expected steady-state usage is roughly **3–6 searches/day**: one per meal slot
as the clock crosses into it, plus one after each cook invalidates the pantry
fingerprint. The cap defaults to 20 to leave headroom without being a real
budget.

- **Trade-off — the cap is a backstop, not a scheduler.** It cannot tell a
  useful search from a wasteful one; it just stops the bleeding. The cooldown
  is what actually removes the waste.
- **Trade-off — file over table.** The counter could live in SQLite next to
  `suggestion_cache`, but a JSON file needs no migration and is trivially
  inspectable (`cat backend/data/search_budget.json`). It assumes one process,
  which already holds for this app.

## Local run

```bash
./scripts/dev.sh   # uvicorn :8000 + vite :5173 (Ctrl+C stops both)
```

Uses repo-root `.venv` and `frontend/node_modules`. LLM calls go to local Ollama (`:11434/v1`).

## Configuration reference

All model configuration lives in `config/models.yaml`. Key fields:

```yaml
ollama_host: "127.0.0.1"

roles:
  reasoning:
    base_url: "http://{ollama_host}:11434/v1"
    model: "qwen3.6:35b"
  vision / ocr:
    base_url: "http://{ollama_host}:11434/v1"
    model: "qwen3-vl:8b"
  embedding:
    base_url: "http://{ollama_host}:11434/v1"
    model: "nomic-embed-text"

features:
  web_search: false               # enable after SearXNG is running
```

Changing `model` or `base_url` under any role is the only thing needed to swap models.
