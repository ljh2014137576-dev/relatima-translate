// Relatima Translate · offscreen document
// 常驻运行：抓取标签页音频 → PCM → glue /capture (:5100)。
// glue 负责云端 ASR(OpenRouter) → 翻译(DeepSeek) → 配音(MiniMax) → 播放。
// 与 popup 无关，弹窗关闭/点击视频都不会中断。

const CAPTURE_URL = "ws://127.0.0.1:5100/capture";

let ready = false;
let stream = null;
let audioContext = null;
let source = null;
let workletNode = null;
let recorderWorker = null;
let captureWs = null;
let captureReconnectTimer = null;
let running = false;
const pending = [];

// ---- 消息处理（含就绪前的排队）----
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "PING") { sendResponse({ ready }); return; }
  if (msg.type === "SET_STREAM" || msg.type === "STOP") {
    if (ready) handle(msg);
    else pending.push(msg);
    sendResponse({ ok: true });
    return;
  }
});

function drainPending() {
  while (pending.length) handle(pending.shift());
}

function handle(msg) {
  if (msg.type === "SET_STREAM") start(msg.streamId);
  else if (msg.type === "STOP") stop();
}

// ---- 抓流 + 音频管线 ----
async function start(streamId) {
  if (running) return;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    });
    audioContext = new AudioContext();
    await audioContext.audioWorklet.addModule("web/pcm_worklet.js");

    source = audioContext.createMediaStreamSource(stream);
    workletNode = new AudioWorkletNode(audioContext, "pcm-forwarder", {
      numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1,
    });
    source.connect(workletNode);

    recorderWorker = new Worker("web/recorder_worker.js");
    recorderWorker.postMessage({
      command: "init",
      config: { sampleRate: audioContext.sampleRate, targetSampleRate: 16000 },
    });
    recorderWorker.onmessage = (e) => {
      if (captureWs && captureWs.readyState === WebSocket.OPEN) captureWs.send(e.data.buffer);
    };
    workletNode.port.onmessage = (e) => {
      recorderWorker.postMessage({ command: "record", buffer: e.data }, [e.data.buffer]);
    };

    connectCapture();
    running = true;
    console.log("[offscreen] capture running -> glue /capture");
  } catch (err) {
    console.error("[offscreen] start failed:", err);
  }
}

function connectCapture() {
  try { captureWs && captureWs.close(); } catch (e) {}
  captureWs = new WebSocket(CAPTURE_URL);
  captureWs.binaryType = "arraybuffer";
  captureWs.onopen = () => console.log("[offscreen] glue capture connected");
  captureWs.onclose = () => {
    console.warn("[offscreen] glue capture disconnected, reconnecting in 2s");
    if (running) captureReconnectTimer = setTimeout(connectCapture, 2000);
  };
}

function stop() {
  running = false;
  clearTimeout(captureReconnectTimer);
  try { recorderWorker && recorderWorker.terminate(); } catch (e) {}
  try { workletNode && workletNode.disconnect(); } catch (e) {}
  try { source && source.disconnect(); } catch (e) {}
  try { stream && stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
  try { audioContext && audioContext.close(); } catch (e) {}
  try { captureWs && captureWs.close(); } catch (e) {}
  stream = audioContext = source = workletNode = recorderWorker = captureWs = null;
  console.log("[offscreen] stopped");
}

ready = true;
drainPending();

