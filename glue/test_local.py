"""Local full-chain test entry point.

Feeds a local video/audio file's PCM to the glue capture endpoint
(ws://127.0.0.1:5100/capture). glue runs cloud ASR (OpenRouter Whisper) ->
DeepSeek translation -> cloud TTS (MiniMax) -> speakers. The user should play
the same video muted and start it when the countdown reaches zero.

Usage:
    python test_local.py path/to/video.mp4 [--delay 5] [--speed 1.0] [--dry-run]
"""
import argparse
import asyncio
import subprocess

import websockets

from main import Pipeline, load_config

CAPTURE_URL = "ws://127.0.0.1:5100/capture"


def load_audio_pcm(path, sample_rate=16000):
    cmd = [
        "ffmpeg", "-i", str(path),
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-ac", "1",
        "-loglevel", "error", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode().strip())
    if not proc.stdout:
        raise RuntimeError(f"no audio in {path}")
    return proc.stdout


async def feed_capture(pcm, speed, url=CAPTURE_URL):
    duration = len(pcm) / (16000 * 2)
    chunk_bytes = int(0.5 * 16000 * 2)

    while True:  # reconnect on server restart
        try:
            async with websockets.connect(url, open_timeout=30) as ws:
                print(f"[local] connected to {url}", flush=True)
                print(f"[local] feeding {duration:.1f}s at {speed}x speed", flush=True)
                for off in range(0, len(pcm), chunk_bytes):
                    await ws.send(pcm[off:off + chunk_bytes])
                    if speed > 0:
                        await asyncio.sleep(0.5 / speed)
                print("[local] audio sent, closing to flush...", flush=True)
                return
        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            print(f"[local] connection lost ({e}); retrying in 3s...", flush=True)
            await asyncio.sleep(3)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--url", default=CAPTURE_URL)
    ap.add_argument("--delay", type=float, default=5.0, help="countdown before feeding (match your player start)")
    ap.add_argument("--speed", type=float, default=1.0, help="feed speed multiplier; 0 = as fast as possible")
    ap.add_argument("--dry-run", action="store_true", help="log translations without running TTS")
    args = ap.parse_args()

    cfg = load_config()
    pipeline = Pipeline(cfg, dry_run=args.dry_run)
    pipeline.start()

    pcm = load_audio_pcm(args.audio)
    if args.delay > 0:
        print(f"[local] starting in {args.delay:.0f}s - start the video player now!", flush=True)
        await asyncio.sleep(args.delay)

    try:
        await feed_capture(pcm, args.speed, args.url)
        print("[local] feed done. waiting for TTS/playback to drain...", flush=True)
        pipeline.wait_idle(timeout=3600.0 if not args.dry_run else 30.0)
        print("[local] pipeline idle.", flush=True)
    finally:
        pipeline.stop()


if __name__ == "__main__":
    asyncio.run(main())
