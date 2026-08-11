# FAQ（实际遇到的坑与解法）

## 1. HuggingFace 连不上 / 下载超时
HF 直连超时（国内网络）。统一用镜像：
```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
```
WLK 模型、NLLB 模型、IndexTTS2 辅助模型（bigvgan/w2v-bert/campplus）都会自动走镜像。
IndexTTS2 主模型（5.5GB）用 ModelScope 最快：
```bash
uv tool install "modelscope"
modelscope download --model IndexTeam/IndexTTS-2 --local_dir checkpoints
```
实测 ~44MB/s，1.5 分钟下完。系统代理在 `127.0.0.1:7890`（curl 加 `-x` 可用）。

## 1b. 模型已缓存但服务启动/首次会话还去联网校验（WinError 10060）
所有模型下载完成后，huggingface_hub 仍会对缓存做 HEAD 校验；hf-mirror 不稳定时这会导致
**WS 连接被拒（HTTP 500）/ health 无响应**。对策：启动时加 `HF_HUB_OFFLINE=1`
（`start_services.ps1` 已内置），服务完全离线跑缓存。
NLLB-1.3B 若下载不下来，可走系统代理：
```powershell
$env:HTTPS_PROXY="http://127.0.0.1:7890"; $env:HTTP_PROXY="http://127.0.0.1:7890"
```

## 2. ComfyUI 占满 GPU → TTS 极慢
ComfyUI 后台任务长期 ~97% 占 GPU 时，IndexTTS2 RTF 高达 129~144（生成 2.6s 语音花 334s）。
**对策**：配音演示前暂停/退出 ComfyUI 或等其空闲。GPU 空闲时实测 **RTF 1.7~2.6**（生成 5~7s 音频仅需 11~13s）。
另外 **NLLB-1.3B 别用**：显存占用过大（VRAM 15.1/16.3GB）导致 RTF 回升到 16.8 且有 OOM 风险，600M 更稳。

## 3. WLK 服务无响应 / 被 kill
`Start-Process` 启动 wlk.exe 若不带隐藏窗口，控制台窗口关闭事件会杀掉进程
（`forrtl: error (200)`）。**对策**：始终 `-WindowStyle Hidden` 启动；
用 `dubbing/start_services.ps1`。首次 WS 连接若超时，是模型首次加载阻塞了事件循环——
用 `--warmup-file <本地wav>` 在启动时预加载即可解决。

## 4. 翻译不出 `line.translation`
- `--translate-on-complete` 会把翻译放到 `buffer_translation`，`line.translation` 不填充。
  glue 依赖 `line.translation`（更稳定、可去重），所以**不要**加该 flag。
- 服务端必须带 `--target-language zh`，否则没有翻译队列。

## 5. 同一句反复配音（重复字幕）
WLK full 模式每次快照会重发已提交行；且 ASR 修订时 `end` 时间戳会变。
glue 已处理：LineTracker 以 `(speaker, start)` 为键 + 稳定性去抖(2s) + 已发音集合，
保证每句只配一次。若仍偏多，调大 `debounce_seconds`。

## 6. POST /tts 返回 400 "ref_audio not found"
glue 与服务端工作目录不同（服务端在 `index-tts` 下），相对路径会找不到。
**对策**：`config.yaml` 的 `ref_audio` 必须用**绝对路径**。

## 7. Windows 上 IndexTTS2 安装
- 用 uv（官方要求），不要 conda/pip。
- 别用 `--all-extras`（deepspeed 装不上）；用 `uv sync --extra webui --default-index "https://mirrors.aliyun.com/pypi/simple"`。
- 不用 `--accel/--torch_compile`（flash-attn/triton 在 Windows 难装），仅 `--fp16`。

## 8. 中文显示乱码
PowerShell 默认 GBK，Python 打印 UTF-8 中文会乱码（不影响功能）。
临时修复：`$env:PYTHONIOENCODING='utf-8'`；长期可用 `chcp 65001`。

## 9. NLLB 翻译漏句/质量差
已用 **DeepSeek LLM 翻译**替代（`llm.enabled: true`，key 在 `glue/config.yaml`）。
实测 4 句英文 NLLB 只译 1 句、DeepSeek 4 句全对。想关掉 LLM 翻译回退 NLLB：`llm.enabled: false`。
自定义词表（专名/梗）配 `llm.glossary`，会注入翻译 prompt。
注意：LLM 依赖外网（DeepSeek API）；网络不可用时句子会被跳过（日志 `[LLM FAIL]`）。
