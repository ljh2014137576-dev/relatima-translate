// Relatima Translate · offscreen document
// 常驻运行：抓取标签页音频 → PCM → WLK WS(:8000) → 转发翻译消息到 glue 中继(:5100)。
// 与 popup 无关，弹窗关闭/点击视频都不会中断。

const WLK_URL = "ws://127.0.0.1:8000/asr?target_language=zh";
const GLUE_URL = "ws://127.0.0.1:5100/relay";

let ready = false;
let stream = null;
let audioContext = null;
let source = null;
let workletNode = null;
let recorderWorker = null;
let wlkWs = null;
let glueWs = null;
let glueReconnectTimer = null;
let wlkReconnectTimer = null;
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
      if (wlkWs && wlkWs.readyState === WebSocket.OPEN) wlkWs.send(e.data.buffer);
    };
    workletNode.port.onmessage = (e) => {
      recorderWorker.postMessage({ command: "record", buffer: e.data }, [e.data.buffer]);
    };

    connectWlk();
    connectGlue();
    running = true;
    console.log("[offscreen] capture running");
  } catch (err) {
    console.error("[offscreen] start failed:", err);
  }
}

function connectWlk() {
  try { wlkWs && wlkWs.close(); } catch (e) {}
  wlkWs = new WebSocket(WLK_URL);
  wlkWs.binaryType = "arraybuffer";
  wlkWs.onopen = () => console.log("[offscreen] WLK connected");
  wlkWs.onmessage = (ev) => {
    // 转发翻译结果到 glue（glue 会配音）
    if (glueWs && glueWs.readyState === WebSocket.OPEN) {
      try { glueWs.send(ev.data); } catch (e) {}
    }
  };
  wlkWs.onclose = () => {
    console.warn("[offscreen] WLK disconnected, reconnecting in 3s");
    wlkReconnectTimer = setTimeout(connectWlk, 3000);
  };
}

function connectGlue() {
  try { glueWs && glueWs.close(); } catch (e) {}
  glueWs = new WebSocket(GLUE_URL);
  glueWs.onopen = () => console.log("[offscreen] glue relay connected");
  glueWs.onclose = () => {
    console.warn("[offscreen] glue relay disconnected, reconnecting in 3s");
    glueReconnectTimer = setTimeout(connectGlue, 3000);
  };
}

function stop() {
  running = false;
  clearTimeout(glueReconnectTimer);
  clearTimeout(wlkReconnectTimer);
  try { recorderWorker && recorderWorker.terminate(); } catch (e) {}
  try { workletNode && workletNode.disconnect(); } catch (e) {}
  try { source && source.disconnect(); } catch (e) {}
  try { stream && stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
  try { audioContext && audioContext.close(); } catch (e) {}
  try { wlkWs && wlkWs.close(); } catch (e) {}
  try { glueWs && glueWs.close(); } catch (e) {}
  stream = audioContext = source = workletNode = recorderWorker = wlkWs = glueWs = null;
  console.log("[offscreen] stopped");
}

ready = true;
drainPending();
