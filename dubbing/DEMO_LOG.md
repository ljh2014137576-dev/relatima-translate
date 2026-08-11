# 演示记录

## 测试环境
- RTX 5080 16GB / 驱动 610.62 / Python 3.12.10
- 服务：WLK small + NLLB-600M (:8000，HF_HUB_OFFLINE 离线模式)，IndexTTS2 fp16 (:50001)，glue relay (:5100)

## GPU 空闲后的性能复测（ComfyUI 退出后）
- TTS 生成速度：**RTF 1.7 ~ 2.6**（GPU 争用时 RTF 56~144）：
  - 5.0 秒音频 → 12.9s（RTF 2.6）
  - 6.5 秒音频 → 11.3s（RTF 1.7）
  - 6.8 秒音频 → 13.6s（RTF 2.0）
- 耗时分布：GPT 自回归 ~92%（瓶颈）、s2mel ~0.8s、bigvgan ~0.1s。
- 全链路（14.7s 音频 1.0x 喂入 + ASR + 翻译 + TTS + 播放）约 23 秒完成，无 OOM。
- NLLB 1.3B 实测：翻译提升不明显，且显存占用过大（VRAM 15.1/16.3GB，RTF 升到 16.8），已回退 600M。

## 测试 1：本地 wav → 中文流式输出（M1）
- 输入：`dubbing/samples/jfk.wav`（11 秒英文演讲）
- 结果：WS 消息含 `lines[]`（`text`/`start`/`end`/`detected_language`/`translation`）
  与 `buffer_translation`（partial）。中文输出如"所以我的 fellow Americans,没有. …"
- final / partial 两类消息均可见。

## 测试 2：POST /tts 克隆声线中文（M2）
- 输入：`{"text":"你好，世界。","ref_audio":"...jfk.wav"}`
- 结果：HTTP 200，返回 114KB WAV（22050Hz mono，2.59s），音色为参考音色的中文朗读。

## 测试 3：本地全链路（M3，最终版代码）
- 命令：`test_local.py jfk.wav --delay 0 --speed 0`
- 流程：jfk 音频 → WLK ASR+翻译 → glue（按句幂等去重 + 尾部去抖）→ IndexTTS2 → 扬声器
- 日志关键行（RTF 1.76）：
  ```
  [ASR+TRANS(flush)] 所以我的 fellow Americans,没有. 你的国家可以为 你做
  [TTS ok] 所以我的 fellow Americans,没有. 你的国家可以为 你做
  [local] pipeline idle.
  ```
- 结论：每句只配一次（不重不丢），中文配音自动播放。

## 测试 4：glue 中继 + 扩展（M4，模拟扩展）
- 模拟扩展连接 `ws://127.0.0.1:5100/relay` 发送含 `translation` 的快照消息
- 结果：glue 正确消费并配音（`[ASR+TRANS] 你好世界。`）。
- 真实 Chrome（YouTube/B站）测试见 README「浏览器演示」步骤。

## 测试 5：DeepSeek LLM 翻译接入（M5，替换 NLLB）
- 配置：`llm.enabled: true`，模型 `deepseek-chat`，API key 在 `glue/config.yaml`。
- 独立测试：4 句英文批翻译，1.2s 返回全部正确中文。
- 全链路（multi3.wav，14.7s，4 句）：
  ```
  [ASR->LLM] Good morning everyone.             -> [LLM->ZH] 大家早上好。
  [ASR->LLM] Today we are going to talk about... -> [LLM->ZH] 今天我们来讲讲人工智能。
  [ASR->LLM] It is changing the way we work...   -> [LLM->ZH] 它正在改变我们工作和生活的方式。
  [ASR->LLM(flush)] Thank you for listening      -> [LLM->ZH] 感谢您的聆听。
  ```
- **对比**：同样内容 NLLB 只译出 1/4 句；DeepSeek 4/4 全译且更自然。
- 每句均进入 TTS 并播放，无重复、无丢失。

## 已知局限
- **延迟**：RTF ~2 意味着每句 TTS 3~13s + ASR 提交 2~5s + LLM 1~2s，长句总延迟约 10s 上下；短句可进 2-8s。
- **NLLB-1.3B**：显存占用过大（15.1/16.3GB，RTF 16.8）已弃用；DeepSeek 翻译已替代 NLLB。
