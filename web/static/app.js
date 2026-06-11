const logBox = document.getElementById("log-box");
const runningNotice = document.getElementById("running-notice");
const runningTask = document.getElementById("running-task");
const heroTask = document.getElementById("hero-task");
const heroNext = document.getElementById("hero-next");
const workflowSummary = document.getElementById("workflow-summary");
const workflowList = document.getElementById("workflow-list");
const sourceFilesBox = document.getElementById("source-files");

let autoScroll = true;
let logLines = [];
let streamSource = null;
let currentStatus = null;
let runningCmd = null;

const COMMAND_LABELS = {
  init: "初始化项目",
  "init-demo": "生成演示资料",
  "prepare-inputs": "导入资料",
  "split-docs": "切分文档",
  "parse-score": "解析评分",
  "extract-facts": "提取事实",
  "generate-outline": "生成大纲",
  "plan-jobs": "生成任务",
  "select-context-all": "选择上下文",
  "write-all": "生成章节",
  "review-fix-all": "审核改稿",
  "summarize-all": "生成摘要",
  "global-review": "全文审核",
  "build-md": "拼接 MD",
  "build-docx": "生成 Word",
  validate: "校验项目",
  run: "一键运行全流程",
  "graph-run": "LangGraph 全流程",
};

function countDoneSteps(workflow) {
  return workflow.filter((step) => step.done).length;
}

function getWorkflowProgress(workflow) {
  const total = workflow.length || 1;
  const done = countDoneSteps(workflow);
  return Math.round((done / total) * 100);
}

function formatRequires(items) {
  if (!items || !items.length) return "无";
  return items.join("、");
}

function formatProduces(items) {
  if (!items || !items.length) return "无";
  return items.join("、");
}

function workflowStateLabel(state) {
  if (state === "done") return "已完成";
  if (state === "ready") return "可执行";
  return "等待前置步骤";
}

function formatBytes(size) {
  if (size < 1024) return size + " B";
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
  return (size / (1024 * 1024)).toFixed(1) + " MB";
}

function renderSourceFiles() {
  if (!sourceFilesBox) return;
  const sources = currentStatus?.sources || {};
  const sections = [
    ["招标文件", sources.tender || []],
    ["公司资料", sources.company || []],
    ["模板", sources.template || []],
  ];

  sourceFilesBox.innerHTML = sections.map(([title, files]) => {
    const body = files.length
      ? files.map((file) => `<li><span>${file.name}</span><small>${formatBytes(file.size)} · ${file.modified}</small></li>`).join("")
      : "<li class='empty-source'>暂无文件</li>";
    return `
      <div class="source-group">
        <div class="source-group-title">${title}</div>
        <ul class="source-file-list">${body}</ul>
      </div>
    `;
  }).join("");
}

