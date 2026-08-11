"""IndexTTS2 HTTP dubbing service.

Loads the IndexTTS2 model once (fp16, no deepspeed/cuda-kernel) and exposes
a simple POST /tts endpoint that returns a WAV of the synthesized speech.

Run (from the index-tts project dir):
    uv run /path/to/indextts_service/server.py --host 127.0.0.1 --port 50001
"""
import argparse
import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import uvicorn

from indextts.infer_v2 import IndexTTS2

app = FastAPI(title="IndexTTS2 dubbing service")

# Global model + lock. IndexTTS2 generates one utterance at a time; all
# requests must serialize on this lock to avoid GPU memory races.
_tts = None
_tts_lock = threading.Lock()
_ready = threading.Event()
_init_error = None


class TTSRequest(BaseModel):
    text: str
    ref_audio: str                # path to the speaker reference wav (3-10s clean)
    emo_audio_prompt: str | None = None  # optional emotional reference wav
    emo_alpha: float | None = None       # 0.0 - 1.0, how much the emo ref affects output
    emo_text: str | None = None          # optional emotion description text


def _load_model(args):
    global _tts, _init_error
    try:
        t0 = time.time()
        _tts = IndexTTS2(
            cfg_path=args.cfg_path,
            model_dir=args.model_dir,
            use_fp16=True,
            use_cuda_kernel=False,
            use_deepspeed=False,
        )
        print(f"[server] model loaded in {time.time() - t0:.1f}s", flush=True)
        _ready.set()
    except Exception as e:
        import traceback
        traceback.print_exc()
        _init_error = str(e)
        _ready.set()


@app.on_event("startup")
async def _startup():
    _load_model_thread = threading.Thread(target=_load_model, args=(app.state.args,), daemon=True)
    _load_model_thread.start()


@app.get("/health")
async def health():
    if not _ready.is_set():
        return {"status": "loading"}
    if _init_error:
        return {"status": "error", "error": _init_error}
    return {"status": "ok"}


@app.post("/tts", response_class=Response)
async def tts(req: TTSRequest):
    if not _ready.is_set():
        raise HTTPException(status_code=503, detail="model still loading")
    if _init_error:
        raise HTTPException(status_code=500, detail=f"model init failed: {_init_error}")

    text = req.text.strip()
    if len(text) < 2:
        raise HTTPException(status_code=400, detail="text too short")

    ref = req.ref_audio
    if not os.path.isfile(ref):
        raise HTTPException(status_code=400, detail=f"ref_audio not found: {ref}")

    out_dir = tempfile.mkdtemp(prefix="indextts_")
    out_path = os.path.join(out_dir, "out.wav")

    try:
        kwargs = {"spk_audio_prompt": ref, "text": text, "output_path": out_path}
        if req.emo_audio_prompt:
            if not os.path.isfile(req.emo_audio_prompt):
                raise HTTPException(status_code=400, detail="emo_audio_prompt not found")
            kwargs["emo_audio_prompt"] = req.emo_audio_prompt
        if req.emo_alpha is not None:
            kwargs["emo_alpha"] = max(0.0, min(1.0, req.emo_alpha))
        if req.emo_text:
            kwargs["emo_text"] = req.emo_text

        def _infer():
            with _tts_lock:
                return _tts.infer(verbose=False, **kwargs)

        # infer blocks the event loop if run inline; run in a worker thread.
        result = await asyncio.get_running_loop().run_in_executor(None, _infer)

        if result is None or not os.path.isfile(out_path):
            raise HTTPException(status_code=500, detail="synthesis produced no output")

        data = Path(out_path).read_bytes()
        return Response(content=data, media_type="audio/wav")
    finally:
        try:
            for f in os.listdir(out_dir):
                os.remove(os.path.join(out_dir, f))
            os.rmdir(out_dir)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="IndexTTS2 dubbing HTTP service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=50001)
    ap.add_argument("--cfg-path", default="checkpoints/config.yaml")
    ap.add_argument("--model-dir", default="checkpoints")
    args = ap.parse_args()

    app.state.args = args
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
