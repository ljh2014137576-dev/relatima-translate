"""Probe script: connect to WLK WS, feed an audio file, print ALL message types/fields."""
import argparse
import asyncio
import json
import subprocess
import sys

import websockets

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2


def load_audio_pcm(audio_path):
    cmd = [
        "ffmpeg", "-i", str(audio_path),
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-loglevel", "error",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode().strip())
    return proc.stdout


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="audio file to feed")
    ap.add_argument("--url", default="ws://127.0.0.1:8000/asr?target_language=zh")
    ap.add_argument("--speed", type=float, default=0.0)
    args = ap.parse_args()

    pcm = load_audio_pcm(args.audio)
    duration = len(pcm) / (SAMPLE_RATE * BYTES_PER_SAMPLE)
    print(f"[probe] {args.audio}: {duration:.1f}s", file=sys.stderr)

    seen_types = set()
    async with websockets.connect(args.url, open_timeout=60) as ws:
        cfg = json.loads(await ws.recv())
        print("[cfg]", json.dumps(cfg), file=sys.stderr)
        is_pcm = cfg.get("useAudioWorklet", False)

        async def sender():
            if is_pcm:
                chunk = int(0.5 * SAMPLE_RATE * BYTES_PER_SAMPLE)
                for off in range(0, len(pcm), chunk):
                    await ws.send(pcm[off:off + chunk])
                    if args.speed > 0:
                        await asyncio.sleep(0.5 / args.speed)
            else:
                for off in range(0, len(pcm), 32000):
                    await ws.send(pcm[off:off + 32000])
                    if args.speed > 0:
                        await asyncio.sleep(0.5 / args.speed)
            await ws.send(b"")
            print("[sent end]", file=sys.stderr)

        async def receiver():
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type", "?")
                seen_types.add(t)
                # Print every field for a few messages
                if t in ("snapshot", "diff") or "lines" in msg or "translation" in raw:
                    lines = msg.get("lines") or msg.get("new_lines") or []
                    trans = msg.get("buffer_translation", "")
                    for ln in lines:
                        print(f"[line] {json.dumps(ln, ensure_ascii=False)}")
                    if trans:
                        print(f"[trans-buffer] {trans}")
                elif t == "ready_to_stop":
                    print("[ready_to_stop]", file=sys.stderr)
                    return
                else:
                    print(f"[{t}] {json.dumps(msg, ensure_ascii=False)[:300]}")

        s = asyncio.create_task(sender())
        r = asyncio.create_task(receiver())
        await asyncio.wait_for(asyncio.gather(s, r), timeout=120)
        print(f"\n[types seen] {sorted(seen_types)}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
