// Web UI mirrors the compact Qt screen and keeps the WebSocket data bridge.
const RECENT_RECORD_STORAGE_KEY = "qiansai-recent-records-v1";
const RECENT_RECORD_BUCKET_MS = 5 * 60 * 1000;

const state = {
  phase: 0,
  frameCount: 0,
  parserErr: 0,
  crcErr: 0,
  wsConnected: false,
  wsReconnectTimer: null,
  lastDataAt: 0,
  polling: false,
  serverVitals: null,
  serverHeartWave: [],
  serverBreathWave: [],
  heartValid: false,
  breathValid: false,
  lastHeartZeroAt: 0,
  lastBreathZeroAt: 0,
  lastTrendDrawAt: 0,
  yoloFile: null,
  yoloImageUrl: "",
  recentRecords: loadRecentRecords(),
  lastRecentSignature: "",
  lastRecentRecordAt: 0,
  heartTrendValues: [],
  breathTrendValues: [],
  motionTrendValues: [],
  boardTimeMs: 0,
  lastFrameMs: 0,
};

const ZERO_APPEND_POINTS = 12;
const ZERO_APPEND_INTERVAL_MS = 2000;
const RECENT_RECORD_MIN_INTERVAL_MS = 5 * 60 * 1000;

const els = {
  navItems: document.querySelectorAll(".nav-item"),
  screens: document.querySelectorAll(".screen"),
  currentTime: document.getElementById("currentTime"),
  hrValue: document.getElementById("hrValue"),
  brValue: document.getElementById("brValue"),
  hrTag: document.getElementById("hrTag"),
  brTag: document.getElementById("brTag"),
  motionValue: document.getElementById("motionValue"),
  alarmCount: document.getElementById("alarmCount"),
  frameCount: document.getElementById("frameCount"),
  parserErr: document.getElementById("parserErr"),
  crcErr: document.getElementById("crcErr"),
  heartReadout: document.getElementById("heartReadout"),
  breathReadout: document.getElementById("breathReadout"),
  heartWave: document.getElementById("heartWave"),
  breathWave: document.getElementById("breathWave"),
  heartTrend: document.getElementById("heartTrend"),
  breathTrend: document.getElementById("breathTrend"),
  motionTrend: document.getElementById("motionTrend"),
  captureImage: document.querySelector(".image-frame-reference img"),
  cameraButtons: document.querySelectorAll(".image-actions button"),
  imageStatusValue: document.getElementById("imageStatusValue"),
  imageStatusFoot: document.getElementById("imageStatusFoot"),
  bedsideStateValue: document.getElementById("bedsideStateValue"),
  bedsideStateFoot: document.getElementById("bedsideStateFoot"),
  safetyValue: document.getElementById("safetyValue"),
  safetyFoot: document.getElementById("safetyFoot"),
  careCaptureTime: document.getElementById("careCaptureTime"),
  careResult: document.getElementById("careResult"),
  careRadar: document.getElementById("careRadar"),
  careNote: document.getElementById("careNote"),
  yoloPageStatus: document.getElementById("yoloPageStatus"),
  yoloPreview: document.getElementById("yoloPreview"),
  yoloPreviewImage: document.getElementById("yoloPreviewImage"),
  yoloFileInput: document.getElementById("yoloFileInput"),
  yoloConf: document.getElementById("yoloConf"),
  yoloIou: document.getElementById("yoloIou"),
  yoloDetectBed: document.getElementById("yoloDetectBed"),
  yoloDetectCount: document.getElementById("yoloDetectCount"),
  yoloDetectConfidence: document.getElementById("yoloDetectConfidence"),
  yoloDetectTime: document.getElementById("yoloDetectTime"),
  yoloOpenImage: document.getElementById("yoloOpenImage"),
  yoloRunDetect: document.getElementById("yoloRunDetect"),
  yoloClear: document.getElementById("yoloClear"),
  yoloZoom: document.getElementById("yoloZoom"),
  yoloResultBody: document.getElementById("yoloResultBody"),
  recentTimes: [0, 1, 2].map((index) => document.getElementById(`recentTime${index}`)),
  recentTexts: [0, 1, 2].map((index) => document.getElementById(`recentText${index}`)),
  recentBadges: [0, 1, 2].map((index) => document.getElementById(`recentBadge${index}`)),
  recentMore: document.getElementById("recentMore"),
  recordModal: document.getElementById("recordModal"),
  recordClose: document.getElementById("recordClose"),
  recordList: document.getElementById("recordList"),
  externalTunnelUrl: document.getElementById("externalTunnelUrl"),
  copyTunnelUrl: document.getElementById("copyTunnelUrl"),
};

