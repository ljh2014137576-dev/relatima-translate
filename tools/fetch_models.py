"""Pre-fetch WLK models into the HuggingFace cache so first start is fast.

Downloads via hf-mirror.com by default (fast in China). Set HF_ENDPOINT or
HTTPS_PROXY to override.
"""
import os
import sys

REPOS = [
    "Systran/faster-whisper-small",        # WLK ASR (small)
    "facebook/nllb-200-distilled-600M",    # WLK translation (NLLB)
]


def main():
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from huggingface_hub import snapshot_download

    for repo in REPOS:
        print(f"\n[fetch] {repo} ...", flush=True)
        path = snapshot_download(repo)
        print(f"[fetch] done: {path}", flush=True)

    print("\n[fetch] All WLK models ready.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
