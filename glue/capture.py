"""CaptureBuffer: accumulate PCM, transcribe overlapping windows via cloud ASR.

For live audio we transcribe every `chunk_seconds` of *new* audio, using a
window that extends `overlap_seconds` into the already-seen past so sentence
tails are not cut. The delta vs the previous window's transcript (longest
common prefix) is forwarded to the pipeline as new ASR text.
"""
import threading
import time


class CaptureBuffer:
    def __init__(self, cfg, whisper, on_text=None, sample_rate=16000):
        self.cfg = cfg["asr"]
        self.whisper = whisper
        self.on_text = on_text or (lambda key, text: None)
        self.sr = sample_rate
        self.chunk = float(self.cfg.get("chunk_seconds", 8))
        self.overlap = float(self.cfg.get("overlap_seconds", 4))

        self.buf = bytearray()
        self.lock = threading.Lock()
        self._last_pos = 0  # bytes already covered by a window
        self._prev_text = ""
        self._window_idx = 0
        self._stopped = False

    # -- ingestion --------------------------------------------------------
    def feed(self, data: bytes):
        with self.lock:
            self.buf += data

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stopped = True

    def flush_now(self):
        """Transcribe whatever remains in the buffer (end of a session)."""
        self._process(full=True)

    def run(self):
        while not self._stopped:
            self._process(full=False)
            time.sleep(0.8)
        self._process(full=True)

    # -- window processing ------------------------------------------------
    def _process(self, full: bool):
        while True:
            with self.lock:
                n = len(self.buf)
                new_bytes = n - self._last_pos
                chunk_bytes = int(self.chunk * self.sr * 2)
                if not full:
                    if new_bytes < chunk_bytes:
                        return
                else:
                    if new_bytes < self.sr * 2 * 1:  # < 1s of new audio
                        return
                win_bytes = int((self.chunk + self.overlap) * self.sr * 2)
                end = n
                start = max(0, end - win_bytes)
                window = bytes(self.buf[start:end])
                self._last_pos = end
            try:
                text = self.whisper.transcribe_pcm(window)
            except Exception as e:
                print(f"[asr] transcribe failed: {e}", flush=True)
                text = ""

            delta = self._delta(self._prev_text, text)
            self._prev_text = text
            if delta:
                self._window_idx += 1
                self.on_text(("or", self._window_idx), delta)

    @staticmethod
    def _delta(prev: str, curr: str) -> str:
        if not prev:
            return curr
        if not curr:
            return ""
        if curr.startswith(prev):
            return curr[len(prev):].strip()
        n = 0
        for a, b in zip(prev, curr):
            if a == b:
                n += 1
            else:
                break
        return curr[n:].strip() if n > 0 else curr
