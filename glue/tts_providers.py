"""Pluggable TTS backends.

Providers:
  - minimax   : MiniMax T2A v2 (+ voice cloning via /v1/voice_clone)
  - elevenlabs: ElevenLabs text-to-speech (+ voice cloning via /v1/voices/add)
  - edge      : Microsoft Edge free TTS (no cloning, preset Chinese voices)
  - local     : local IndexTTS2 HTTP service (the old setup)

All providers return 16-bit PCM WAV bytes so the glue AudioPlayer is unchanged.
"""
import base64
import io
import json
import os
import time
import wave

import requests


class TTSError(Exception):
    pass


def _wav_from_pcm(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _voice_cache_path(ref_audio: str) -> str:
    return os.path.join(os.path.dirname(ref_audio) or ".", ".voice_cache.json")


def _load_cached_voice(ref_audio: str, provider: str):
    try:
        with open(_voice_cache_path(ref_audio), "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get(provider)
    except Exception:
        return None


def _save_cached_voice(ref_audio: str, provider: str, voice_id: str):
    path = _voice_cache_path(ref_audio)
    cache = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        pass
    cache[provider] = voice_id
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ MiniMax
class MiniMaxTTS:
    def __init__(self, cfg):
        self.api_key = cfg.get("api_key") or os.environ.get("MINIMAX_API_KEY", "")
        self.group_id = cfg.get("group_id") or os.environ.get("MINIMAX_GROUP_ID", "")
        self.model = cfg.get("model", "speech-02-turbo")
        self.voice_id = cfg.get("voice_id", "")
        self.timeout = cfg.get("timeout", 60)
        if not self.api_key or not self.group_id:
            raise TTSError("MiniMax: need api_key + group_id (config or MINIMAX_API_KEY/MINIMAX_GROUP_ID env)")

    def ensure_voice(self, ref_audio):
        if self.voice_id:
            return self.voice_id
        cached = _load_cached_voice(ref_audio, "minimax")
        if cached:
            self.voice_id = cached
            return cached
        with open(ref_audio, "rb") as f:
            resp = requests.post(
                f"https://api.minimaxi.com/v1/voice_clone?GroupId={self.group_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": f},
                data={"voice_id": f"rt_{int(time.time())}", "voice_name": "relatima"},
                timeout=self.timeout,
            )
        j = resp.json()
        code = j.get("base_resp", {}).get("status_code")
        if code not in (0, 200):
            raise TTSError(f"MiniMax voice_clone failed: {code} {j.get('base_resp', {}).get('status_msg')}")
        vid = j.get("voice_id", "")
        if not vid:
            raise TTSError("MiniMax voice_clone returned no voice_id")
        _save_cached_voice(ref_audio, "minimax", vid)
        self.voice_id = vid
        return vid

    def synthesize(self, text, ref_audio=None):
        vid = self.ensure_voice(ref_audio) if ref_audio else self.voice_id
        if not vid:
            raise TTSError("MiniMax: no voice_id (set tts.minimax.voice_id or provide ref_audio)")
        payload = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": vid, "speed": 1.0, "vol": 1.0, "pitch": 0},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "wav", "channel": 1},
        }
        resp = requests.post(
            f"https://api.minimaxi.com/v1/t2a_v2?GroupId={self.group_id}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout,
        )
        j = resp.json()
        code = j.get("base_resp", {}).get("status_code")
        if code not in (0, 200):
            raise TTSError(f"MiniMax t2a failed: {code} {j.get('base_resp', {}).get('status_msg')}")
        audio = j.get("data", {}).get("audio", "")
        if not audio:
            raise TTSError("MiniMax: empty audio response")
        return base64.b64decode(audio)


