"""Glue layer: consume WLK ASR text -> (LLM/NLLB) Chinese -> IndexTTS2 -> speaker.

Two entry points:
  * main.py (browser mode): runs a relay WebSocket server on :5100; the
    Chrome extension forwards every WLK message here, and glue dubs them.
  * test_local.py (local mode): feeds a local video's audio to WLK over the
    /asr WebSocket and dubs whatever comes back (video played muted by user).

Pipeline rules (from the plan):
  - If LLM translation is enabled (config `llm.enabled`), the committed ASR
    source text is translated via DeepSeek (better quality than NLLB).
    Otherwise WLK's built-in `line.translation` (NLLB) is used.
  - Only finalized sentence-level content is dubbed; snapshots are idempotent.
  - TTS is strictly serial: IndexTTS2 synthesizes one utterance at a time.
  - Long text is split on punctuation before synthesis.
"""
import json
import queue
import re
import threading
import time
import collections

import websockets
import yaml

from audio_player import AudioPlayer
from tts_client import TTSClient

_SENT_END_RE = re.compile(r"[。！？!?；;]")
_LLM_STOP = object()


def split_long(text, max_len):
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts = _SENT_END_RE.split(text)
    out, cur = [], ""
    for p in parts:
        if not p:
            continue
        if len(cur) + len(p) + 1 <= max_len:
            cur = f"{cur}{p}。"
        else:
            if cur:
                out.append(cur)
            cur = f"{p}。"
    if cur:
        out.append(cur)
    return out


class LineTracker:
    """Turns WLK's growing `line.translation` into one dubbing per sentence.

    WLK full-mode snapshots resend the *whole* accumulated translation of a
    committed line every update. This tracker splits the translation into
    complete sentences and remembers which ones were already dubbed, so
    re-sent snapshots are idempotent. The trailing unfinished partial is
    emitted by a debounce fallback once it stabilizes.
    """

    _SENT_RE = re.compile(r"[^。！？!?；;]+[。！？!?；;]")
    _MAX_EMITTED = 20

    def __init__(self, debounce=2.0, force_len=60, min_len=2, min_complete=4):
        self.debounce = debounce
        self.force_len = force_len
        self.min_len = min_len
        self.min_complete = min_complete
        self._pending = {}  # key -> {"emitted": set, "tail": str, "ts": float}
        self._done = set()

    def _split_sentences(self, text):
        sentences = [m.group(0).strip() for m in self._SENT_RE.finditer(text)]
        tail = self._SENT_RE.sub("", text).strip()
        return sentences, tail

    def update(self, key, translation, now):
        ready = []
        if len(translation) < self.min_len or key in self._done:
            return ready
        entry = self._pending.setdefault(key, {"emitted": set(), "tail": "", "ts": now})

        sentences, tail = self._split_sentences(translation)
        for s in sentences:
            if s in entry["emitted"] or len(s) < self.min_complete:
                continue
            entry["emitted"].add(s)
            if len(entry["emitted"]) > self._MAX_EMITTED:
                entry["emitted"].pop()
            ready.append(s)

        # A revised whole-translation could shrink: drop emitted sentences no
        # longer present (rare NLLB re-translation of the same span).
        if tail:
            if tail != entry["tail"]:
                entry["tail"] = tail
                entry["ts"] = now
        else:
            entry["tail"] = ""
            entry["ts"] = now

        ready += self._maybe_flush_tail(key, now)
        return ready

    def _maybe_flush_tail(self, key, now):
        entry = self._pending.get(key)
        if entry is None or len(entry["tail"]) < self.min_complete:
            return []
        # Emit the tail once it is long enough and stable (or oversized).
        if len(entry["tail"]) >= self.force_len or now - entry["ts"] >= self.debounce:
            text = entry["tail"]
            self._finish(key)
            return [text]
        return []

    def _finish(self, key):
        self._done.add(key)
        self._pending.pop(key, None)

    def tick(self, now):
        ready = []
        for key in list(self._pending.keys()):
            ready += self._maybe_flush_tail(key, now)
        return ready

    def flush(self, now):
        ready = []
        for key in list(self._pending.keys()):
            tail = self._pending[key]["tail"].strip()
            if len(tail) >= self.min_len:
                ready.append(tail)
            self._done.add(key)
            del self._pending[key]
        return ready


