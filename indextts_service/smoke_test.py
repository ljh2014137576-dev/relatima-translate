"""Smoke test: load IndexTTS2 and synthesize one Chinese sentence from a reference voice."""
import os
import sys
import time

import torch

from indextts.infer_v2 import IndexTTS2


def main():
    start = time.time()
    print(f"[smoke] device cuda avail: {torch.cuda.is_available()}", flush=True)
    tts = IndexTTS2(
        cfg_path=os.path.join("checkpoints", "config.yaml"),
        model_dir="checkpoints",
        use_fp16=True,
        use_cuda_kernel=False,
        use_deepspeed=False,
    )
    print(f"[smoke] model loaded in {time.time() - start:.1f}s", flush=True)

    ref = sys.argv[1] if len(sys.argv) > 1 else os.path.join("dubbing", "samples", "jfk.wav")
    text = sys.argv[2] if len(sys.argv) > 2 else "大家好，我是人工智能配音助手。现在正在用克隆的声音说中文。"
    out = sys.argv[3] if len(sys.argv) > 3 else "smoke_out.wav"

    t0 = time.time()
    tts.infer(spk_audio_prompt=ref, text=text, output_path=out, verbose=False)
    dt = time.time() - t0
    print(f"[smoke] inferred {out} in {dt:.1f}s", flush=True)
    if os.path.exists(out):
        print(f"[smoke] size: {os.path.getsize(out)} bytes", flush=True)


if __name__ == "__main__":
    main()
