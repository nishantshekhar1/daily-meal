# Docker Setup

## GPU box (Linux, 2x RTX 4090)

### Prerequisites

```bash
# NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

### Download models

```bash
# Set your HuggingFace token if needed
export HUGGING_FACE_HUB_TOKEN=hf_...

pip install huggingface_hub
python scripts/download_models.py
```

### Start model servers

```bash
docker compose -f docker/docker-compose.models.yml up -d

# Verify all three endpoints are healthy:
curl http://localhost:8010/health   # reasoning + vision
curl http://localhost:8011/health   # OCR
curl http://localhost:8012/health   # embedding
```

### Update config

Edit `config/models.yaml` and set `gpu_box_host` to the GPU machine's LAN IP.

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
