"""Batch (preload) dubbing: process a WHOLE video file/URL into a dubbed
Chinese audio track + SRT subtitles, optionally muxed into the video.

Unlike the real-time streaming mode (extension), this mode processes the
entire video up front so playback is instantly synced:

  input video/URL
      -> 16k mono wav (ffmpeg / yt-dlp)
      -> WhisperLiveKit :8000  (collect sentence-level source text + timing)
      -> DeepSeek               (translate each sentence to Chinese)
      -> IndexTTS2 :50001       (clone reference voice, say each sentence)
      -> timeline placement     (dubbed audio at each sentence's start time)
      -> out/<name>_dub.wav + _zh.srt (+ _dubbed.mp4 if video input)

Usage:
  python batch_dub.py "video.mp4"                  # local file
  python batch_dub.py "https://youtu.be/xxx"       # online video (yt-dlp)
  python batch_dub.py input.mp4 --out outdir --gap 0.35 --max-batch 5
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import wave
from dataclasses import dataclass, field

import numpy as np
import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_translator import DeepSeekTranslator
from main import load_config, split_long
from tts_client import TTSClient

SR = 16000
WS_URL = "ws://127.0.0.1:8000/asr?target_language=zh"


@dataclass
class Segment:
    start: float
    end: float
    text: str = ""
    zh: str = ""
    wav_path: str = ""
    duration: float = 0.0


# ---------------------------------------------------------------- audio source
def resolve_source(src, workdir):
    """Return a local 16k mono wav path for src (file or URL)."""
    out_wav = os.path.join(workdir, "audio_16k.wav")
    if src.startswith(("http://", "https://")):
        print(f"[batch] downloading audio via yt-dlp: {src}", flush=True)
        import yt_dlp
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(workdir, "dl_audio.%(ext)s"),
            "quiet": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(src, download=True)
            src = ydl.prepare_filename(info)
    # extract 16k mono wav
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-ar", "16000", "-ac", "1", out_wav]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.decode('utf-8', 'replace')}")
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", out_wav],
        capture_output=True).stdout.strip())
    print(f"[batch] audio: {out_wav}  ({dur:.1f}s)", flush=True)
    return out_wav, dur


def load_pcm(wav_path):
    with wave.open(wav_path, "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


# ---------------------------------------------------------------- transcription
_SENT_RE = __import__("re").compile(r"[^.!?。！？]+[.!?。！？]")


def _split_sentences(text):
    sents = [m.group(0).strip() for m in _SENT_RE.finditer(text)]
    return [s for s in sents if len(s) >= 2]


async def transcribe(pcm, url=WS_URL):
    """Feed PCM to WLK at max speed, return sentence-level source segments."""
    lines, seen = [], set()

    async with websockets.connect(url, open_timeout=60) as ws:
        cfg = json.loads(await ws.recv())
        if not cfg.get("useAudioWorklet", False):
            raise RuntimeError("WLK server must run with --pcm-input")

        chunk = int(0.5 * SR * 2)

        async def sender():
            for off in range(0, len(pcm), chunk):
                await ws.send(pcm[off:off + chunk])
                await asyncio.sleep(0)
            await ws.send(b"")

        async def receiver():
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "ready_to_stop":
                    return
                for line in msg.get("lines") or []:
                    if line.get("speaker") == -2:
                        continue
                    text = (line.get("text") or "").strip()
                    if len(text) < 2:
                        continue
                    key = (line.get("start"), line.get("end"))
                    if key in seen:
                        continue
                    seen.add(key)
                    s, e = _ts(line["start"]), _ts(line["end"])
                    lines.append(Segment(start=s, end=e, text=text))

        await asyncio.gather(sender(), receiver())

    # WLK evolves one committed line (start fixed, end grows, text grows).
    # Dedupe by start keeping the LONGEST text, then split into sentences
    # with timing spread proportionally across the line span.
    by_start = {}
    for seg in lines:
        k = round(seg.start, 2)
        if k not in by_start or len(seg.text) > len(by_start[k].text):
            by_start[k] = seg

    out = []
    for line in sorted(by_start.values(), key=lambda x: x.start):
        sents = _split_sentences(line.text)
        if not sents:
            sents = [line.text]
        total = sum(len(s) for s in sents) or 1
        span = max(0.0, line.end - line.start)
        t = line.start
        for s in sents:
            dur = span * len(s) / total
            out.append(Segment(start=t, end=t + dur, text=s))
            t += dur
    return out


def _ts(s):
    """'H:MM:SS.mmm' -> seconds."""
    parts = s.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return float(parts[-1])


# ---------------------------------------------------------------- translation
def translate_segments(cfg, segments, max_batch=5):
    tr = DeepSeekTranslator(cfg)
    for i in range(0, len(segments), max_batch):
        batch = segments[i:i + max_batch]
        out = tr.translate_batch([s.text for s in batch])
        if len(out) < len(batch):
            # mapping mismatch -> translate each individually
            out = [tr.translate_batch([s.text])[0] for s in batch]
        for s, zh in zip(batch, out):
            s.zh = zh.strip()
        print(f"[batch] translated {i + len(batch)}/{len(segments)}", flush=True)
        time.sleep(0.2)


# ---------------------------------------------------------------- TTS + mix
def synthesize_all(cfg, segments, workdir, gap):
    tts = TTSClient(cfg)
    # Timeline placement: place each dubbed sentence at its source start time,
    # shifting right if it would overlap the previously placed audio.
    sr_out = 22050
    cursor = 0.0
    placed = []  # (start_sec, end_sec)
    total = len(segments)
    for i, seg in enumerate(segments):
        text = seg.zh or seg.text
        chunks = split_long(text, cfg["tts"].get("max_text_len", 80))
        seg_wavs = []
        for ci, c in enumerate(chunks):
            p = os.path.join(workdir, f"seg_{i:04d}_{ci}.wav")
            data = tts.synthesize(c)
            with open(p, "wb") as f:
                f.write(data)
            seg_wavs.append(p)
        # concat chunks
        if len(seg_wavs) == 1:
            seg_path = seg_wavs[0]
        else:
            seg_path = os.path.join(workdir, f"seg_{i:04d}.wav")
            _concat_wavs(seg_wavs, seg_path, sr_out)
        dur = _wav_duration(seg_path, sr_out)
        seg.wav_path = seg_path
        seg.duration = dur

        # place at max(source start, cursor) to avoid overlap
        start = max(seg.start, cursor)
        placed.append((start, start + dur))
        cursor = start + dur + gap
        print(f"[batch] TTS {i + 1}/{total}  @{start:.1f}s  {text[:30]}", flush=True)

    # build the dubbed track
    total_len = max((e for _, e in placed), default=0.0) + 1.0
    track = np.zeros(int(total_len * sr_out), dtype=np.float32)
    for (s, e), seg in zip(placed, segments):
        if not seg.wav_path:
            continue
        data = _load_wav_f32(seg.wav_path, sr_out)
        i0 = int(s * sr_out)
        i1 = min(i0 + len(data), len(track))
        track[i0:i1] += data[: i1 - i0]
    return track, sr_out, placed


def _concat_wavs(paths, out, sr):
    parts = [_load_wav_f32(p, sr) for p in paths]
    full = np.concatenate([p for p in parts if len(p)])
    _save_wav(out, full, sr)


def _wav_duration(path, sr):
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def _load_wav_f32(path, sr):
    tmp = path
    with wave.open(tmp, "rb") as w:
        if w.getframerate() != sr:
            tmp = path + f".{sr}.wav"
    if tmp != path:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ar", str(sr), "-ac", "1", tmp], check=True)
    with wave.open(tmp, "rb") as w:
        frames = w.readframes(w.getnframes())
    a = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return a


def _save_wav(path, arr, sr):
    pcm = np.clip(arr, -1, 1)
    data = (pcm * 32767).astype(np.int16).tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)


# ---------------------------------------------------------------- outputs
def write_srt(segments, path):
    def fmt(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(f"{i}\n{fmt(s.start)} --> {fmt(s.end)}\n{s.zh or s.text}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Batch/preload dubbing")
    ap.add_argument("src", help="video/audio file or http(s) URL")
    ap.add_argument("--out", default=None, help="output directory (default: out/)")
    ap.add_argument("--gap", type=float, default=0.35, help="seconds between sentences")
    ap.add_argument("--max-batch", type=int, default=5)
    ap.add_argument("--no-mux", action="store_true", help="skip muxing into the video")
    ap.add_argument("--dry-run", action="store_true", help="transcribe+translate only, no TTS")
    args = ap.parse_args()

    cfg = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"))
    workdir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(workdir, exist_ok=True)

    name = os.path.splitext(os.path.basename(args.src))[0][:40]
    wav_path, duration = resolve_source(args.src, workdir)

    print("[batch] transcribing ...", flush=True)
    pcm = load_pcm(wav_path)
    segments = asyncio.run(transcribe(pcm))
    print(f"[batch] {len(segments)} sentences", flush=True)
    for s in segments[:5]:
        print(f"   {s.start:7.2f}  {s.text[:50]}", flush=True)

    print("[batch] translating (DeepSeek) ...", flush=True)
    translate_segments(cfg, segments, args.max_batch)

    if args.dry_run:
        srt = os.path.join(workdir, f"{name}_zh.srt")
        write_srt(segments, srt)
        print(f"[batch] (dry-run) subtitles : {srt}", flush=True)
        for s in segments:
            print(f"   {s.start:7.2f}-{s.end:7.2f}  {s.zh or s.text[:40]}", flush=True)
        print("[batch] done (dry-run).", flush=True)
        return

    print("[batch] synthesizing (IndexTTS2) ...", flush=True)
    track, sr_out, placed = synthesize_all(cfg, segments, workdir, args.gap)

    dub_wav = os.path.join(workdir, f"{name}_dub.wav")
    _save_wav(dub_wav, track, sr_out)
    srt = os.path.join(workdir, f"{name}_zh.srt")
    write_srt(segments, srt)
    print(f"[batch] dubbed audio : {dub_wav}", flush=True)
    print(f"[batch] subtitles     : {srt}", flush=True)

    if not args.no_mux and os.path.splitext(args.src)[1].lower() in (".mp4", ".mkv", ".webm", ".mov"):
        out_video = os.path.join(workdir, f"{name}_dubbed.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", args.src, "-i", dub_wav,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-shortest", out_video,
        ], check=True)
        print(f"[batch] dubbed video : {out_video}", flush=True)

    print("[batch] done.", flush=True)


if __name__ == "__main__":
    main()
