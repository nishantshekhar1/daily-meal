#!/usr/bin/env python3
"""Download all required model weights to the local cache.

Run this on the GPU box before starting the vLLM model servers.

Usage:
    python scripts/download_models.py [--models-dir ./docker/models]
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("Install huggingface_hub first:  pip install huggingface_hub")
    sys.exit(1)

# Exact repo IDs to pin — verify and update these before first run.
# AWQ quants exist under official Qwen org or community mirrors.
MODELS = [
    {
        "repo_id": "Qwen/Qwen3-VL-32B-Instruct-AWQ",
        "description": "Main reasoning + vision model (~20GB weights)",
        "required": True,
    },
    {
        "repo_id": "PaddlePaddle/PaddleOCR-VL-1.6",
        "description": "Receipt OCR specialist (~2GB)",
        "required": True,
    },
    {
        "repo_id": "Qwen/Qwen3-Embedding-0.6B",
        "description": "Embedding model for ingredient canonicalization (~1GB)",
        "required": True,
    },
]


def download(models_dir: str) -> None:
    token = os.getenv("HUGGING_FACE_HUB_TOKEN")
    for m in MODELS:
        print(f"\n── {m['repo_id']}  ({m['description']}) ──")
        try:
            path = snapshot_download(
                repo_id=m["repo_id"],
                cache_dir=models_dir,
                token=token,
            )
            print(f"   ✓ Downloaded to: {path}")
        except Exception as e:
            if m["required"]:
                print(f"   ✗ FAILED (required): {e}")
                sys.exit(1)
            else:
                print(f"   ! Failed (optional): {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download model weights")
    parser.add_argument("--models-dir", default="./docker/models", help="Local cache directory")
    args = parser.parse_args()
    os.makedirs(args.models_dir, exist_ok=True)
    download(args.models_dir)
    print("\nAll models downloaded successfully.")
