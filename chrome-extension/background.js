// Relatima Translate · background service worker
// 点击扩展图标 = 开始/停止同传。抓流实际在 offscreen 文档里跑，
// 所以弹窗/焦点变化都不影响，可后台常驻。

let capturing = false;
let captureTabId = null;

async function ensureOffscreen() {
  const ctx = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (ctx.length > 0) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Capture tab audio continuously for real-time dubbing",
  });
}

async function closeOffscreen() {
  const ctx = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (ctx.length > 0) await chrome.offscreen.closeDocument();
}

function setBadge(text, color) {
  try {
    chrome.action.setBadgeText({ text });
    if (color) chrome.action.setBadgeBackgroundColor({ color });
  } catch (e) { /* ignore */ }
}

// 点图标：开始 / 停止
chrome.action.onClicked.addListener(async (tab) => {
  try {
    if (capturing) {
      await stopCapture();
      setBadge("");
    } else {
      await startCapture(tab.id);
    }
  } catch (err) {
    console.error("[background] toggle failed:", err);
    setBadge("!", "#c62828");
    setTimeout(() => setBadge(capturing ? "ON" : ""), 3000);
  }
});

async function startCapture(tabId) {
  await ensureOffscreen();

  // 等待 offscreen 就绪（最多 5s）
  for (let i = 0; i < 50; i++) {
    try {
      const r = await chrome.runtime.sendMessage({ type: "PING" });
      if (r && r.ready) break;
    } catch (e) { /* not ready yet */ }
    await new Promise((res) => setTimeout(res, 100));
  }

  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  captureTabId = tabId;
  await chrome.runtime.sendMessage({ type: "SET_STREAM", streamId, tabId });
  capturing = true;
  setBadge("ON", "#2e7d32");
  console.log("[background] capture started on tab", tabId);
}

async function stopCapture() {
  capturing = false;
  captureTabId = null;
  try { await chrome.runtime.sendMessage({ type: "STOP" }); } catch (e) {}
  await closeOffscreen();
  console.log("[background] capture stopped");
}

// 捕获的标签页被关闭时自动停止
chrome.tabs.onRemoved.addListener(async (tabId) => {
  if (capturing && captureTabId === tabId) {
    await stopCapture();
    setBadge("");
  }
});

// 保活：定期 ping offscreen，防止被回收
setInterval(async () => {
  if (!capturing) return;
  try { await chrome.runtime.sendMessage({ type: "PING" }); } catch (e) {}
}, 20000);

// SW 重启后恢复 badge 状态
chrome.runtime.onStartup.addListener(() => {
  setBadge(capturing ? "ON" : "");
});
