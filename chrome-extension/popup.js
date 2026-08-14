const CONTROL = "http://127.0.0.1:5120";

const dot = document.getElementById("dot");
const toggle = document.getElementById("toggle");
const wsCheck = document.getElementById("websearch");
const statusEl = document.getElementById("status");

async function getState() {
  let running = false;
  try {
    const r = await chrome.runtime.sendMessage({ type: "STATUS" });
    running = !!(r && r.running);
  } catch (e) {}
  let ws = { enabled: false };
  try {
    const r = await fetch(`${CONTROL}/websearch`);
    ws = await r.json();
  } catch (e) { console.error("[popup] control unreachable:", e); }
  return { running, websearch: !!ws.enabled };
}

async function render() {
  const s = await getState();
  dot.className = "dot" + (s.running ? " on" : "");
  toggle.textContent = s.running ? "■ 停止同传" : "● 开始同传";
  toggle.classList.toggle("on", s.running);
  wsCheck.checked = s.websearch;
  statusEl.textContent = s.running ? "后台抓流中，正在同传…" : "空闲（打开视频→静音标签页→开始同传）";
}

toggle.addEventListener("click", async () => {
  const s = await getState();
  if (s.running) {
    await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
  } else {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) { statusEl.textContent = "未找到当前标签页"; return; }
    const r = await chrome.runtime.sendMessage({ type: "START", tabId: tab.id }).catch(() => null);
    if (!r || !r.ok) { statusEl.textContent = "启动失败：" + (r && r.error || "未知"); return; }
  }
  render();
});

wsCheck.addEventListener("change", async () => {
  try {
    await fetch(`${CONTROL}/websearch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: wsCheck.checked }),
    });
    statusEl.textContent = wsCheck.checked ? "已开启联网查证" : "已关闭联网查证";
  } catch (e) {
    statusEl.textContent = "联网查证控制失败（glue 未运行？）";
    wsCheck.checked = !wsCheck.checked;
  }
});

render();
