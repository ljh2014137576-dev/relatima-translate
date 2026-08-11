"""Sequential audio player backed by sounddevice.

Plays synthesized WAVs (16-bit PCM mono) in FIFO order on the system output.
The queue_seconds knob holds each chunk at least that long after it was
produced, so the dubbed voice trails the video instead of racing it.
"""
import io
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd


class AudioPlayer:
    def __init__(self, cfg):
        self.q = queue.Queue()  # items: (wav_bytes, produced_timestamp)
        self.cfg = cfg
        self._stop = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def enqueue(self, wav_bytes):
        self.q.put((wav_bytes, time.time()))

    def _decode(self, wav_bytes):
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            sr = w.getframerate()
            channels = w.getnchannels()
            sw = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if sw != 2:
            raise RuntimeError(f"unsupported sample width: {sw}")
        arr = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            arr = arr.reshape(-1, channels).mean(axis=1)
        return arr.astype(np.float32) / 32768.0, sr

    def _loop(self):
        while not self._stop:
            try:
                wav_bytes, ts = self.q.get(timeout=0.5)
            except queue.Empty:
                continue

            delay = self.cfg["playback"]["queue_seconds"] - (time.time() - ts)
            if delay > 0:
                time.sleep(delay)

            try:
                audio, sr = self._decode(wav_bytes)
                device = self.cfg["playback"].get("device")
                sd.play(audio, samplerate=sr, device=device)
                sd.wait()
                gap = self.cfg["playback"].get("sentence_gap", 0.0)
                if gap > 0:
                    time.sleep(gap)
            except Exception as e:
                print(f"[PLAY] error: {e}", flush=True)
