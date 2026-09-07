# Daily Meal Planner

A locally-hosted, LAN-accessible weekly meal-planning app powered by a local AI agent (Ollama). Suggests meals based on what is in the pantry, scaled to household composition, with separate toddler-safe meals. No cloud inference. No accounts.

---

## Quick start (development)

### One command (both servers)

```bash
./scripts/dev.sh
```

Backend: `http://127.0.0.1:8000` · Frontend: `http://127.0.0.1:5173`

### Manual setup

### Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.12+ |
| Node | 20+ |
| Docker + Compose | v2+ |
| Ollama | installed + running locally (or on LAN) |

### 1. Clone and configure

```bash
git clone <repo> daily-meal && cd daily-meal
cp config/models.yaml.example config/models.yaml   # set ollama_host if remote
cp backend/.env.example backend/.env
```

### 2. Pull Ollama models

```bash
# ensure `ollama serve` is running, then:
python scripts/download_models.py
```

See [docker/README.md](docker/README.md) for optional Docker app packaging notes.

### 3. Start the backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.db.seed          # seed canonical ingredients
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev -- --host          # --host makes it reachable on LAN (phone)
```

Open `http://<this-machine-ip>:5173` on any device on the LAN.

---

## Running on a smaller GPU

The default [config/models.yaml](config/models.yaml) is tuned for the 2x4090 box. To run on a single smaller card, point `CONFIG_PATH` at one of the ready-made profiles in [config/profiles/](config/profiles/):

```bash
# in backend/.env
CONFIG_PATH=../config/profiles/16gb.yaml
```

| Profile | Reasoning | Vision + OCR | Total VRAM |
|---|---|---|---|
| `24gb.yaml` | `qwen3:30b-a3b` | `qwen3-vl:8b` | ~25.3 GB |
| `16gb.yaml` | `qwen3:14b` | `qwen3-vl:4b` | ~12.8 GB |
| `12gb.yaml` | `qwen3:8b` | `qwen3-vl:4b` | ~9.0 GB |
| `8gb.yaml` | `qwen3:4b` | `qwen3-vl:2b` | ~5.2 GB |

Inventory arithmetic and toddler safety are plain Python, so a smaller model cannot corrupt your pantry or produce an unsafe toddler dish — it only affects the quality and variety of what gets suggested. See [config/profiles/README.md](config/profiles/README.md) for the per-tier tradeoffs and the `ollama pull` commands.

---

## Improving suggestions over time

The app learns from what you actually cook. Cooking a meal, re-rolling a plan, or leaving a suggestion untouched are all recorded automatically; the thumbs on each meal card are there to sharpen those weaker signals, not to carry the loop on their own.

Learned preference re-ranks future suggestions and adds a short hint to the planner's prompt. It is deliberately slow to move — a scope needs several consistent signals before it shifts anything, so one disappointing dinner will not banish a whole cuisine.

Inspect what it has learned, or measure whether it is working:

```bash
curl localhost:8000/api/v1/feedback/preferences   # learned likes and dislikes
curl localhost:8000/api/v1/feedback/metrics       # cook-through rate and friends
```

`cook_through_rate` — how many suggested meals actually got made — is the number to watch. `repetition_rate` is the one to watch out for: if it climbs, suggestions have narrowed onto the same few dishes.

To turn the learning off while still recording and measuring feedback, set `learn_from_feedback: false` under `features` in your config. That is also the baseline to compare against if you want to know whether the loop is helping.

### Checking suggestion quality after a change

`backend/app/eval/` runs fixture households through the real planner and scores the plans on structural checks — invented ingredients, uncookable recipes, toddler safety violations. It needs a running Ollama and takes minutes.

```bash
cd backend
python -m app.eval.runner                          # current config
python -m app.eval.runner --profiles \
    ../config/models.yaml ../config/profiles/12gb.yaml
```

The second form is how to answer "does the smaller profile actually produce worse plans". See [`backend/app/eval/README.md`](backend/app/eval/README.md).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the live design document.

## Project layout

```
daily-meal/
├── backend/              FastAPI application
│   ├── app/
│   │   ├── api/routes/   REST endpoints
│   │   ├── agents/       LLM agent & tools
│   │   ├── core/         config, settings
│   │   ├── db/           session, migrations, seed
│   │   ├── eval/         offline suggestion-quality harness
│   │   ├── models/       SQLModel entities
│   │   ├── schemas/      Pydantic I/O schemas
│   │   └── services/     business logic
│   └── alembic/
├── frontend/             React + Vite + Tailwind
│   └── src/
├── config/
│   ├── models.yaml       LLM role → endpoint mapping
│   └── profiles/         per-VRAM-tier model configs
├── docker/
│   ├── docker-compose.models.yml   (deprecated; use Ollama)
│   └── docker-compose.app.yml      App stack (backend + frontend)
└── scripts/              helper scripts
```