const BED_TEXT = { 0: "离床", 1: "在床", 2: "无" };
const SLEEP_TEXT = { 0: "深睡", 1: "浅睡", 2: "清醒", 3: "无人" };
const BED_WORD_REPLACEMENTS = [
  ["患者在床，姿态正常", "体征平稳"],
  ["在床，体征平稳", "体征平稳"],
  ["在床，等待体征", "等待体征"],
  ["无人，离床", "无人状态"],
  ["离床或无人，建议确认", "无人状态，建议确认"],
  ["入床", ""],
  ["在床", ""],
  ["离床", "无人状态"],
];

function initNav() {
  els.navItems.forEach((item) => {
    item.addEventListener("click", () => {
      activateScreen(item.dataset.target);
      window.history.replaceState(null, "", `#${item.dataset.target}`);
    });
  });
}

function initRecordModal() {
  els.recentMore?.addEventListener("click", openRecordModal);
  els.recordClose?.addEventListener("click", closeRecordModal);
  els.recordModal?.addEventListener("click", (event) => {
    if (event.target === els.recordModal) closeRecordModal();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeRecordModal();
  });
}

function cutoffTime(days) {
  return Date.now() - days * 24 * 60 * 60 * 1000;
}

function normalizeRecentRecords(records, days = 30) {
  if (!Array.isArray(records)) return [];
  const cutoff = cutoffTime(days);
  const normalized = records
    .filter((record) => record && Number.isFinite(Date.parse(record.isoTime)) && Date.parse(record.isoTime) >= cutoff)
    .sort((a, b) => Date.parse(b.isoTime) - Date.parse(a.isoTime));
  const spaced = [];
  for (const record of normalized) {
    const recordTime = Date.parse(record.isoTime);
    const tooClose = spaced.some((saved) => Math.abs(Date.parse(saved.isoTime) - recordTime) < RECENT_RECORD_MIN_INTERVAL_MS);
    if (!tooClose) spaced.push(record);
    if (spaced.length >= 240) break;
  }
  return spaced;
}

function loadRecentRecords() {
  try {
    return normalizeRecentRecords(JSON.parse(localStorage.getItem(RECENT_RECORD_STORAGE_KEY) || "[]"));
  } catch (_) {
    return [];
  }
}

async function syncRecentRecordsFromServer() {
  try {
    const response = await fetch(appUrl(`/care/records?t=${Date.now()}`), { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return;
    const payload = await response.json();
    if (!Array.isArray(payload.records)) return;
    state.recentRecords = normalizeRecentRecords(mergeRecentRecords(payload.records, state.recentRecords));
    updateRecentRecordState();
    saveRecentRecords(false);
    renderRecentPreview();
  } catch (_) {
    // Keep browser-side records when the board-side file is temporarily unavailable.
  }
}

function mergeRecentRecords(...groups) {
  const map = new Map();
  groups.flat().forEach((record) => {
    if (!record || !record.isoTime) return;
    const key = `${record.isoTime}|${record.text || ""}|${record.signature || ""}`;
    map.set(key, record);
  });
  return Array.from(map.values());
}

function updateRecentRecordState() {
  const latest = normalizeRecentRecords(state.recentRecords)[0];
  state.lastRecentRecordAt = latest ? Date.parse(latest.isoTime) || 0 : 0;
  state.lastRecentSignature = latest?.signature || "";
}

function saveRecentRecords(pushServer = true) {
  state.recentRecords = normalizeRecentRecords(state.recentRecords);
  updateRecentRecordState();
  try {
    localStorage.setItem(RECENT_RECORD_STORAGE_KEY, JSON.stringify(state.recentRecords));
  } catch (_) {
    // Ignore storage failures on embedded browsers.
  }
  if (pushServer) persistRecentRecordsToServer();
}

async function persistRecentRecordsToServer() {
  try {
    await fetch(appUrl("/care/records"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records: state.recentRecords }),
      credentials: "same-origin",
    });
  } catch (_) {
    // Browser localStorage still keeps a copy until the next successful sync.
  }
}

