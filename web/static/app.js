const logBox = document.getElementById("log-box");
const runningNotice = document.getElementById("running-notice");
const runningTask = document.getElementById("running-task");
let autoScroll = true;
let logLines = [];
let streamSource = null;

const PIPELINE = [
  { cmd: "init", label: "初始化" },
  { cmd: "prepare-inputs", label: "导入资料" },
  { cmd: "split-docs", label: "切分文档" },
  { cmd: "parse-score", label: "解析评分" },
  { cmd: "extract-facts", label: "提取事实" },
  { cmd: "generate-outline", label: "生成大纲" },
  { cmd: "plan-jobs", label: "生成任务" },
  { cmd: "select-context-all", label: "选择上下文" },
  { cmd: "write-all", label: "生成章节" },
  { cmd: "review-fix-all", label: "审核改稿" },
  { cmd: "summarize-all", label: "生成摘要" },
  { cmd: "global-review", label: "全文审核" },
  { cmd: "build-md", label: "拼接MD" },
  { cmd: "build-docx", label: "生成Word" },
];

const CMD_DONE_MAP = {
  "prepare-inputs": ["inputs.tender_md", "inputs.score_md", "inputs.company_md"],
  "split-docs": ["workspace.tender_chunks", "workspace.company_chunks"],
  "parse-score": ["workspace.score_points"],
  "extract-facts": ["workspace.global_facts"],
  "generate-outline": ["workspace.outline"],
  "plan-jobs": ["workspace.jobs_count"],
  "select-context-all": ["workspace.contexts_count"],
  "write-all": ["workspace.chapters_count"],
  "review-fix-all": ["workspace.reviews_count"],
  "summarize-all": ["workspace.summaries_count"],
  "global-review": ["workspace.global_review"],
  "build-md": ["outputs.final_md"],
  "build-docx": ["outputs.final_docx"],
};

let currentStatus = null;
let runningCmd = null;

// ====================  Flow  ====================

function buildFlow() {
  const container = document.getElementById("flow-container");
  container.innerHTML = "";
  PIPELINE.forEach((step, i) => {
    const div = document.createElement("div");
    div.className = "flow-step";
    div.id = "step-" + step.cmd;
    div.onclick = () => runCommand(step.cmd);

    const node = document.createElement("div");
    node.className = "node";
    node.textContent = i + 1;

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = step.label;

    const arrow = document.createElement("div");
    arrow.className = "arrow";

    div.appendChild(node);
    div.appendChild(label);
    div.appendChild(arrow);
    container.appendChild(div);
  });
}

function updateFlowStatus() {
  if (!currentStatus) return;
  const s = currentStatus;

  PIPELINE.forEach((step, i) => {
    const el = document.getElementById("step-" + step.cmd);
    if (!el) return;

    el.className = "flow-step";

    if (runningCmd === step.cmd) {
      el.classList.add("running");
      return;
    }

    const keys = CMD_DONE_MAP[step.cmd];
    if (keys && keys.every(k => {
      const parts = k.split(".");
      if (parts[0] === "inputs") return s.inputs[parts[1]];
      if (parts[0] === "workspace") return s.workspace[parts[1]];
      if (parts[0] === "outputs") return s.outputs[parts[1]];
      return false;
    })) {
      el.classList.add("done");
    }
  });
}

// ====================  Log  ====================

function appendLog(text) {
  logLines.push(text);
  if (logLines.length > 2000) logLines = logLines.slice(-2000);
  logBox.textContent = logLines.join("\n");
  if (autoScroll) logBox.scrollTop = logBox.scrollHeight;
}

function clearLogs() { logLines = []; logBox.textContent = ""; }
function toggleAutoScroll() { autoScroll = !autoScroll; document.getElementById("autoscroll-label").textContent = autoScroll ? "开" : "关"; }

// ====================  SSE  ====================

function connectLogStream() {
  if (streamSource) streamSource.close();
  streamSource = new EventSource("/api/logs/stream");
  streamSource.onmessage = function (e) {
    try { appendLog(JSON.parse(e.data).line); } catch (_) {}
  };
  streamSource.onerror = function () { streamSource.close(); streamSource = null; setTimeout(connectLogStream, 2000); };
}

// ====================  Status  ====================

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    currentStatus = await r.json();
    updateFlowStatus();

    if (currentStatus.running) {
      runningCmd = currentStatus.current_task;
      runningNotice.style.display = "flex";
      runningTask.textContent = "运行中: " + currentStatus.current_task;
      disableAll(true);
    } else {
      runningCmd = null;
      runningNotice.style.display = "none";
      disableAll(false);
    }
  } catch (e) { console.error("status error", e); }
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
    const r = await fetch("/api/run-command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command: cmd }) });
    const data = await r.json();
    if (data.ok) {
      appendLog("--- 触发: " + cmd + " ---");
      connectLogStream();
    } else { alert(data.message); }
  } catch (e) { alert("请求失败: " + e); }
}

async function runFullPipeline() { runCommand("run"); }
async function runGraphRun() { runCommand("graph-run"); }

// ====================  Upload  ====================

async function uploadFiles(category) {
  const input = document.getElementById("upload-" + category);
  if (!input || !input.files.length) { alert("请先选择文件"); return; }
  const form = new FormData();
  for (const f of input.files) form.append("files", f);
  try {
    const r = await fetch("/api/upload?category=" + category, { method: "POST", body: form });
    const data = await r.json();
    if (data.ok) { appendLog("上传成功: " + data.saved.join(", ")); loadStatus(); }
    else { alert(data.message); }
  } catch (e) { alert("上传失败: " + e); }
}

// ====================  Download  ====================

function downloadFinalMd() { window.open("/api/download/final-md", "_blank"); }
function downloadFinalDocx() { window.open("/api/download/final-docx", "_blank"); }

async function viewGlobalReview() {
  try {
    const r = await fetch("/api/file/global-review");
    const data = await r.json();
    if (data.ok) {
      const w = window.open("", "_blank");
      w.document.write("<pre style='font-size:12px;white-space:pre-wrap;word-break:break-all'>" + JSON.stringify(data.data, null, 2) + "</pre>");
    } else { alert(data.message); }
  } catch (e) { alert("请求失败: " + e); }
}

// ====================  Clean  ====================

function confirmClean() {
  if (!confirm("确认清空 workspace/ 和 outputs/ 目录？\n此操作不可恢复。")) return;
  fetch("/api/clean-workspace", { method: "POST" })
    .then(r => r.json())
    .then(data => { if (data.ok) appendLog(data.message); loadStatus(); });
}

// ====================  Init  ====================

buildFlow();
loadStatus();
connectLogStream();
setInterval(loadStatus, 2000);
