# 模式 A · 实时配音（RTX 5080 落地版）

在 16GB 显存的 RTX 5080 上实现"边播边处理"的实时配音：

```
浏览器(YouTube/B站) --tabCapture--> WhisperLiveKit :8000(ASR+翻译中文)
        --WebSocket 转发--> glue :5100(消费 final 句)
        --POST /tts--> IndexTTS2 :50001(克隆原声说中文)
        --WAV--> sounddevice 播放
```

## 目录结构

```
<repo>\
├─ venv_wlk\                # WhisperLiveKit 环境（Python 3.12）[gitignored]
├─ venv_glue\               # glue 环境 [gitignored]
├─ index-tts\               # IndexTTS2 仓库（uv 管理，checkpoints/ 模型 5.5GB）[gitignored]
├─ indextts_service\
│   └─ server.py            # IndexTTS2 HTTP 配音服务（FastAPI，端口 50001）
├─ glue\
│   ├─ config.yaml          # 端口/参考音频/LLM/队列参数
│   ├─ main.py              # 中继 WS :5100 + LLM 翻译 + TTS 队列 + 播放（浏览器模式）
│   ├─ tts_client.py        # POST :50001/tts
│   ├─ audio_player.py      # sounddevice 顺序播放
│   ├─ llm_translator.py    # DeepSeek 翻译
│   ├─ test_local.py        # 本地视频文件全链路测试
│   └─ refs\                # 参考音频（每个视频一份，3~10s 干净原声）
├─ dubbing\
│   ├─ start_services.ps1   # 一键启动三个服务
│   ├─ stop_services.ps1
│   ├─ README.md
│   └─ FAQ.md
└─ chrome-extension\        # Chrome 扩展（tabCapture + 转发到 glue 中继）
```

## 启动顺序

**1. 一键启动（推荐）**

```powershell
powershell -ExecutionPolicy Bypass -File dubbing\start_services.ps1
```

会自动拉起并等待三个服务就绪：
1. WhisperLiveKit `ws://127.0.0.1:8000/asr`（small 模型 + 翻译，`--pcm-input`）
2. IndexTTS2 配音服务 `http://127.0.0.1:50001`（模型加载约 30s~2min）
3. glue 中继 `ws://127.0.0.1:5100/relay`

**2. 浏览器演示（YouTube / B站）**
1. Chrome 打开 `chrome://extensions` → 开发者模式 → 加载已解压的扩展 `chrome-extension/`
2. 打开视频页，**把视频标签页静音**（勿全局静音，避免把中文配音也静掉）
3. 点扩展图标 → Start Capture → 等待 3~8 秒即可听到中文配音

**3. 本地视频验证（不依赖浏览器）**
播放器（PotPlayer/VLC）打开外语视频并静音，然后：

```powershell
cd H:\ttstranslate\glue
..\venv_glue\Scripts\python.exe test_local.py "视频路径.mp4" --delay 5
```

`--delay 5` 为倒计时（对齐播放器起点）；`--speed 0` 可跳过喂入等待；
`--dry-run` 只打印将配音的文本，不做 TTS（快速调试用）。

## 配置说明（glue/config.yaml）

| 键 | 默认 | 说明 |
|---|---|---|
| `wlk.url` | ws://127.0.0.1:8000/asr?target_language=zh | WLK 字幕端点 |
| `wlk.debounce_seconds` | 2.0 | 句尾稳定多久才翻译/配音（越大越完整但延迟越高） |
| `wlk.force_len` | 60/200 | 无标点的长句强制切分长度 |
| `llm.enabled` | true | true=DeepSeek 翻译 ASR 原文；false=回退 WLK 内置 NLLB |
| `llm.api_key` | ""（用环境变量） | DeepSeek API key；留空则读 `DEEPSEEK_API_KEY` 环境变量 |
| `llm.model` | deepseek-chat | DeepSeek 模型 |
| `llm.max_batch` / `llm.batch_window` | 4 / 0.4 | 每批翻译句子数 / 批量收集窗口秒数 |
| `llm.glossary` | [] | 自定义词表，如 `[{"from":"RTX","to":"RTX"}]` |
| `tts.ref_audio` | refs/default.wav | 说话人参考音频（相对 glue/ 目录自动解析为绝对路径） |
| `tts.max_text_len` | 80 | 单次合成最大字数，超出按标点切分 |
| `playback.queue_seconds` | 2.0 | 延迟旋钮：每句相对其就绪时刻滞后 X 秒播放 |
| `playback.sentence_gap` | 0.4 | 句间静音，避免连读 |

**参考音频**：每个视频准备一段 3~10 秒干净原声（无 BGM/无他人说话），
放 `glue/refs/<video_id>.wav`，改 `config.yaml` 的 `tts.ref_audio` 指向它。

## 手动启动（调试用）

```powershell
# 设好路径变量（按你的实际目录）
$Root = "D:\path\to\repo"

# WLK（HF 走镜像；模型已缓存后可加 $env:HF_HUB_OFFLINE="1"）
$env:HF_ENDPOINT="https://hf-mirror.com"
& "$Root\venv_wlk\Scripts\wlk.exe" --model small --language auto --target-language zh --nllb-size 600M --host 127.0.0.1 --port 8000 --pcm-input --warmup-file "$Root\dubbing\samples\jfk.wav" --cors-origins *

# IndexTTS2 服务（uv 管理，必须在其仓库目录内跑）
$env:Path="$env:USERPROFILE\.local\bin;$env:Path"
$env:PYTHONPATH="$Root\index-tts"
cd "$Root\index-tts"
uv run "$Root\indextts_service\server.py" --host 127.0.0.1 --port 50001

# glue（浏览器模式，DeepSeek 翻译需要 DEEPSEEK_API_KEY 或 config 的 llm.api_key）
cd "$Root\glue"
..\venv_glue\Scripts\python.exe main.py
```

## 已实测数据（GPU 空闲）

| 项 | 结果 |
|---|---|
| TTS 生成速度 | RTF 1.7~2.6（5~7s 音频仅需 11~13s；瓶颈为 GPT 自回归） |
| DeepSeek 翻译 | 4 句英文 → 4 句中文全部正确，单批 ~1.2s |
| 本地全链路 | 14.7s 多句音频：ASR→DeepSeek→TTS→扬声器，**每句都配音、无重复**，~30s 完成 |
| 对比 NLLB | 同样内容 NLLB 只译出 1/4 句 |
