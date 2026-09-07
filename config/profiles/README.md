# GPU profiles

Ready-made model configurations for running on a single GPU smaller than the 2x4090 box.

Each file here is a complete replacement for [`config/models.yaml`](../models.yaml). Nothing in the application code changes — `Settings.config_path` in [`backend/app/core/config.py`](../../backend/app/core/config.py) is bound to the `CONFIG_PATH` environment variable, so pointing it at a profile swaps every model role at once.

## Selecting a profile

Set `CONFIG_PATH` in `backend/.env`:

```bash
CONFIG_PATH=../config/profiles/16gb.yaml
```

Then restart the backend. The default (`config/models.yaml`, unchanged) stays tuned for the 2x4090 box.

## What each profile uses

| Profile | Reasoning | Vision + OCR | Total VRAM | Suggestions | Prewarm |
|---|---|---|---|---|---|
| `24gb.yaml` | `qwen3:30b-a3b` | `qwen3-vl:8b` | ~25.3 GB | 3 | on |
| `16gb.yaml` | `qwen3:14b` | `qwen3-vl:4b` | ~12.8 GB | 3 | on |
| `12gb.yaml` | `qwen3:8b` | `qwen3-vl:4b` | ~9.0 GB | 2 | off |
| `8gb.yaml` | `qwen3:4b` | `qwen3-vl:2b` | ~5.2 GB | 2 | off |
| *(default)* `models.yaml` | `qwen3.6:35b` | `qwen3-vl:8b` | 2x4090 | 3 | on |

All profiles use `nomic-embed-text` (~0.3 GB) for embeddings. It is already minimal — shrinking it saves nothing and costs ingredient-matching accuracy.

## Pulling the models

Pick the line matching your profile:

```bash
# 24 GB
ollama pull qwen3:30b-a3b && ollama pull qwen3-vl:8b && ollama pull nomic-embed-text

# 16 GB
ollama pull qwen3:14b && ollama pull qwen3-vl:4b && ollama pull nomic-embed-text

# 12 GB
ollama pull qwen3:8b && ollama pull qwen3-vl:4b && ollama pull nomic-embed-text

# 8 GB
ollama pull qwen3:4b && ollama pull qwen3-vl:2b && ollama pull nomic-embed-text
```

Qwen3-VL requires Ollama 0.12.7 or newer. Check with `ollama --version`.

## What actually degrades

Correctness does not. Inventory arithmetic lives in `InventoryAllocator` and toddler safety in `validate_toddler_dish`, both plain Python — a smaller model cannot put wrong quantities in your pantry or produce an unsafe toddler dish. What changes is the quality and reliability of what gets *suggested*:

- **24 GB** — essentially no perceptible change. `qwen3:30b-a3b` is a mixture-of-experts model, so it decodes at roughly 3B speed while holding well above 8B-class quality. This is the sweet spot.
- **16 GB** — recipes slightly less varied. Shortlisting stays reliable.
- **12 GB** — noticeably more generic recipes. Occasional spurious "not enough stock" when the model names an ingredient that is not in the pantry.
- **8 GB** — intermittent outright failures. See the warning at the top of [`8gb.yaml`](8gb.yaml).

## Two knobs that matter as much as model size

Both live under `preferences` and are already tuned per profile:

- **`max_suggestions`** — a request costs `1 + max_suggestions` LLM generations (one shortlist plus one recipe write each). Going from 3 to 2 removes a quarter of the work.
- **`prewarm_suggestions`** — keeps the reasoning model warm in the background so the app opens with a plan ready. Worth it on a large card; on a small one it competes for VRAM with the vision model, so the smaller profiles turn it off.

## Mixing and matching

Roles are independent. If receipt accuracy matters more to you than planning quality, it is reasonable to run, say, the 12 GB reasoning model alongside the 16 GB vision model — just edit the `model:` line under the role you care about. The only constraint is total VRAM.

## Remote GPU box

Every profile defaults to `ollama_host: "127.0.0.1"`. If Ollama runs on another machine, change that one line — `{ollama_host}` is substituted into all four `base_url` values.
