# Docker Setup

## Model serving (Ollama)

Models are served by **Ollama**, not the old vLLM compose stack.

```bash
# Install: https://ollama.com/download
# Ensure the service is running, then pull required models:
python scripts/download_models.py

# Sanity check
curl http://127.0.0.1:11434/v1/models
```

Configure endpoints in `config/models.yaml` (`ollama_host`, roles → model names).

Default mapping:

| Role | Ollama model | Purpose |
|---|---|---|
| reasoning | `qwen3.6:35b` | Meal planning + tools |
| vision / ocr | `qwen3-vl:8b` | Ingredient photos + receipt OCR |
| embedding | `nomic-embed-text` | Ingredient canonicalization |

## App machine

```bash
docker compose -f docker/docker-compose.app.yml up -d
```

App will be available on port 80 of this machine.

## SearXNG (optional web search)

```bash
docker run -d --name searxng \
  -p 8080:8080 \
  searxng/searxng

# Then enable in config/models.yaml:
#   features:
#     web_search: true
#     searxng_url: "http://<searxng-host>:8080"
```
