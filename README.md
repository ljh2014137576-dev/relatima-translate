# relatima-translate · 实时 AI 配音

在 RTX 5080（16GB，Windows）上实现"边播边处理"的实时配音：
浏览器里播放 YouTube / B站 视频 → 自动把原声翻译成中文 → 用**克隆原声的音色**说出中文，延迟数秒。

```
浏览器(YouTube/B站) ─tabCapture─▶ WhisperLiveKit :8000 (ASR 原文)
        ─WS 转发─▶ glue :5100 ──DeepSeek 翻译中文──▶ IndexTTS2 :50001 (克隆音色说中文)
        ─WAV─▶ sounddevice 播放
```

## 组件

| 组件 | 职责 | 独立环境 |
|---|---|---|
| **WhisperLiveKit** | 流式 ASR（whisper small）→ 原文 | `venv_wlk` |
| **DeepSeek API** | 原文 → 中文（替代低质量 NLLB） | 云端 |
| **IndexTTS2** | 克隆参考音色说中文（fp16） | `index-tts/` + uv venv |
| **glue** | 订阅字幕 → LLM 翻译 → TTS → 扬声器 | `venv_glue` |
| **Chrome 扩展** | tabCapture 抓标签页音频 → WLK | 浏览器 |

## 快速开始

完整安装与启动步骤见 **[dubbing/README.md](dubbing/README.md)**，排障见 **[dubbing/FAQ.md](dubbing/FAQ.md)**。

```powershell
# 1) 设置 DeepSeek key（必填，用于翻译）
setx DEEPSEEK_API_KEY "sk-xxx"

# 2) 一键启动三个服务
powershell -ExecutionPolicy Bypass -File dubbing\start_services.ps1

# 3) Chrome 加载 chrome-extension/（解压扩展），视频页静音后 Start Capture
```

### 本地文件全链路测试（不依赖浏览器）

```powershell
cd glue
..\venv_glue\Scripts\python.exe test_local.py path\to\video.mp4 --delay 5
```

## 实测结果（RTX 5080，GPU 空闲）

- TTS 生成速度 **RTF 1.7~2.6**（5~7s 音频仅需 11~13s）
- DeepSeek 翻译 4 句全对（NLLB 只对 1 句），单批 ~1.2s
- 全链路每句都配音、无重复

## 参考音频

每个视频准备一段 **3~10 秒干净原声**（无 BGM/无他人说话）放 `glue/refs/`，
在 `glue/config.yaml` 指定 `tts.ref_audio`。

## 许可证

组件各属其上游（WhisperLiveKit Apache-2.0，IndexTTS2 见其仓库）。
本项目代码结构参考《模式 A·实时配音落地计划》。
