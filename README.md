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