class SentenceTracker:
    """Dedupes WLK's growing committed *source* text into new sentences for
    LLM translation.

    WLK full-mode snapshots resend the whole accumulated `line.text` every
    update. This tracker splits the source text into complete sentences,
    remembers which were already submitted for translation, and keeps the
    unfinished trailing tail until it completes or stabilizes.
    """

    _SENT_RE = re.compile(r"[^.!?。！？]+[.!?。！？]")

    def __init__(self, debounce=2.0, force_len=200, min_len=2):
        self.debounce = debounce
        self.force_len = force_len
        self.min_len = min_len
        self._pending = {}  # key -> {"submitted": set, "tail": str, "ts": float}
        self._done = set()

    @staticmethod
    def _split(text):
        sentences = [m.group(0).strip() for m in SentenceTracker._SENT_RE.finditer(text)]
        tail = SentenceTracker._SENT_RE.sub("", text).strip()
        return sentences, tail

    def update(self, key, text, now):
        ready = []
        if not text or key in self._done:
            return ready
        entry = self._pending.setdefault(key, {"submitted": set(), "tail": "", "ts": now})

        sentences, tail = self._split(text)
        for s in sentences:
            if len(s) < self.min_len or s in entry["submitted"]:
                continue
            entry["submitted"].add(s)
            ready.append(s)

        if tail != entry["tail"]:
            entry["tail"] = tail
            entry["ts"] = now
        ready += self._maybe_flush_tail(key, now)
        return ready

    def _maybe_flush_tail(self, key, now):
        entry = self._pending.get(key)
        if entry is None or len(entry["tail"]) < self.min_len:
            return []
        if len(entry["tail"]) >= self.force_len or now - entry["ts"] >= self.debounce:
            text = entry["tail"]
            self._finish(key)
            return [text]
        return []

    def _finish(self, key):
        self._done.add(key)
        self._pending.pop(key, None)

    def tick(self, now):
        ready = []
        for key in list(self._pending.keys()):
            ready += self._maybe_flush_tail(key, now)
        return ready

    def flush(self, now):
        ready = []
        for key in list(self._pending.keys()):
            tail = self._pending[key]["tail"]
            if len(tail) >= self.min_len:
                ready.append(tail)
            self._finish(key)
        return ready


