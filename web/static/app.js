const logBox = document.getElementById("log-box");
const statusBar = document.getElementById("status-bar");
const runningNotice = document.getElementById("running-notice");
const runningTask = document.getElementById("running-task");
let autoScroll = true;
let logLines = [];
let streamSource = null;

// ====================  Log  ====================

function appendLog(text) {
  logLines.push(text);
  if (logLines.length > 2000) logLines = logLines.slice(-2000);
  logBox.textContent = logLines.join("\n");
  if (autoScroll) logBox.scrollTop = logBox.scrollHeight;
}

function clearLogs() {
  logLines = [];
  logBox.textContent = "";
}

function toggleAutoScroll() {
  autoScroll = !autoScroll;
  document.getElementById("autoscroll-label").textContent = autoScroll ? "开" : "关";
}

// ====================  SSE log stream  ====================

function connectLogStream() {
  if (streamSource) {
    streamSource.close();
  }
  streamSource = new EventSource("/api/logs/stream");
  streamSource.onmessage = function (e) {
    try {
      const data = JSON.parse(e.data);
      appendLog(data.line);
    } catch (_) {}
  };
  streamSource.onerror = function () {
    streamSource.close();
    streamSource = null;
    setTimeout(connectLogStream, 2000);
  };
}

// ====================  Status  ====================

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    renderStatus(s);

    if (s.running) {
      runningNotice.style.display = "flex";
      runningTask.textContent = "运行中: " + s.current_task;
      disableAll(true);
    } else {
      runningNotice.style.display = "none";
      disableAll(false);
    }
  } catch (e) {
    console.error("status error", e);
  }
}

function renderStatus(s) {
  const inputs = s.inputs;
  const ws = s.workspace;
  const out = s.outputs;

  const ok = '<span class="ok">已生成</span>';
  const warn = '<span class="warn">未生成</span>';

  statusBar.innerHTML =
    '<div class="item"><span class="label">招标文件</span><span class="value">' + (inputs.tender_md ? ok : warn) + '</span></div>' +
    '<div class="item"><span class="label">公司资料</span><span class="value">' + (inputs.company_md ? ok : warn) + '</span></div>' +
    '<div class="item"><span class="label">评分标准</span><span class="value">' + (inputs.score_md ? ok : warn) + '</span></div>' +
    '<div class="item"><span class="label">大纲</span><span class="value">' + (ws.outline ? ok : warn) + '</span></div>' +
    '<div class="item"><span class="label">章节</span><span class="value">' + ws.chapters_count + '</span></div>' +
    '<div class="item"><span class="label">审核</span><span class="value">' + ws.reviews_count + '</span></div>' +
    '<div class="item"><span class="label">摘要</span><span class="value">' + ws.summaries_count + '</span></div>' +
    '<div class="item"><span class="label">Word</span><span class="value">' + (out.final_docx ? ok : warn) + '</span></div>';
}

function disableAll(disabled) {
  document.querySelectorAll("button").forEach(b => {
    if (b.className.includes("danger")) return;
    b.disabled = disabled;
  });
}

// ====================  Command  ====================

async function runCommand(cmd) {
  try {
    const r = await fetch("/api/run-command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: cmd }),
    });
    const data = await r.json();
    if (data.ok) {
      appendLog("--- 触发命令: " + cmd + " ---");
      connectLogStream();
      disableAll(true);
      runningNotice.style.display = "flex";
      runningTask.textContent = "运行中: " + cmd;
    } else {
      alert(data.message);
    }
  } catch (e) {
    alert("请求失败: " + e);
  }
}

// ====================  Upload  ====================

async function uploadFiles(category) {
  const input = document.getElementById("upload-" + category);
  if (!input || !input.files.length) {
    alert("请先选择文件");
    return;
  }
  const form = new FormData();
  for (const f of input.files) {
    form.append("files", f);
  }
  try {
    const r = await fetch("/api/upload?category=" + category, { method: "POST", body: form });
    const data = await r.json();
    if (data.ok) {
      appendLog("上传成功: " + data.saved.join(", "));
      loadStatus();
    } else {
      alert(data.message);
    }
  } catch (e) {
    alert("上传失败: " + e);
  }
}

// ====================  Download  ====================

function downloadFinalMd() {
  window.open("/api/download/final-md", "_blank");
}

function downloadFinalDocx() {
  window.open("/api/download/final-docx", "_blank");
}

async function viewGlobalReview() {
  try {
    const r = await fetch("/api/file/global-review");
    const data = await r.json();
    if (data.ok) {
      const w = window.open("", "_blank");
      w.document.write("<pre style='font-size:12px;white-space:pre-wrap;word-break:break-all'>" +
        JSON.stringify(data.data, null, 2) + "</pre>");
    } else {
      alert(data.message);
    }
  } catch (e) {
    alert("请求失败: " + e);
  }
}

// ====================  Clean  ====================

function confirmClean() {
  if (!confirm("确认清空 workspace/ 和 outputs/ 目录？\n此操作不可恢复。")) return;
  fetch("/api/clean-workspace", { method: "POST" })
    .then(r => r.json())
    .then(data => {
      if (data.ok) appendLog(data.message);
      loadStatus();
    });
}

// ====================  Init  ====================

loadStatus();
connectLogStream();
setInterval(loadStatus, 2000);
