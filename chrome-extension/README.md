# WhisperLiveKit Chrome Extension (dubbing fork)

Fork of [QuentinFuxa/WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
`chrome-extension` v0.1.1, adapted for the real-time dubbing pipeline:

- Captures the **active tab's audio** via `chrome.tabCapture`.
- Sends PCM to the WhisperLiveKit server at `ws://localhost:8000/asr?target_language=zh`.
- **Forwards every WLK message to the glue relay** at `ws://127.0.0.1:5100/relay`
  so the local dubbing pipeline (`glue/main.py`) can synthesize + play the
  Chinese dubbing. The popup UI still shows live captions.

## Loading (Chrome)

1. Chrome → `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `chrome-extension` directory.
3. Open a YouTube / Bilibili video, **mute the video tab** (not the whole system),
   click the extension icon → **Start Capture**.

> Note: only tab audio is captured; the microphone is not used.
> Original audio is also routed to the speakers by the extension, so mute the
> video to avoid hearing both languages.
