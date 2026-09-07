# Daily Meal Planner — Architecture

> **Live document.** Updated alongside every significant code change.
> Last updated: 2026-09-07 (switched model serving to Ollama)

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
| `agents/planner.py` | `MealPlannerAgent.suggest()` | 3-stage planning: shortlist → allocate → write recipes |
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
`features.web_search: false` in `models.yaml`. SearXNG is self-hosted and must be explicitly enabled, preserving the local-only property.

---

## Not in scope (first pass)

- Nutrition tracking beyond energy-based portion scaling
- Allergy management beyond toddler rules
- Multi-household / login
- Cloud deployment
- Push notifications for low stock
- Barcode scanning

---

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
