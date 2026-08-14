"""Cloud ASR via OpenRouter (OpenAI-compatible) Whisper large-v3.

The API is batch-only (whole audio file per request), so the glue layer feeds
short overlapping windows of PCM and extracts the newly-transcribed delta.
"""
import io
import os
import wave

import requests


class OpenRouterWhisper:
    def __init__(self, cfg):
        asr = cfg.get("asr", {})
        self.api_key = asr.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = asr.get("model", "openai/whisper-large-v3")
        self.base_url = asr.get("base_url", "https://openrouter.ai/api/v1")
        self.timeout = asr.get("timeout", 60)
        if not self.api_key:
            raise RuntimeError("OpenRouter key missing: set OPENROUTER_API_KEY or asr.openrouter_api_key")

    def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000) -> str:
        """Transcribe 16-bit mono PCM bytes -> text."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
        wav = buf.getvalue()
        resp = requests.post(
            f"{self.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": ("audio.wav", wav, "audio/wav")},
            data={"model": self.model},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