class Pipeline:
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.text_queue = queue.Queue()
        self.tts = TTSClient(cfg)
        self.player = AudioPlayer(cfg)
        self.llm_enabled = bool(cfg.get("llm", {}).get("enabled", False))
        self._stop = False
        self._active = 0  # in-flight TTS synthesis count

        if self.llm_enabled:
            from llm_translator import DeepSeekTranslator
            self.llm = DeepSeekTranslator(cfg)
            self.llm_queue = queue.Queue()
            self.sent_tracker = SentenceTracker(
                debounce=cfg["wlk"].get("debounce_seconds", 2.0),
                force_len=cfg["wlk"].get("force_len", 200),
                min_len=cfg["wlk"].get("min_text_len", 2),
            )
            self.max_batch = cfg["llm"].get("max_batch", 6)
            self.batch_window = cfg["llm"].get("batch_window", 0.5)
            self.llm_history = collections.deque(
                maxlen=int(cfg["llm"].get("context_sentences", 8))
            )
            print(f"[glue] LLM translation enabled: {self.llm.model}", flush=True)
            # expose the search backend so the extension can toggle it live
            try:
                from control import register_search
                register_search(self.llm.search)
            except Exception as e:
                print(f"[glue] control not wired: {e}", flush=True)
        else:
            self.llm = None
            self.llm_queue = None
            self.tracker = LineTracker(
                debounce=cfg["wlk"].get("debounce_seconds", 2.0),
                force_len=cfg["wlk"].get("force_len", 60),
                min_len=cfg["wlk"].get("min_text_len", 2),
            )

    def start(self):
        self.player.start()
        threading.Thread(target=self._tts_loop, daemon=True).start()
        threading.Thread(target=self._tick_loop, daemon=True).start()
        if self.llm_enabled:
            threading.Thread(target=self._llm_loop, daemon=True).start()

    def reset_context(self):
        """Start a fresh context for a new video session: clear the per-video
        translation history (source+translated pairs) and the ASR sentence
        trackers so the next dubbing is context-independent."""
        if self.llm_enabled:
            self.llm_history.clear()
            self.sent_tracker = SentenceTracker(
                debounce=self.cfg["wlk"].get("debounce_seconds", 1.0),
                force_len=self.cfg["wlk"].get("force_len", 200),
                min_len=self.cfg["wlk"].get("min_text_len", 2),
            )
        else:
            self.tracker = LineTracker(
                debounce=self.cfg["wlk"].get("debounce_seconds", 1.0),
                force_len=self.cfg["wlk"].get("force_len", 60),
                min_len=self.cfg["wlk"].get("min_text_len", 2),
            )
        print("[glue] context reset (new video session)", flush=True)

    def _tick_loop(self):
        while not self._stop:
            try:
                if self.llm_enabled:
                    for s in self.sent_tracker.tick(time.time()):
                        self.llm_queue.put(s)
                else:
                    for text in self.tracker.tick(time.time()):
                        print(f"[ASR+TRANS] {text}", flush=True)
                        self.text_queue.put(text)
            except Exception as e:
                print(f"[tick] error: {e}", flush=True)
            time.sleep(0.5)

    def stop(self):
        self._stop = True
        self.player.stop()
        self.text_queue.put(None)
        if self.llm_queue is not None:
            self.llm_queue.put(_LLM_STOP)  # sentinel

    def wait_idle(self, timeout=600.0):
        """Block until all queued text has been translated/synthesized/played."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            idle = self.text_queue.empty() and self._active == 0 and self.player.q.empty()
            if self.llm_queue is not None:
                idle = idle and self.llm_queue.empty()
            if idle:
                return True
            time.sleep(1)
        return False

    # -- message handling ------------------------------------------------
    def process_message(self, msg):
        now = time.time()
        for line in msg.get("lines") or []:
            if line.get("speaker") == -2:
                continue  # silence placeholder
            # Key on speaker + start time only: WLK revises a line's `end`
            # as the ASR refines, so (speaker,start,end) would fragment one
            # sentence into many keys and cause repeated dubbing.
            key = (line.get("speaker"), line.get("start"))
            if self.llm_enabled:
                src = (line.get("text") or "").strip()
                if not src:
                    continue
                for s in self.sent_tracker.update(key, src, now):
                    print(f"[ASR->LLM] {s[:60]}", flush=True)
                    self.llm_queue.put(s)
            else:
                trans = (line.get("translation") or "").strip()
                if not trans:
                    continue
                for text in self.tracker.update(key, trans, now):
                    print(f"[ASR+TRANS] {text}", flush=True)
                    self.text_queue.put(text)

    def flush_pending(self):
        now = time.time()
        if self.llm_enabled:
            for s in self.sent_tracker.flush(now):
                print(f"[ASR->LLM(flush)] {s[:60]}", flush=True)
                self.llm_queue.put(s)
        else:
            for text in self.tracker.flush(now):
                print(f"[ASR+TRANS(flush)] {text}", flush=True)
                self.text_queue.put(text)

    # -- cloud ASR entry (from CaptureBuffer) -----------------------------
    def feed_asr_text(self, key, text):
        now = time.time()
        if not text:
            return
        if self.llm_enabled:
            for s in self.sent_tracker.update(key, text, now):
                print(f"[ASR->LLM] {s[:60]}", flush=True)
                self.llm_queue.put(s)
        else:
            for s in self.tracker.update(key, text, now):
                print(f"[ASR+TRANS] {s[:60]}", flush=True)
                self.text_queue.put(s)

    # -- LLM translation worker ------------------------------------------
    def _llm_loop(self):
        batch = []
        batch_started = None
        while not self._stop:
            timeout = None
            if batch and batch_started is not None:
                timeout = max(0.05, self.batch_window - (time.time() - batch_started))
            try:
                item = self.llm_queue.get(timeout=timeout if timeout is not None else 1.0)
            except queue.Empty:
                item = None
            if item is None:
                if batch:
                    self._translate_batch(batch)
                    batch, batch_started = [], None
                continue
            if item is _LLM_STOP:
                break
            batch.append(item)
            if batch_started is None:
                batch_started = time.time()
            if len(batch) >= self.max_batch:
                self._translate_batch(batch)
                batch, batch_started = [], None
        if batch:
            self._translate_batch(batch)

    def _translate_batch(self, sentences):
        try:
            # pass the previously translated sentences as conversation context
            # and let DeepSeek merge fragments into complete sentences.
            context = list(self.llm_history) if self.llm_history else None
            results = self.llm.translate_batch(sentences, context=context, merge=True)
            # update history for the next batch (keeps pronoun/term coherence)
            src_text = " ".join(sentences)
            for zh in results:
                zh = zh.strip()
                if not zh:
                    continue
                print(f"[LLM->ZH] {zh[:50]}", flush=True)
                self.text_queue.put(zh)
                self.llm_history.append((src_text, zh))
        except Exception as e:
            print(f"[LLM FAIL] {sentences[0][:30]}... -> {e}", flush=True)

    # -- TTS worker ------------------------------------------------------
    def _tts_loop(self):
        while not self._stop:
            text = self.text_queue.get()
            if text is None:
                break
            for chunk in split_long(text, self.cfg["tts"]["max_text_len"]):
                if self.dry_run:
                    print(f"[DRY TTS] {chunk[:50]}", flush=True)
                    time.sleep(0.1)
                    continue
                self._active += 1
                try:
                    wav = self.tts.synthesize(chunk)
                    self.player.enqueue(wav)
                    print(f"[TTS ok] {chunk[:40]}", flush=True)
                except Exception as e:
                    print(f"[TTS FAIL] {chunk[:30]}... -> {e}", flush=True)
                finally:
                    self._active -= 1


# -- browser mode: capture + relay servers --------------------------------
async def capture_server(pipeline, cfg):
    """WS endpoint that receives raw PCM (16k s16le mono) and runs cloud ASR.

    Each connection is treated as ONE video session: it gets a fresh ASR
    buffer and resets the per-video translation context.
    """
    from capture import CaptureBuffer
    from whisper_client import OpenRouterWhisper

    host = cfg["relay"]["host"]
    port = cfg["relay"]["port"]
    whisper = OpenRouterWhisper(cfg)

    async def handler(ws):
        pipeline.reset_context()          # new video = fresh context
        buffer = CaptureBuffer(cfg, whisper, on_text=pipeline.feed_asr_text)
        buffer.start()
        print(f"[capture] video session started ({ws.remote_address})", flush=True)
        try:
            async for msg in ws:
                if isinstance(msg, bytes) and msg:
                    buffer.feed(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            buffer.flush_now()
            buffer.stop()
            print("[capture] video session ended", flush=True)

    async with websockets.serve(handler, host, port, subprotocols=[]):
        print(f"[glue] capture listening on ws://{host}:{port}/capture", flush=True)
        await asyncio_future_forever()


async def relay_server(pipeline, cfg):
    host = cfg["relay"]["host"]
    port = cfg["relay"]["port"]

    async def handler(ws):
        print(f"[relay] extension connected ({ws.remote_address})", flush=True)
        try:
            async for raw in ws:
                try:
                    pipeline.process_message(json.loads(raw))
                except Exception as e:
                    print(f"[relay] bad message: {e}", flush=True)
        except websockets.exceptions.ConnectionClosed:
            pass
        print("[relay] extension disconnected", flush=True)

    async with websockets.serve(handler, host, port):
        print(f"[glue] relay listening on ws://{host}:{port}", flush=True)
        await asyncio_future_forever()


async def asyncio_future_forever():
    import asyncio
    await asyncio.Future()


async def run_browser(cfg):
    pipeline = Pipeline(cfg)
    pipeline.start()
    try:
        from control import start_control
        start_control()
        # cloud ASR (OpenRouter) is the default; /relay kept for legacy WLK mode
        await capture_server(pipeline, cfg)
    except KeyboardInterrupt:
        pipeline.stop()


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    import asyncio
    cfg = load_config()
    try:
        asyncio.run(run_browser(cfg))
    except KeyboardInterrupt:
        pass
