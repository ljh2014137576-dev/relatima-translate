const CONTROL = "http://127.0.0.1:5120";
const CAPTURE = "ws://127.0.0.1:5100/capture";
const KEY = "websearchEnabled";

const toggle = document.getElementById("toggle");
const wsCheck = document.getElementById("websearch");
const statusEl = document.getElementById("status");
const retryBtn = document.getElementById("retry");
const verEl = document.getElementById("ver");

// show the loaded extension version so we can confirm a fresh install
if (verEl && chrome.runtime && chrome.runtime.getManifest) {
  verEl.textContent = "v" + chrome.runtime.getManifest().version;
}

function setStatus(html, cls) {
  statusEl.className = "status" + (cls ? " " + cls : "");
  statusEl.innerHTML = html;
}

async function fetchWithTimeout(url, opts, ms = 2500) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try { return await fetch(url, { ...opts, signal: ctl.signal }); }
  finally { clearTimeout(t); }
}

async function testWSCapture() {
  return new Promise((resolve) => {
    let ws;
    const t = setTimeout(() => { try { ws && ws.close(); } catch (e) {} resolve(false); }, 2500);
    try {
      ws = new WebSocket(CAPTURE);
      ws.onopen = () => { clearTimeout(t); ws.close(); resolve(true); };
      ws.onerror = () => { clearTimeout(t); resolve(false); };
    } catch (e) { clearTimeout(t); resolve(false); }
  });
}

async function tryControl() {
  for (const base of [CONTROL, "http://localhost:5120"]) {
    try {
      const r = await fetchWithTimeout(`${base}/websearch`);
      if (r.ok) return true;
    } catch (e) {}
  }
  return false;
}

async function detect() {
  // HTTP control (:5120)
  const http = await tryControl();

  // WS capture (:5100)
  const ws = await testWSCapture();

  if (http && ws) {
    setStatus("本地 Agent 已连接<br><span class='hint'>HTTP 控制 ✓ &nbsp;·&nbsp; 音频通道 ✓</span>", "ok");
  } else if (http || ws) {
    setStatus("本地 Agent 部分可达<br><span class='hint'>HTTP 控制 " + (http ? "✓" : "✗") + " · 音频通道 " + (ws ? "✓" : "✗") + "<br>请确认 glue 已启动（start_services.ps1）</span>", "err");
  } else {
    setStatus("无法连接本地 Agent<br><span class='hint'>请先运行 dubbing\\start_services.ps1 启动 glue</span>", "err");
  }
  return { http, ws };
}

async function readCaptureState() {
  try {
    const r = await chrome.runtime.sendMessage({ type: "STATUS" });
    return !!(r && r.running);
  } catch (e) { return false; }
}

async function render() {
  const running = await readCaptureState();
  toggle.textContent = running ? "■ 停止同传" : "● 开始同传";
  toggle.classList.toggle("on", running);

  // 联网查证：先读本地记忆，再尝试与 glue 同步
  const stored = await chrome.storage.local.get(KEY).catch(() => ({}));
  wsCheck.checked = stored[KEY] === true;
  try {
    const r = await fetchWithTimeout(`${CONTROL}/websearch`);
    const j = await r.json();
    wsCheck.checked = !!j.enabled;
  } catch (e) {
    // glue 不可达：保持本地记忆
  }
}

toggle.addEventListener("click", async () => {
  const running = await readCaptureState();
  if (running) {
    await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
    setStatus("已停止同传", "");
  } else {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) { setStatus("未找到当前标签页", "err"); return; }
    const r = await chrome.runtime.sendMessage({ type: "START", tabId: tab.id }).catch(() => null);
    if (!r || !r.ok) {
      setStatus("启动失败：" + ((r && r.error) || "未知错误，看 Service Worker 控制台"), "err");
      return;
    }
    setStatus("已开始同传，后台运行中（关掉本窗口不影响）", "ok");
  }
  render();
});

wsCheck.addEventListener("change", async () => {
  const enabled = wsCheck.checked;
  await chrome.storage.local.set({ [KEY]: enabled });
  try {
    await fetchWithTimeout(`${CONTROL}/websearch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  } catch (e) {
    setStatus("已记住设置，但 glue 未连接（稍后会自动生效）", "err");
  }
});

retryBtn.addEventListener("click", async () => {
  setStatus("正在检测…", "");
  await detect();
  await render();
});

(async () => {
  await render();
  await detect();
})();