function buildWorkflow() {
  const workflow = currentStatus?.workflow || [];
  workflowList.innerHTML = "";
  if (!workflow.length) {
    workflowList.innerHTML = "<div class='empty-state'>暂无流程数据，请刷新状态。</div>";
    workflowSummary.innerHTML = "";
    return;
  }

  const progress = getWorkflowProgress(workflow);
  const nextStep = currentStatus?.next_step || null;
  const blockedStep = currentStatus?.blocked_step || null;
  const sourceStale = !!currentStatus?.sync?.source_stale;

  workflowSummary.innerHTML = `
    <div class="summary-card">
      <span class="summary-label">完成度</span>
      <strong>${progress}%</strong>
      <div class="progress-bar"><span style="width:${progress}%"></span></div>
    </div>
    <div class="summary-card">
      <span class="summary-label">下一步</span>
      <strong>${nextStep ? nextStep.label : "暂无"}</strong>
      <p>${nextStep ? nextStep.command : "已无可执行步骤"}</p>
    </div>
    <div class="summary-card">
      <span class="summary-label">阻塞点</span>
      <strong>${blockedStep ? blockedStep.label : "无"}</strong>
      <p>${sourceStale ? "sources/ 已更新，请先重新执行导入资料" : (blockedStep ? formatRequires(blockedStep.missing_requires) : "没有阻塞")}</p>
    </div>
  `;

  workflow.forEach((step, index) => {
    const card = document.createElement("article");
    card.className = `workflow-card state-${step.state}`;
    if (runningCmd === step.command) card.classList.add("running");

    const requires = formatRequires(step.requires);
    const produces = formatProduces(step.produces);
    const missing = step.missing_requires && step.missing_requires.length
      ? step.missing_requires.join("、")
      : "无";

    card.innerHTML = `
      <div class="workflow-card-top">
        <div class="workflow-index">${String(index + 1).padStart(2, "0")}</div>
        <div class="workflow-main">
          <div class="workflow-title-row">
            <h3>${step.label}</h3>
            <span class="status-pill">${workflowStateLabel(step.state)}</span>
          </div>
          <p class="workflow-command">命令：<code>${step.command}</code></p>
          <p class="workflow-message">${step.message}</p>
        </div>
      </div>
      <div class="workflow-meta">
        <div><span>前置条件</span><strong>${requires}</strong></div>
        <div><span>产物</span><strong>${produces}</strong></div>
        <div><span>缺少项</span><strong>${missing}</strong></div>
      </div>
      <div class="workflow-card-actions">
        <button ${step.done ? "disabled" : ""} onclick="runCommand('${step.command}')">
          ${step.done ? "已完成" : "运行这一步"}
        </button>
      </div>
    `;

    workflowList.appendChild(card);
  });
}

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

// ====================  SSE  ====================

function connectLogStream() {
  if (streamSource) streamSource.close();
  streamSource = new EventSource("/api/logs/stream");
  streamSource.onmessage = function (e) {
    try {
      appendLog(JSON.parse(e.data).line);
    } catch (_) {}
  };
  streamSource.onerror = function () {
    streamSource.close();
    streamSource = null;
    setTimeout(connectLogStream, 2000);
  };
}

// ====================  Status  ====================

function updateChrome() {
  if (!currentStatus) return;
  heroTask.textContent = currentStatus.running ? `运行中：${currentStatus.current_task}` : "空闲";
  if (currentStatus.sync?.source_stale) {
    heroNext.textContent = "导入资料（sources 已更新）";
  } else {
    heroNext.textContent = currentStatus.next_step ? currentStatus.next_step.label : "已完成全部流程";
  }

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

  renderSourceFiles();
}

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    currentStatus = await r.json();
    updateChrome();
    buildWorkflow();
  } catch (e) {
    console.error("status error", e);
  }
}

function refreshStatus() {
  loadStatus();
}

function disableAll(disabled) {
  document.querySelectorAll("button").forEach((b) => {
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
      appendLog("--- 触发: " + cmd + " ---");
      connectLogStream();
    } else {
      alert(data.message);
    }
  } catch (e) {
    alert("请求失败: " + e);
  }
}

async function runFullPipeline() {
  runCommand("run");
}

async function runGraphRun() {
  runCommand("graph-run");
}

async function runRecommendedStep() {
  if (!currentStatus || !currentStatus.next_step) {
    alert("当前没有可执行的下一步，请先刷新状态。");
    return;
  }
  runCommand(currentStatus.next_step.command);
}

// ====================  Upload  ====================

async function uploadFiles(category) {
  const input = document.getElementById("upload-" + category);
  if (!input || !input.files.length) {
    alert("请先选择文件");
    return;
  }
  const form = new FormData();
  for (const f of input.files) form.append("files", f);
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
      w.document.write("<pre style='font-size:12px;white-space:pre-wrap;word-break:break-all'>" + JSON.stringify(data.data, null, 2) + "</pre>");
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
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) appendLog(data.message);
      loadStatus();
    });
}

// ====================  Init  ====================

buildWorkflow();
loadStatus();
connectLogStream();
setInterval(loadStatus, 2000);
