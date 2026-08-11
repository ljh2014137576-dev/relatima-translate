"""HTTP client for the IndexTTS2 dubbing service (POST /tts -> WAV bytes)."""
import os

import requests


class TTSClient:
    def __init__(self, cfg, config_dir=None):
        self.url = cfg["tts"]["url"]
        self.timeout = cfg["tts"].get("timeout", 600)
        # Resolve the reference audio to an absolute path. config_dir defaults
        # to the directory of this module so `refs/xxx.wav` works regardless of
        # where the IndexTTS2 server process is running.
        config_dir = config_dir or os.path.dirname(os.path.abspath(__file__))
        ref = cfg["tts"]["ref_audio"]
        self.ref_audio = os.path.abspath(os.path.join(config_dir, ref))

    def synthesize(self, text, emo_alpha=None):
        payload = {"text": text, "ref_audio": self.ref_audio}
        if emo_alpha is not None:
            payload["emo_alpha"] = emo_alpha
        resp = requests.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        if not resp.content:
            raise RuntimeError("empty wav from TTS service")
        return resp.content