function renderRecentPreview() {
  const records = normalizeRecentRecords(state.recentRecords).slice(0, 3);
  [0, 1, 2].forEach((index) => {
    const record = records[index];
    if (els.recentTimes[index]) els.recentTimes[index].textContent = record ? `● ${record.time}` : "● --:--:--";
    if (els.recentTexts[index]) els.recentTexts[index].textContent = record ? sanitizeBedText(record.text, "护理记录") : "暂无记录";
    if (els.recentBadges[index]) els.recentBadges[index].textContent = record ? record.badge : "等待";
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}

function openRecordModal() {
  renderRecordList();
  els.recordModal?.classList.add("open");
  els.recordModal?.setAttribute("aria-hidden", "false");
}

function closeRecordModal() {
  els.recordModal?.classList.remove("open");
  els.recordModal?.setAttribute("aria-hidden", "true");
}

function renderRecordList() {
  if (!els.recordList) return;
  const records = normalizeRecentRecords(state.recentRecords, 30);
  if (!records.length) {
    els.recordList.innerHTML = `<div class="record-empty">最近三十天暂无护理记录</div>`;
    return;
  }
  els.recordList.innerHTML = records.map((record) => `
    <article class="record-item">
      <time>${escapeHtml(record.displayTime || record.time || "--")}</time>
      <strong>${escapeHtml(sanitizeBedText(record.text, "护理记录"))}</strong>
      <em>${escapeHtml(record.badge || "已记录")}</em>
      <p>${escapeHtml(sanitizeBedText(record.note, ""))}</p>
    </article>
  `).join("");
}

function activateScreen(target) {
  const screen = document.getElementById(target);
  const navItem = document.querySelector(`[data-target="${target}"]`);
  if (!screen || !navItem) return;
  els.navItems.forEach((nav) => nav.classList.remove("active"));
  els.screens.forEach((item) => item.classList.remove("active"));
  navItem.classList.add("active");
  screen.classList.add("active");
  drawStaticCharts();
}

function getWsUrl() {
  const loc = window.location;
  const protocol = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${loc.host}/ws`;
}

function appUrl(path) {
  return `${window.location.origin}${path}`;
}

async function refreshTunnelUrl() {
  if (!els.externalTunnelUrl) return;
  try {
    // 外网 quick tunnel 会变，这里定时从板端读取当前有效地址。
    const response = await fetch(appUrl(`/tunnel-url?t=${Date.now()}`), {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`tunnel ${response.status}`);
    const url = (await response.text()).trim();
    const ready = url.startsWith("https://");
    els.externalTunnelUrl.textContent = url || "等待外网地址";
    els.externalTunnelUrl.href = ready ? url : "#";
  } catch (_) {
    els.externalTunnelUrl.textContent = "外网地址获取失败";
    els.externalTunnelUrl.href = "#";
  }
}

function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
  return Promise.resolve();
}

function initTunnelUrl() {
  els.copyTunnelUrl?.addEventListener("click", async () => {
    const url = els.externalTunnelUrl?.textContent?.trim() || "";
    if (!url.startsWith("https://")) return;
    await copyText(url);
    els.copyTunnelUrl.textContent = "已复制";
    setTimeout(() => {
      els.copyTunnelUrl.textContent = "复制外网地址";
    }, 1200);
  });
  refreshTunnelUrl();
  setInterval(refreshTunnelUrl, 5000);
}

let ws = null;

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(getWsUrl());
    ws.onopen = () => {
      state.wsConnected = true;
    };
    ws.onclose = () => {
      state.wsConnected = false;
      scheduleReconnect();
    };
    ws.onerror = () => {
      state.wsConnected = false;
      scheduleReconnect();
    };
    ws.onmessage = handleWsMessage;
  } catch (_) {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (state.wsReconnectTimer) return;
  state.wsReconnectTimer = setTimeout(() => {
    state.wsReconnectTimer = null;
    connectWebSocket();
  }, 5000);
}

function handleWsMessage(event) {
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch (_) {
    return;
  }

  applyRealtimeMessage(msg);
}

function applyRealtimeMessage(msg) {
  state.lastDataAt = Date.now();
  if (Number.isFinite(Number(msg.ts))) state.boardTimeMs = Number(msg.ts);
  if (Number.isFinite(Number(msg.last_frame_ms))) state.lastFrameMs = Number(msg.last_frame_ms);
  if (msg.type === "vital_signs") {
    state.serverVitals = msg.data;
    if (Number.isFinite(Number(msg.data?.ts))) state.boardTimeMs = Number(msg.data.ts);
    if (Number.isFinite(Number(msg.data?.lastFrameMs))) state.lastFrameMs = Number(msg.data.lastFrameMs);
  }
  if (msg.type === "waveform") {
    if (state.serverVitals?.heartValid) {
      state.serverHeartWave = Array.isArray(msg.heart) ? msg.heart : [];
    } else {
      appendZeroWave("heart");
    }
    if (state.serverVitals?.breathValid) {
      state.serverBreathWave = Array.isArray(msg.breath) ? msg.breath : [];
    } else {
      appendZeroWave("breath");
    }
  }
  if (msg.type === "stats") {
    state.frameCount = msg.frame_count ?? state.frameCount;
    state.parserErr = msg.parser_err || 0;
    state.crcErr = msg.crc_err || 0;
  }
}

function appendZeroWave(kind) {
  const key = kind === "heart" ? "serverHeartWave" : "serverBreathWave";
  const timeKey = kind === "heart" ? "lastHeartZeroAt" : "lastBreathZeroAt";
  const previous = state[key] || [];
  if (!previous.length) return;
  const now = Date.now();
  if (now - state[timeKey] < ZERO_APPEND_INTERVAL_MS) return;
  state[key] = [...previous, ...Array(ZERO_APPEND_POINTS).fill(128)].slice(-previous.length);
  state[timeKey] = now;
}

function mapRawSnapshot(raw) {
  const human = raw.human || {};
  const heart = raw.heart || {};
  const breath = raw.breath || {};
  const sleep = raw.sleep || {};
  const system = raw.system || {};
  const care = raw.care || null;
  const online = system.online !== false;
  const hr = Number(heart.rate || 0);
  const br = Number(breath.rate || 0);
  const heartValid = online && hr > 0;
  const breathValid = online && br > 0;
  const toDisplayWave = (values) => (
    Array.isArray(values)
      ? values.map((v) => Math.max(0, Math.min(255, Number(v) + 128)))
      : []
  );
  return [
    {
      type: "vital_signs",
      data: {
        hr,
        br,
        motion: Number(human.motion_val || 0),
        presence: Number(human.exist || 0) === 1 ? "有人" : "无人",
        stability: SLEEP_TEXT[Number(sleep.state ?? 3)] || "未知",
        bedState: BED_TEXT[Number(sleep.bed ?? 0)] || "未知",
        breathState: Number(breath.state || 0),
        online,
        ts: Number(raw.timestamp_ms || raw.ts || Date.now()),
        lastFrameMs: Number(system.last_frame_ms || system.last_frame_ts_ms || system.last_frame_ts || 0),
        heartValid,
        breathValid,
        care,
      },
    },
    {
      type: "waveform",
      heart: toDisplayWave(heart.wave),
      breath: toDisplayWave(breath.wave),
      frame_count: Number(system.frame_count || 0),
      ts: Number(raw.timestamp_ms || raw.ts || Date.now()),
      last_frame_ms: Number(system.last_frame_ms || system.last_frame_ts_ms || system.last_frame_ts || 0),
    },
    {
      type: "stats",
      frame_count: Number(system.frame_count || 0),
      parser_err: Number(system.parse_error_count || 0),
      crc_err: Number(system.checksum_error_count || 0),
      ts: Number(raw.timestamp_ms || raw.ts || Date.now()),
      last_frame_ms: Number(system.last_frame_ms || system.last_frame_ts_ms || system.last_frame_ts || 0),
    },
  ];
}

async function pollRadarSnapshot() {
  if (state.polling) return;
  state.polling = true;
  try {
    const response = await fetch(appUrl(`/radar/raw?t=${Date.now()}`), { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error(`radar ${response.status}`);
    const raw = await response.json();
    mapRawSnapshot(raw).forEach(applyRealtimeMessage);
  } catch (_) {
    state.serverVitals = { ...(state.serverVitals || {}), online: false };
    appendZeroWave("heart");
    appendZeroWave("breath");
  } finally {
    state.polling = false;
  }
}

let cameraObserveTimer = null;
let lastCaptureUrl = "";

async function captureCameraFrame() {
  if (!els.captureImage) return;
  const button = els.cameraButtons[0];
  const originalText = button?.textContent;
  try {
    if (button) {
      button.disabled = true;
      button.textContent = "拍摄中...";
    }
    state.polling = true;
    const response = await fetch(appUrl("/camera/capture"), { method: "POST", cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error(`capture ${response.status}`);
    const blob = await response.blob();
    if (!blob.type.includes("image")) throw new Error("capture response is not image");
    if (lastCaptureUrl) URL.revokeObjectURL(lastCaptureUrl);
    lastCaptureUrl = URL.createObjectURL(blob);
    els.captureImage.src = lastCaptureUrl;
    if (button) button.textContent = "拍摄完成";
  } catch (_) {
    if (button) button.textContent = "拍摄失败";
  } finally {
    state.polling = false;
    pollRadarSnapshot();
    setTimeout(() => {
      if (!button) return;
      button.disabled = false;
      button.textContent = originalText || "▣ 拍摄一张";
    }, 900);
  }
}

function initCameraControls() {
  const buttons = Array.from(els.cameraButtons);
  if (!buttons.length) return;
  buttons[0]?.addEventListener("click", captureCameraFrame);
  buttons[1]?.addEventListener("click", () => {
    if (!cameraObserveTimer) cameraObserveTimer = setInterval(captureCameraFrame, 1500);
    captureCameraFrame();
  });
  buttons[2]?.addEventListener("click", () => {
    if (cameraObserveTimer) clearInterval(cameraObserveTimer);
    cameraObserveTimer = null;
  });
  buttons[3]?.addEventListener("click", () => {
    if (!els.captureImage?.src) return;
    const link = document.createElement("a");
    link.href = els.captureImage.src;
    link.download = `bedside_${Date.now()}.jpg`;
    link.click();
  });
}

function initYoloPage() {
  els.yoloOpenImage?.addEventListener("click", () => els.yoloFileInput?.click());
  els.yoloFileInput?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (file) await selectYoloFile(file);
  });
  els.yoloRunDetect?.addEventListener("click", runYoloDetect);
  els.yoloClear?.addEventListener("click", clearYoloPage);
  els.yoloZoom?.addEventListener("click", () => {
    if (els.yoloPreviewImage?.src) els.yoloPreviewImage.requestFullscreen?.();
  });
  renderYoloResult(null);
}

async function selectYoloFile(file) {
  if (els.yoloPageStatus) els.yoloPageStatus.textContent = "正在压缩图片";
  const previewFile = await compressYoloImage(file);
  state.yoloFile = previewFile;
  if (state.yoloImageUrl) URL.revokeObjectURL(state.yoloImageUrl);
  state.yoloImageUrl = URL.createObjectURL(previewFile);
  if (els.yoloPreviewImage) els.yoloPreviewImage.src = state.yoloImageUrl;
  els.yoloPreview?.classList.add("has-image");
  if (els.yoloPageStatus) els.yoloPageStatus.textContent = previewFile.name || "已选择图片";
  renderYoloResult({ pending: true });
}

function compressYoloImage(file) {
  const maxSide = 960;
  const quality = 0.82;
  if (!file.type.startsWith("image/")) return Promise.resolve(file);

  return new Promise((resolve) => {
    const image = new Image();
    const sourceUrl = URL.createObjectURL(file);
    image.onload = () => {
      URL.revokeObjectURL(sourceUrl);
      const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
      if (scale >= 1 && file.size <= 1.6 * 1024 * 1024) {
        resolve(file);
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const context = canvas.getContext("2d", { alpha: false });
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (!blob) {
          resolve(file);
          return;
        }
        const baseName = file.name.replace(/\.[^.]+$/, "") || "yolo-image";
        resolve(new File([blob], `${baseName}_compressed.jpg`, { type: "image/jpeg", lastModified: Date.now() }));
      }, "image/jpeg", quality);
    };
    image.onerror = () => {
      URL.revokeObjectURL(sourceUrl);
      resolve(file);
    };
    image.src = sourceUrl;
  });
}

function clearYoloPage() {
  state.yoloFile = null;
  if (state.yoloImageUrl) URL.revokeObjectURL(state.yoloImageUrl);
  state.yoloImageUrl = "";
  if (els.yoloPreviewImage) els.yoloPreviewImage.removeAttribute("src");
  if (els.yoloFileInput) els.yoloFileInput.value = "";
  els.yoloPreview?.classList.remove("has-image");
  if (els.yoloPageStatus) els.yoloPageStatus.textContent = "等待图片";
  renderYoloResult(null);
}

async function runYoloDetect() {
  if (!state.yoloFile) {
    if (els.yoloPageStatus) els.yoloPageStatus.textContent = "请先选择图片";
    return;
  }
  if (els.yoloRunDetect) {
    els.yoloRunDetect.disabled = true;
    els.yoloRunDetect.textContent = "检测中...";
  }
  if (els.yoloPageStatus) els.yoloPageStatus.textContent = "检测中";
  try {
    const form = new FormData();
    form.append("image", state.yoloFile);
    form.append("conf", els.yoloConf?.value || "0.25");
    form.append("iou", els.yoloIou?.value || "0.70");
    const startedAt = performance.now();
    const response = await fetch(appUrl("/yolo/detect"), {
      method: "POST",
      body: form,
      cache: "no-store",
      credentials: "same-origin",
    });
    const data = await response.json();
    renderYoloResult({ ...data, elapsedMs: data.elapsedMs ?? Math.round(performance.now() - startedAt) });
    if (els.yoloPageStatus) els.yoloPageStatus.textContent = data.ok ? "检测完成" : "检测不可用";
  } catch (_) {
    renderYoloResult({ ok: false, detections: [], message: "检测失败", elapsedMs: 0 });
    if (els.yoloPageStatus) els.yoloPageStatus.textContent = "检测失败";
  } finally {
    if (els.yoloRunDetect) {
      els.yoloRunDetect.disabled = false;
      els.yoloRunDetect.textContent = "◎ 开始检测";
    }
  }
}

function renderYoloResult(result) {
  const pending = result?.pending;
  const detections = Array.isArray(result?.detections) ? result.detections : [];
  const confidence = Number(result?.confidence || result?.maxConfidence || 0);
  const bedOccupied = result?.bedOccupied;

  if (els.yoloDetectBed) {
    els.yoloDetectBed.textContent = pending
      ? "等待检测"
      : result && result.ok === false
        ? "检测不可用"
      : bedOccupied === true
        ? "判断在床"
        : bedOccupied === false
          ? "判断离床"
          : "等待图片";
  }
  if (els.yoloDetectCount) els.yoloDetectCount.textContent = String(detections.length);
  if (els.yoloDetectConfidence) els.yoloDetectConfidence.textContent = confidence > 0 ? `${Math.round(confidence * 100)}%` : "--";
  if (els.yoloDetectTime) els.yoloDetectTime.textContent = result?.elapsedMs ? `${Math.round(result.elapsedMs)} ms` : "--";

  if (!els.yoloResultBody) return;
  if (!result || pending) {
    els.yoloResultBody.innerHTML = `<tr><td colspan="5">${pending ? "图片已选择，等待开始检测" : "暂无检测结果"}</td></tr>`;
    return;
  }
  if (!detections.length) {
    els.yoloResultBody.innerHTML = `<tr><td colspan="5">${escapeHtml(result.message || "暂无检测结果")}</td></tr>`;
    return;
  }
  els.yoloResultBody.innerHTML = detections
    .map((item, index) => {
      const label = item.label || item.className || item.name || "目标";
      const score = Number(item.confidence || item.score || 0);
      const box = item.bbox || item.box || item.xyxy || [];
      return `<tr><td>${index + 1}</td><td>${escapeHtml(state.yoloFile?.name || "--")}</td><td>${escapeHtml(label)}</td><td>${Math.round(score * 100)}%</td><td>${escapeHtml(formatBox(box))}</td></tr>`;
    })
    .join("");
}

function formatBox(box) {
  if (!Array.isArray(box) || box.length < 4) return "--";
  return `[${box.slice(0, 4).map((value) => Math.round(Number(value) || 0)).join(", ")}]`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function resizeCanvas(canvas) {
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  return { ctx, width, height, ratio };
}

function drawQtGrid(ctx, w, h, ratio) {
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(47, 91, 145, 0.32)";
  ctx.lineWidth = 1 * ratio;
  for (let x = 0; x <= w; x += 18 * ratio) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h - 8 * ratio);
    ctx.stroke();
  }
  for (let y = 0; y <= h; y += 18 * ratio) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(123, 147, 184, 0.75)";
  ctx.beginPath();
  ctx.moveTo(34 * ratio, 0);
  ctx.lineTo(34 * ratio, h - 8 * ratio);
  ctx.lineTo(w - 8 * ratio, h - 8 * ratio);
  ctx.stroke();

  ctx.fillStyle = "rgba(212, 221, 235, 0.9)";
  ctx.font = `${15 * ratio}px "Microsoft YaHei UI"`;
  ["2", "1", "0", "-1", "-2"].forEach((label, index) => {
    const y = (18 + index * ((h / ratio - 34) / 4)) * ratio;
    ctx.fillText(label, 6 * ratio, y);
  });
}

function drawWave(canvas, color, phase, slow, serverData) {
  const target = resizeCanvas(canvas);
  if (!target) return;
  const { ctx, width: w, height: h, ratio } = target;
  drawQtGrid(ctx, w, h, ratio);

  const points = [];
  const start = 34 * ratio;
  const end = w - 8 * ratio;
  for (let x = start; x <= end; x += 2 * ratio) {
    let sample;
    if (serverData && serverData.length) {
      const idx = Math.floor(((x - start) / Math.max(1, end - start)) * serverData.length);
      sample = ((serverData[idx] || 128) - 128) / 128;
    } else if (state.lastDataAt > 0) {
      sample = 0;
    } else if (slow) {
      const t = (x / ratio + phase * 0.78) / 34;
      sample = 0.72 * Math.sin(t) + 0.13 * Math.sin(2 * t - 0.75) - 0.04 * Math.sin(3 * t + 0.45);
    } else {
      const t = (x / ratio + phase * 2.15) / 46;
      sample = Math.sin(t) * 0.85 + Math.sin(t * 0.5) * 0.08;
    }
    const scale = h * (slow ? 0.30 : 0.42);
    points.push([x, h / 2 - sample * scale]);
  }

  // 鍏堢敾鍙戝厜鎻忚竟锛屽啀鐢诲疄绾匡紝璐磋繎 Qt 鐨?QPainter 娉㈠舰璐ㄦ劅銆?  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = color.replace("1)", "0.32)");
  ctx.lineWidth = 8 * ratio;
  strokeSmoothPath(ctx, points);
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.2 * ratio;
  strokeSmoothPath(ctx, points);
}

function strokeSmoothPath(ctx, points) {
  if (!points.length) return;
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i += 1) {
    const [px, py] = points[i - 1];
    const [x, y] = points[i];
    ctx.bezierCurveTo(px + (x - px) * 0.5, py, px + (x - px) * 0.5, y, x, y);
  }
  ctx.stroke();
}

function updateMetrics() {
  let hr = 0;
  let br = 0;
  let motion = 0;
  let presence = "等待数据";
  let carePresence = "等待";

  if (state.serverVitals) {
    hr = Math.round(state.serverVitals.hr ?? 0);
    br = Math.round(state.serverVitals.br ?? 0);
    motion = Math.round(state.serverVitals.motion ?? 0);
    carePresence = state.serverVitals.presence || "等待";
    if (state.serverVitals.presence) presence = `${carePresence} / ${state.serverVitals.stability || "清醒"}`;
  }
  const online = state.serverVitals?.online !== false && Date.now() - state.lastDataAt < 4000;
  const heartValid = online && state.serverVitals?.heartValid && hr > 0;
  const breathValid = online && state.serverVitals?.breathValid && br > 0;

  els.hrValue.textContent = heartValid ? String(hr) : "0";
  els.brValue.textContent = breathValid ? String(br) : "0";
  els.hrTag.textContent = heartValid ? (hr >= 60 && hr <= 100 ? "●  正常" : "●  异常") : "●  无有效心率";
  els.brTag.textContent = breathValid ? (br >= 12 && br <= 20 ? "●  正常" : "●  异常") : "●  无有效呼吸";
  if (els.motionValue) els.motionValue.textContent = `轻微 ${motion}/100  •  ${online ? "实时刷新" : "等待数据"}`;
  els.heartReadout.textContent = `HR:  ${heartValid ? hr : 0} bpm`;
  els.breathReadout.textContent = `RR:  ${breathValid ? br : 0} rpm`;
  if (els.frameCount) els.frameCount.textContent = String(state.frameCount);
  if (els.parserErr) els.parserErr.textContent = String(state.parserErr);
  if (els.crcErr) els.crcErr.textContent = String(state.crcErr);
  updateTrendSamples({ hr: heartValid ? hr : 0, br: breathValid ? br : 0, motion, online });
  updateClock();
  updateCareRecords({ hr, br, motion, presence: carePresence, online, heartValid, breathValid });
}

function pushBounded(values, value, limit = 7) {
  values.push(value);
  while (values.length > limit) values.shift();
}

function updateTrendSamples({ hr, br, motion, online }) {
  if (!online) return;
  pushBounded(state.heartTrendValues, hr || 0);
  pushBounded(state.breathTrendValues, br || 0);
  pushBounded(state.motionTrendValues, Math.max(0, Math.min(100, motion || 0)));
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatDateTime(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}  ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function formatTime(date) {
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function updateClock() {
  if (!els.currentTime) return;
  const base = state.boardTimeMs || Date.now();
  const drift = state.lastDataAt ? Date.now() - state.lastDataAt : 0;
  const displayDate = new Date(base + Math.max(0, drift));
  const frameSuffix = state.lastFrameMs ? `  帧 ${formatTime(new Date(state.lastFrameMs))}` : "";
  els.currentTime.textContent = `▣  ${formatDateTime(displayDate)}${frameSuffix}`;
}

function updateCareRecords({ hr, br, motion, presence, online, heartValid, breathValid }) {
  const now = new Date();
  const vitals = state.serverVitals || {};
  const care = vitals.care || {};
  const result = sanitizeBedText(care.result, "等待实时数据");
  const note = sanitizeBedText(care.note || (online ? "等待生命体征" : "等待雷达刷新"), "等待生命体征");
  const radar = sanitizeBedText(care.radar || "等待影像复核", "等待影像复核");
  const safe = care.safety || "关注";

  if (els.imageStatusValue) els.imageStatusValue.textContent = online ? "观察中" : "待观察";
  if (els.imageStatusFoot) els.imageStatusFoot.textContent = `● 最新 ${formatTime(now)}`;
  if (els.bedsideStateValue) els.bedsideStateValue.textContent = online ? "清晰" : "待复核";
  if (els.bedsideStateFoot) els.bedsideStateFoot.textContent = online ? "● 画面完整显示" : "● 等待刷新";
  if (els.safetyValue) els.safetyValue.textContent = safe;
  if (els.safetyFoot) els.safetyFoot.textContent = `● ${note}`;
  if (els.careCaptureTime) {
    els.careCaptureTime.textContent = `${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}  ${formatTime(now)}`;
  }
  if (els.careResult) els.careResult.textContent = result;
  if (els.careRadar) els.careRadar.textContent = radar;
  if (els.careNote) els.careNote.textContent = note;

  const canRecordByTime = !state.lastRecentRecordAt || now.getTime() - state.lastRecentRecordAt >= RECENT_RECORD_MIN_INTERVAL_MS;
  const bucket = Math.floor(now.getTime() / RECENT_RECORD_BUCKET_MS);
  const signature = `${care.signature || "care"}|${bucket}`;
  if (care.recordable && canRecordByTime && signature !== state.lastRecentSignature) {
    state.recentRecords.unshift({
      isoTime: now.toISOString(),
      displayTime: `${pad2(now.getMonth() + 1)}-${pad2(now.getDate())} ${formatTime(now)}`,
      time: formatTime(now),
      text: sanitizeBedText(care.recent_event, "护理记录"),
      badge: "已记录",
      note,
      signature,
    });
    saveRecentRecords();
  }

  renderRecentPreview();
  if (els.recordModal?.classList.contains("open")) renderRecordList();
}

function sanitizeBedText(value, fallback = "") {
  let text = String(value || fallback || "").trim();
  BED_WORD_REPLACEMENTS.forEach(([source, target]) => {
    text = text.split(source).join(target);
  });
  text = text.replace(/[，,]\s*[，,]/g, "，").replace(/^[，,\s]+|[，,\s]+$/g, "");
  return text || fallback;
}

function drawLineChart(canvas, values, color, min, max, labels) {
  const target = resizeCanvas(canvas);
  if (!target) return;
  const { ctx, width: w, height: h, ratio } = target;
  ctx.clearRect(0, 0, w, h);
  const left = 52 * ratio;
  const right = 16 * ratio;
  const top = 20 * ratio;
  const bottom = 30 * ratio;
  const cw = w - left - right;
  const ch = h - top - bottom;

  ctx.strokeStyle = "#dce5ef";
  ctx.lineWidth = 1 * ratio;
  [0, 0.5, 1].forEach((n) => {
    const y = top + ch * n;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(w - right, y);
    ctx.stroke();
  });

  const points = values.map((value, index) => {
    const x = left + (cw * index) / Math.max(1, values.length - 1);
    const y = top + ch - ((value - min) / (max - min)) * ch;
    return [x, y, value];
  });

  ctx.strokeStyle = color;
  ctx.lineWidth = 3 * ratio;
  ctx.beginPath();
  points.forEach(([x, y], index) => {
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = color;
  points.forEach(([x, y]) => {
    ctx.beginPath();
    ctx.arc(x, y, 6 * ratio, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = "#33415c";
  ctx.font = `${14 * ratio}px "Microsoft YaHei UI"`;
  ctx.textAlign = "center";
  points.forEach(([x, y, value], index) => {
    ctx.fillText(String(value), x, y - 13 * ratio);
    ctx.fillText(labels[index], x, h - 7 * ratio);
  });
  ctx.textAlign = "left";
  [max, Math.round((max + min) / 2), min].forEach((value, index) => {
    ctx.fillText(String(value), 10 * ratio, top + (ch * index) / 2 + 5 * ratio);
  });
}

function drawMotionChart() {
  const labels = trendLabels();
  drawLineChart(els.motionTrend, padTrendValues(state.motionTrendValues, 0), "#7546c9", 0, 100, labels);
}

function padTrendValues(values, fallback) {
  const clean = values.filter((value) => Number.isFinite(value));
  const padded = [...clean];
  while (padded.length < 7) padded.unshift(fallback);
  return padded.slice(-7);
}

function trendLabels() {
  const today = new Date();
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(today.getTime() - (6 - index) * 60 * 1000);
    return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  });
}

function drawStaticCharts() {
  const labels = trendLabels();
  drawLineChart(els.heartTrend, padTrendValues(state.heartTrendValues, 0), "#1769f4", 0, 140, labels);
  drawLineChart(els.breathTrend, padTrendValues(state.breathTrendValues, 0), "#ff2f7d", 0, 40, labels);
  drawMotionChart();
}

function tick() {
  state.phase += 2.5;
  drawWave(els.heartWave, "rgba(86, 223, 145, 1)", state.phase, false, state.serverHeartWave);
  drawWave(els.breathWave, "rgba(255, 101, 173, 1)", state.phase, true, state.serverBreathWave);
  updateMetrics();
  if (Date.now() - state.lastTrendDrawAt > 1000) {
    drawStaticCharts();
    state.lastTrendDrawAt = Date.now();
  }
  requestAnimationFrame(tick);
}

window.addEventListener("resize", drawStaticCharts);

initNav();
initRecordModal();
initCameraControls();
initYoloPage();
initTunnelUrl();
activateScreen((location.hash || "#realtime").slice(1));
updateRecentRecordState();
renderRecentPreview();
syncRecentRecordsFromServer();
drawStaticCharts();
connectWebSocket();
pollRadarSnapshot();
setInterval(pollRadarSnapshot, 250);
tick();