# ---------------------------------------------------------------- ElevenLabs
class ElevenLabsTTS:
    BASE = "https://api.elevenlabs.io"

    def __init__(self, cfg):
        self.api_key = cfg.get("api_key") or os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice_id = cfg.get("voice_id", "")
        self.model = cfg.get("model", "eleven_multilingual_v2")
        self.timeout = cfg.get("timeout", 60)
        if not self.api_key:
            raise TTSError("ElevenLabs: need api_key (config or ELEVENLABS_API_KEY env)")

    def _h(self):
        return {"xi-api-key": self.api_key}

    def ensure_voice(self, ref_audio):
        if self.voice_id:
            return self.voice_id
        cached = _load_cached_voice(ref_audio, "elevenlabs")
        if cached:
            self.voice_id = cached
            return cached
        with open(ref_audio, "rb") as f:
            resp = requests.post(
                f"{self.BASE}/v1/voices/add",
                headers=self._h(),
                files={"files": f},
                data={"name": "relatima"},
                timeout=self.timeout,
            )
        j = resp.json()
        vid = j.get("voice_id", "")
        if not vid:
            raise TTSError(f"ElevenLabs voices/add failed: {j}")
        _save_cached_voice(ref_audio, "elevenlabs", vid)
        self.voice_id = vid
        return vid

    def synthesize(self, text, ref_audio=None):
        vid = self.ensure_voice(ref_audio) if ref_audio else self.voice_id
        if not vid:
            raise TTSError("ElevenLabs: no voice_id (set tts.elevenlabs.voice_id or provide ref_audio)")
        resp = requests.post(
            f"{self.BASE}/v1/text-to-speech/{vid}",
            headers={**self._h(), "Content-Type": "application/json"},
            json={"text": text, "model_id": self.model, "output_format": "wav_22050hz_pcm_16"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise TTSError(f"ElevenLabs tts failed: {resp.status_code} {resp.text[:200]}")
        return resp.content


# ------------------------------------------------------------------- Edge
class EdgeTTS:
    def __init__(self, cfg):
        import edge_tts
        self._edge = edge_tts
        self.voice = cfg.get("voice", "zh-CN-YunxiNeural")

    def synthesize(self, text, ref_audio=None):
        import asyncio
        tmp = f"edge_{int(time.time() * 1000)}.mp3"
        try:
            asyncio.run(self._edge.Communicate(text, self.voice).save(tmp))
            with open(tmp, "rb") as f:
                mp3 = f.read()
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        # mp3 -> wav 16k mono via ffmpeg
        import subprocess
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", "pipe:0",
             "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
            input=mp3, capture_output=True,
        )
        if proc.returncode != 0:
            raise TTSError(f"EdgeTTS ffmpeg failed: {proc.stderr[:200]}")
        return _wav_from_pcm(proc.stdout, 16000)


# ------------------------------------------------------------------- Local
class LocalTTS:
    def __init__(self, cfg, config_dir=None):
        self.url = cfg.get("url", "http://127.0.0.1:50001/tts")
        self.timeout = cfg.get("timeout", 1800)
        config_dir = config_dir or os.path.dirname(os.path.abspath(__file__))
        self.ref_audio = os.path.abspath(os.path.join(config_dir, cfg.get("ref_audio", "refs/default.wav")))

    def synthesize(self, text, ref_audio=None):
        payload = {"text": text, "ref_audio": ref_audio or self.ref_audio}
        resp = requests.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        if not resp.content:
            raise TTSError("Local TTS: empty response")
        return resp.content


def build_tts(cfg, config_dir=None):
    """Create the configured TTS provider. Returns an object with
    synthesize(text, ref_audio=None) -> wav bytes."""
    tts = cfg.get("tts", {})
    provider = tts.get("provider", "local")
    if provider == "minimax":
        return MiniMaxTTS(tts.get("minimax", {}))
    if provider == "elevenlabs":
        return ElevenLabsTTS(tts.get("elevenlabs", {}))
    if provider == "edge":
        return EdgeTTS(tts.get("edge", {}))
    if provider == "local":
        return LocalTTS(tts, config_dir)
    raise TTSError(f"unknown tts.provider: {provider}")
