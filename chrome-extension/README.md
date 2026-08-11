# Relatima Translate · Chrome 扩展（实时同传）

后台常驻抓取**当前标签页音频**，送到本机 WhisperLiveKit + DeepSeek + IndexTTS2，
实现"边看边听中文配音"（同声传译）。

## 架构（MV3 后台常驻）

```
点扩展图标 → background (service worker)
        └─ 创建 offscreen 文档（常驻）
              ├─ chromeMediaSource:tab 抓标签页音频
              ├─ PCM → WLK :8000 (ASR)
              └─ 转发翻译消息 → glue 中继 :5100 → TTS → 扬声器
```

弹窗/焦点变化不影响抓流；关掉弹窗后继续在后台运行。

## 使用

1. 先启动本机服务（`dubbing\start_services.ps1`），并设好 `DEEPSEEK_API_KEY`。
2. Chrome 打开 `chrome://extensions` → 开发者模式 → 加载已解压的 `chrome-extension/`。
3. 打开 YouTube / B站 视频，**右键标签页 → 静音网站**（tab 级静音，别在播放器里静音）。
4. **点扩展图标** → 图标出现绿色 **ON** → 几秒后听到中文配音。
5. 再点一次图标停止（ON 消失）。

> 说明：
> - 扩展不依赖麦克风（抓的是标签页音频）。
> - 若图标点了没反应，看 `chrome://extensions` 里扩展的"Service Worker"控制台，
>   或 `H:\ttstranslate\glue_out.log` 与 `H:\ttstranslate\wlk_stderr.log`。

## 调试

- Service Worker 控制台：`chrome://extensions` → 该扩展 → "Service Worker" 链接。
- offscreen 日志会打印在 SW 控制台里（`[offscreen]` 前缀）。
