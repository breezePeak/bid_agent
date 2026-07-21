const logBox = document.getElementById("log-box");
const logBand = document.getElementById("log-band");
const runningNotice = document.getElementById("running-notice");
const runningTask = document.getElementById("running-task");
const heroTask = document.getElementById("hero-task");
const heroDot = document.getElementById("run-state-dot");
const workflowList = document.getElementById("workflow-list");
const stepDetailPanel = document.getElementById("step-detail-panel");
const artifactPreviewPanel = document.getElementById("artifact-preview-panel");
const sourceFilesBox = document.getElementById("source-files");
const progressCaption = document.getElementById("progress-caption");
const progressPercent = document.getElementById("progress-percent");
const progressFill = document.getElementById("progress-fill");
const currentStage = document.getElementById("current-stage");
const workspaceGate = document.getElementById("workspace-gate");
const sourceBand = document.getElementById("source-band");
const mainWorkbench = document.getElementById("main-workbench");
const workspaceSelect = document.getElementById("workspace-select");
const workspaceMeta = document.getElementById("workspace-meta");
const runNameInput = document.getElementById("run-name");
const projectTypeSelect = document.getElementById("project-type-select");
const createWorkspaceButton = document.getElementById("btn-create-workspace");
const startButton = document.getElementById("btn-start");
const pauseButton = document.getElementById("btn-pause");
const projectProfilePanel = document.getElementById("project-profile-panel");
const agentRunsPanel = document.getElementById("agent-runs-panel");
const manualReviewPanel = document.getElementById("manual-review-panel");
const manualReviewItemsPanel = document.getElementById("manual-review-items");
const manualReviewCategory = document.getElementById("manual-review-category");
const chatThread = document.getElementById("chat-thread");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");

let autoScroll = true;
let logLines = [];
let streamSource = null;
let currentStatus = null;
let runsLoadInFlight = false;
let lastRunsLoadedAt = 0;
let autoRunActive = false;
let autoRunId = "";
let autoRunIndex = 0;
let autoLastCommand = "";
let autoLastStartedAt = 0;
let workspaceEntered = false;
let selectedStepCommand = "";
let stepDetailInFlight = false;
let logDockExpanded = false;
let chatMessages = [];
let finalMdLinesCache = [];
let pendingLineEdit = null;
const RUN_STARTED_KEY = "bidAgentCurrentRunStarted";
const AUTO_RUN_KEY = "bidAgentAutoRunActive";
const AUTO_RUN_ID_KEY = "bidAgentAutoRunId";
const AUTO_INDEX_KEY = "bidAgentAutoRunIndex";
const AUTO_LAST_COMMAND_KEY = "bidAgentAutoRunLastCommand";
const AUTO_LAST_STARTED_AT_KEY = "bidAgentAutoRunLastStartedAt";
const CHAT_HISTORY_KEY = "bidAgentChatHistory";

// 流程定义以后端 /api/status.workflow 为准，前端不再硬编码阶段列表。
const EXTRA_COMMAND_LABELS = {
  "init-demo": "生成演示资料",
  "analyze-template": "解析模板结构",
  validate: "校验项目",
  run: "自动执行全流程",
  "graph-run": "自动执行全流程",
};

function coreWorkflow(workflow) {
  return (Array.isArray(workflow) ? workflow : []).filter((step) => step && step.kind !== "utility" && step.command);
}

function autoRunCommands() {
  return coreWorkflow(currentStatus?.workflow || []).map((step) => step.command);
}

function commandLabel(command) {
  const step = (currentStatus?.workflow || []).find((item) => item.command === command);
  if (step?.label) return step.label;
  return EXTRA_COMMAND_LABELS[command] || command;
}

function stageToCommand(stage) {
  if (!stage) return "";
  const hit = (currentStatus?.workflow || []).find((step) => step.id === stage || step.command === stage);
  if (hit?.command) return hit.command;
  return String(stage).replaceAll("_", "-");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(size) {
  if (!Number.isFinite(size)) return "0 B";
  if (size < 1024) return size + " B";
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
  return (size / (1024 * 1024)).toFixed(1) + " MB";
}

function formatRunOption(run) {
  const progress = run.progress || {};
  const done = Number.isFinite(progress.done) ? progress.done : 0;
  const total = Number.isFinite(progress.total) ? progress.total : 0;
  const label = progress.status_label || progress.status || "未知状态";
  return `${run.id} · ${done}/${total} · ${label}`;
}

function formatValue(value) {
  if (Array.isArray(value)) {
    if (!value.length) return "无";
    return value.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value && typeof value === "object") return escapeHtml(JSON.stringify(value));
  if (value === "" || value === null || value === undefined) return "无";
  return escapeHtml(value);
}

function renderArtifactList(title, items) {
  const rows = (items || []).map((item) => {
    const state = item.exists ? "已生成" : "未生成";
    const meta = item.type === "glob"
      ? `${item.count || 0} 个 · ${formatBytes(item.size || 0)}`
      : item.type === "virtual"
        ? "内置产物"
        : `${formatBytes(item.size || 0)}`;
    const samples = item.samples?.length ? `<small>${escapeHtml(item.samples.join("、"))}</small>` : "";
    const canPreview = item.exists && item.previewable !== false && item.type !== "virtual";
    const pathHtml = canPreview
      ? `<button class="artifact-link" onclick="event.stopPropagation();previewArtifact('${escapeHtml(item.path)}')">${escapeHtml(item.path)}</button>`
      : `<span>${escapeHtml(item.path)}</span>`;
    return `
      <li class="${item.exists ? "ok" : "missing"}">
        ${pathHtml}
        <strong>${state}</strong>
        <em>${escapeHtml(meta)}${item.modified ? ` · ${escapeHtml(item.modified)}` : ""}</em>
        ${samples}
      </li>
    `;
  }).join("");
  return `
    <section class="detail-block">
      <h4>${escapeHtml(title)}</h4>
      <ul class="artifact-list">${rows || "<li class='missing'><span>无</span><strong>无</strong><em></em></li>"}</ul>
    </section>
  `;
}

function renderReviewRows(rows) {
  if (!rows?.length) return "";
  const items = rows.map((row) => {
    const state = row.need_rewrite ? "仍需处理" : row.rewritten ? "已重写通过" : "通过";
    const problems = row.problems?.length
      ? row.problems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
      : "<li>无明确问题</li>";
    const weak = row.weak_coverage?.length ? `弱覆盖：${escapeHtml(row.weak_coverage.join("、"))}` : "";
    const rewriteLink = row.rewrite_path
      ? `<button class="artifact-link" onclick="event.stopPropagation();previewArtifact('${escapeHtml(row.rewrite_path)}')">重写日志</button>`
      : "<span>未重写</span>";
    return `
      <article class="review-row ${row.need_rewrite ? "needs-work" : ""}">
        <div class="review-row-head">
          <div>
            <strong>${escapeHtml(row.chapter_id)} ${escapeHtml(row.chapter_title)}</strong>
            <span>${escapeHtml(state)} · 问题 ${row.problem_count || 0} 个${weak ? ` · ${weak}` : ""}</span>
          </div>
          <div class="review-links">
            <button class="artifact-link" onclick="event.stopPropagation();previewArtifact('${escapeHtml(row.review_path)}')">审核JSON</button>
            ${rewriteLink}
          </div>
        </div>
        <ul>${problems}</ul>
      </article>
    `;
  }).join("");
  return `
    <section class="detail-block review-detail-block">
      <h4>章节审核明细</h4>
      <div class="review-list">${items}</div>
    </section>
  `;
}

function renderScoreRows(scorePoints, scoreRequirements) {
  if (!scorePoints?.length && !scoreRequirements?.length) return "";
  const pointRows = (scorePoints || []).map((item) => `
    <article class="score-row">
      <div class="score-row-head">
        <strong>${escapeHtml(item.id || "")} ${escapeHtml(item.title || "")}</strong>
        <span>${escapeHtml(item.score ?? "未给分")}</span>
      </div>
      <div class="score-row-meta">${escapeHtml(item.category || "未分类")}</div>
      <div class="score-row-text">${escapeHtml(item.requirement || "")}</div>
      <div class="score-row-text muted">${escapeHtml(item.response_strategy || "")}</div>
    </article>
  `).join("");
  const requirementRows = (scoreRequirements || []).slice(0, 20).map((item) => `
    <article class="score-row compact">
      <div class="score-row-head">
        <strong>${escapeHtml(item.title || item.category || "原始评分要求")}</strong>
        <span>${escapeHtml(item.score ?? "未给分")}</span>
      </div>
      <div class="score-row-text">${escapeHtml(item.requirement || item.scoring_criteria || "")}</div>
    </article>
  `).join("");
  return `
    <section class="detail-block">
      <h4>评分点明细</h4>
      <div class="score-list">${pointRows || "<div class='detail-empty small'>暂无评分点明细。</div>"}</div>
    </section>
    <section class="detail-block">
      <h4>原始评分要求</h4>
      <div class="score-list">${requirementRows || "<div class='detail-empty small'>暂无原始评分要求。</div>"}</div>
    </section>
  `;
}

function renderProjectProfile() {
  if (!projectProfilePanel) return;
  const profile = currentStatus?.project_profile || {};
  const choices = currentStatus?.project_profile_choices || [];
  if (projectTypeSelect) {
    projectTypeSelect.innerHTML = choices.map((item) => `
      <option value="${escapeHtml(item.project_type)}">${escapeHtml(item.label)}</option>
    `).join("");
    projectTypeSelect.value = profile.project_type || "general";
  }
  const metrics = [
    ["项目类型", profile.label || profile.project_type || "general"],
    ["编码", profile.project_type || "general"],
    ["说明", profile.description || "默认通用 prompt 策略"],
    ["更新时间", profile.updated_at || "未记录"],
  ];
  projectProfilePanel.innerHTML = metrics.map(([label, value]) => `
    <div class="mini-kv">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
}

function renderLatestAgentRuns() {
  if (!agentRunsPanel) return;
  const rows = currentStatus?.latest_agent_runs || [];
  if (!rows.length) {
    agentRunsPanel.innerHTML = "<div class='detail-empty small'>暂无 agent run 记录。</div>";
    return;
  }
  agentRunsPanel.innerHTML = rows.map((run) => `
    <article class="agent-run-card">
      <div class="agent-run-head">
        <strong>${escapeHtml(run.agent_name || "")}</strong>
        <span>${escapeHtml(run.chapter_id || run.stage || "")}</span>
      </div>
      <div class="agent-run-meta">
        <span>${escapeHtml(run.prompt_file || "")}</span>
        <span>v${escapeHtml(run.prompt_version || "")}</span>
        <span>${escapeHtml((run.prompt_checksum || "").slice(0, 10))}</span>
      </div>
      <div class="agent-run-meta">
        <span>${escapeHtml(run.model || "")}</span>
        <span>temp ${escapeHtml(run.temperature)}</span>
        <span>${escapeHtml(run.project_type || "general")}</span>
      </div>
    </article>
  `).join("");
}

function renderManualReviewSummary() {
  if (!manualReviewPanel) return;
  const summary = currentStatus?.manual_review_summary || {};
  const metrics = [
    ["待处理总数", summary.total_pending ?? 0],
    ["弱证据/缺口", summary.template_evidence_pending ?? 0],
    ["评分点覆盖", summary.score_coverage_pending ?? 0],
    ["章节问题", summary.chapter_review_pending ?? 0],
    ["全文风险", summary.global_review_pending ?? 0],
  ];
  const replay = (summary.latest_replay_requests || []).map((item) =>
    `<li>${escapeHtml(item.category || "")} / ${escapeHtml(item.item_id || "")} -> ${escapeHtml(item.recommended_stage || "")}</li>`
  ).join("");
  manualReviewPanel.innerHTML = `
    <div class="mini-grid">
      ${metrics.map(([label, value]) => `
        <div class="mini-kv"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>
      `).join("")}
    </div>
    <div class="inline-note">最近重跑建议</div>
    <ul class="compact-list">${replay || "<li>暂无</li>"}</ul>
  `;
}

function renderManualReviewItems(items) {
  if (!manualReviewItemsPanel) return;
  if (!items?.length) {
    manualReviewItemsPanel.innerHTML = "<div class='detail-empty small'>当前分类暂无待处理项。</div>";
    return;
  }
  manualReviewItemsPanel.innerHTML = items.map((item) => {
    const title = item.title || item.description || item.score_point_id || item.item_id;
    const override = item.override || {};
    const currentStatusText = override.status || item.status || "pending";
    return `
      <article class="manual-item">
        <div class="manual-item-head">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(currentStatusText)}</span>
        </div>
        <div class="manual-item-body">
          <div class="manual-item-text">${escapeHtml(JSON.stringify(item).slice(0, 360))}</div>
          <textarea id="manual-note-${escapeHtml(item.item_id)}" placeholder="填写人工说明或修订指令">${escapeHtml(override.operator_instruction || override.operator_note || "")}</textarea>
          <div class="manual-item-actions">
            <button onclick="submitManualReview('${escapeHtml(item.category)}', '${escapeHtml(item.item_id)}', 'accepted')">接受/确认</button>
            <button onclick="submitManualReview('${escapeHtml(item.category)}', '${escapeHtml(item.item_id)}', 'resolved')">标记已处理</button>
            <button onclick="submitManualReview('${escapeHtml(item.category)}', '${escapeHtml(item.item_id)}', 'dismissed')">忽略</button>
          </div>
        </div>
      </article>
    `;
  }).join("");
}

function loadChatHistory() {
  try {
    const raw = sessionStorage.getItem(CHAT_HISTORY_KEY);
    chatMessages = raw ? JSON.parse(raw) : [];
  } catch (_) {
    chatMessages = [];
  }
  if (!chatMessages.length) {
    chatMessages = [
      {
        role: "assistant",
        text: "我可以帮你解释当前流程状态、打开节点详情、查看人工复核问题，也可以按你关心的意图回答：评分、风险、输出、资料、卡住原因。",
        actions: [
          { type: "chat_prompt", prompt: "当前状态怎么样", label: "当前状态" },
          { type: "chat_prompt", prompt: "继续执行下一步", label: "继续执行" },
          { type: "chat_prompt", prompt: "我关心评分覆盖情况", label: "评分覆盖" },
          { type: "chat_prompt", prompt: "我关心质量风险", label: "质量风险" },
        ],
      },
    ];
  }
}

function persistChatHistory() {
  sessionStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(chatMessages.slice(-30)));
}

function renderChatAction(action) {
  if (!action || !action.label) return "";
  const type = escapeHtml(action.type || "");
  const command = escapeHtml(action.command || action.params?.command || "");
  const category = escapeHtml(action.category || action.params?.category || "");
  const tool = escapeHtml(action.tool || "");
  const prompt = escapeHtml(action.prompt || action.label || "");
  if (action.type === "chat_prompt") {
    return `<button onclick="sendQuickChat('${prompt}')">${escapeHtml(action.label)}</button>`;
  }
  // Encode tool/command so confirm_tool and run_command both work
  return `<button onclick="handleChatAction('${type}', '${command}', '${category}', '${tool}', '${prompt}')">${escapeHtml(action.label)}</button>`;
}

function renderChatMessages() {
  if (!chatThread) return;
  chatThread.innerHTML = chatMessages.map((item) => `
    <article class="chat-message ${item.role === "user" ? "is-user" : "is-assistant"}">
      <div class="chat-bubble">${escapeHtml(item.text || "")}</div>
      ${(item.actions || []).length ? `<div class="chat-actions">${item.actions.map(renderChatAction).join("")}</div>` : ""}
    </article>
  `).join("");
  chatThread.scrollTop = chatThread.scrollHeight;
}

function pushChatMessage(role, text, actions = []) {
  chatMessages.push({ role, text, actions });
  persistChatHistory();
  renderChatMessages();
}

async function handleChatAction(type, command, category, tool = "", prompt = "") {
  if (type === "confirm_tool" || (type === "run_command" && tool)) {
    const text = prompt || (command ? `确认执行 ${command}` : "确认执行");
    chatInput.value = text;
    // Mark confirmation for orchestrate payload via global flag
    window.__pendingChatAction = {
      type: "confirm_tool",
      tool: tool || "",
      command: command || "",
      user_confirmed: true,
    };
    await submitChat();
    return;
  }
  if (type === "run_command" && command) {
    await runCommand(command);
    return;
  }
  if ((type === "show_step" || type === "open_detail" || type === "revalidate_gate" || type === "rerun_stage" || type === "retry_stage") && command) {
    if (type === "rerun_stage" || type === "retry_stage" || type === "revalidate_gate") {
      await runCommand(command);
      return;
    }
    await showStepDetail(command);
    return;
  }
  if (type === "show_manual_review" && category) {
    if (manualReviewCategory) manualReviewCategory.value = category;
    await loadManualReviewItems(category);
    pushChatMessage("assistant", `已切到人工复核分类：${category}。`);
    return;
  }
  if (type === "auto_run") {
    await resumeAutoRun();
    return;
  }
  if (type === "dispatch_chapters") {
    await runCommand("write-all");
    return;
  }
  if (type === "dispatch_review") {
    await runCommand("review-fix-all");
    return;
  }
  if (type === "global_review") {
    await runCommand("global-review");
    return;
  }
  if (type === "upload_materials" || type === "upload_evidence") {
    pushChatMessage("assistant", "请在左侧上传公司资料或模板后发送「继续」。");
    return;
  }
  if (type === "chat_prompt" || prompt) {
    sendQuickChat(prompt || command || "");
    return;
  }
  pushChatMessage("assistant", `未处理的操作：${type || "unknown"}`);
}

async function submitChat() {
  const message = (chatInput?.value || "").trim();
  if (!message) return;
  pushChatMessage("user", message);
  if (chatInput) chatInput.value = "";
  if (chatSend) chatSend.disabled = true;
  try {
    const docChatMatch = message.match(/^(整份|全文|整个文档|整份文档|全文文档|改写文档|把文档|对文档|对整份)\s*[:：]?\s*([\s\S]+)/);
    if (docChatMatch) {
      const instruction = docChatMatch[2].trim();
      if (!instruction) {
        pushChatMessage("assistant", "请补充改写要求，例如：整份：把第三章改成更正式的语气。", []);
        return;
      }
      switchDetailTab("doc");
      appendLog("[WYSIWYG] 通过聊天发起 AI 全文改写：" + instruction);
      const response = await fetch("/api/final-doc/chat-edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction }),
      });
      const data = await response.json();
      if (!data.ok) {
        pushChatMessage("assistant", data.message || "AI 全文改写失败。", []);
        return;
      }
      pendingDocEdit = {
        kind: "chat_edit",
        instruction,
        new_md: data.new_md || "",
        source: "ai_chat_edit",
      };
      docChatEditState = { instruction, preview: data.new_md || "", loading: false };
      await loadDocEditor();
      showDocChatEditModal(docChatEditState);
      renderDocPendingBar();
      pushChatMessage("assistant", "AI 已生成全文改写预览，请在中间弹窗内确认或放弃。", []);
      return;
    }
    const editMatch = message.match(/引用文档第(\d+)行[：:]([\s\S]*?)\n修改意见[：:]([\s\S]*)/);
    const unboundEditMatch = message.match(/引用文档内容[：:]([\s\S]*?)\n修改意见[：:]([\s\S]*)/);
    if (editMatch) {
      const lineNumber = Number(editMatch[1]);
      const instruction = editMatch[3].trim();
      if (!instruction) {
        pushChatMessage("assistant", "请在“修改意见：”后填写你的修改要求。可例如：更正式、补充评分点响应、减少空话。", []);
        return;
      }
      const response = await fetch("/api/final-md/line-regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line_number: lineNumber, instruction }),
      });
      const data = await response.json();
      if (!data.ok) {
        pushChatMessage("assistant", data.message || "AI 修改失败。", []);
        return;
      }
      pendingLineEdit = {
        line_number: data.line_number || lineNumber,
        old_text: data.old_text || "",
        new_text: data.generated_text || "",
        instruction,
      };
      pushChatMessage("assistant", `AI 已按你的意见生成预览（第 ${pendingLineEdit.line_number} 行）。请回到中间文档查看，确认后再保存并重建 Word。`, []);
      showPendingLinePreview();
      return;
    }
    if (unboundEditMatch) {
      pushChatMessage("assistant", "这段内容没有匹配到 final.md 的具体行号，暂时不能自动回写。请改为点击文档中的普通段落，或在 final.md 行里选择对应内容后再提交修改意见。", []);
      return;
    }
    const pendingAction = window.__pendingChatAction || null;
    window.__pendingChatAction = null;
    const payload = { message, selected_command: selectedStepCommand || "" };
    if (pendingAction && typeof pendingAction === "object") {
      payload.action = pendingAction;
    }
    // Prefer orchestrate so confirm_tool / agent actions actually execute
    let response = await fetch("/api/chat/orchestrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (response.status === 404) {
      response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    const data = await response.json();
    if (!data.ok) {
      pushChatMessage("assistant", data.message || data.reply || "聊天请求失败。");
      return;
    }
    const reply = data.reply || data.assistant?.content || data.assistant || data.message || "";
    const actions = data.actions || data.assistant?.actions || [];
    pushChatMessage("assistant", typeof reply === "string" ? reply : String(reply || ""), actions);
    if (data.triggered_command || data.triggered_auto_run) {
      refreshStatus();
    }
  } catch (error) {
    pushChatMessage("assistant", `聊天请求失败：${error}`);
  } finally {
    if (chatSend) chatSend.disabled = false;
  }
}

function sendQuickChat(prompt) {
  if (!chatInput) return;
  chatInput.value = prompt;
  submitChat();
}

function renderFinalMdEditorShell(command) {
  if (command !== "build-docx") return "";
  return `
    <section class="detail-block final-editor-block">
      <div id="inline-docx-preview" class="inline-docx-preview">
        <div class="detail-empty small">正在加载 Word 预览...</div>
      </div>
    </section>
  `;
}

function findFinalMdLineNumber(text) {
  const normalized = String(text || "").trim();
  if (!normalized) return "";
  const exact = finalMdLinesCache.find((line) => String(line.text || "").trim() === normalized);
  if (exact) return String(exact.number);
  const partial = finalMdLinesCache.find((line) => {
    const candidate = String(line.text || "").trim();
    return candidate && (candidate.includes(normalized) || normalized.includes(candidate));
  });
  return partial ? String(partial.number) : "";
}

function quoteDocxTextToChat(text, lineNumber = "") {
  if (!chatInput) return;
  const reference = lineNumber ? `引用文档第${lineNumber}行` : "引用文档内容";
  chatInput.value = `${reference}：${text}\n修改意见：`;
  chatInput.focus();
}

function quoteDocxElementToChat(element) {
  quoteDocxTextToChat(element?.dataset?.quoteText || "", element?.dataset?.lineNumber || "");
}

function renderFinalMdLineView(highlightLine, overrideText) {
  const box = document.getElementById("inline-docx-preview");
  if (!box) return;
  if (!finalMdLinesCache.length) {
    box.innerHTML = "<div class='detail-empty small'>final.md 内容尚未加载。</div>";
    return;
  }
  const rows = finalMdLinesCache.map((line) => {
    const isPending = highlightLine && Number(line.number) === Number(highlightLine);
    const text = isPending ? (overrideText || "") : (line.text || "");
    const className = `md-line-view ${isPending ? "is-pending" : ""}`;
    const id = `md-line-${line.number}`;
    return `<p id="${id}" class="${className}"><span class="md-line-number">${line.number}</span><strong>${escapeHtml(text)}</strong></p>`;
  }).join("");
  box.innerHTML = `<div class="md-line-list">${rows}</div>`;
  const target = highlightLine ? document.getElementById(`md-line-${highlightLine}`) : null;
  if (target) target.scrollIntoView({ block: "center", behavior: "smooth" });
}

function showPendingLinePreview() {
  if (!pendingLineEdit) return;
  renderFinalMdLineView(pendingLineEdit.line_number, pendingLineEdit.new_text);
  const confirmBar = document.getElementById("pending-confirm-bar");
  if (confirmBar) confirmBar.remove();
  const bar = document.createElement("div");
  bar.id = "pending-confirm-bar";
  bar.className = "pending-confirm-bar";
  bar.innerHTML = `
    <div class="pending-confirm-text">第 ${pendingLineEdit.line_number} 行已生成预览，背景变色的就是修改后的行。</div>
    <div class="pending-confirm-actions">
      <button class="primary" onclick="confirmPendingLineEdit()">确认保存并重建 Word</button>
      <button class="danger" onclick="discardPendingLineEdit()">放弃</button>
    </div>
  `;
  const box = document.getElementById("inline-docx-preview");
  (box?.parentElement || stepDetailPanel)?.appendChild(bar);
}

async function confirmPendingLineEdit() {
  if (!pendingLineEdit) return;
  if (RUNNING ?? currentStatus?.global_running) {
    alert("当前已有任务正在运行，请稍后再确认。");
    return;
  }
  try {
    const response = await fetch("/api/final-md/line-confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_text: pendingLineEdit.new_text }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "确认失败。");
      return;
    }
    pushChatMessage("assistant", `已保存第 ${pendingLineEdit.line_number} 行的修改，并开始重新生成 Word。`, []);
    pendingLineEdit = null;
    document.getElementById("pending-confirm-bar")?.remove();
    appendLog("[AI改写] 用户已确认保存，开始重新生成 Word。");
    setTimeout(loadStatus, 800);
    setTimeout(loadInlineDocxPreview, 1800);
  } catch (error) {
    alert("确认失败: " + error);
  }
}

async function discardPendingLineEdit() {
  try {
    await fetch("/api/final-md/line-discard", { method: "POST" });
  } catch (_) {}
  pendingLineEdit = null;
  document.getElementById("pending-confirm-bar")?.remove();
  loadInlineDocxPreview();
  pushChatMessage("assistant", "已放弃这次预览修改，文档保持不变。", []);
}

function renderDocxBlocks(data, selectable = false) {
  return (data.blocks || []).map((block) => {
    if (block.type === "table") {
      const rows = (block.rows || []).map((row) => `
        <tr>${(row || []).map((cell) => {
          const lineNumber = findFinalMdLineNumber(cell || "");
          const attrs = selectable ? ` role="button" tabindex="0" data-quote-text="${escapeHtml(cell || "")}" data-line-number="${escapeHtml(lineNumber)}" onclick="quoteDocxElementToChat(this)"` : "";
          return `<td class="${selectable ? "docx-selectable" : ""}"${attrs}>${escapeHtml(cell || "")}</td>`;
        }).join("")}</tr>
      `).join("");
      return `<section class="docx-block"><h4>表格 ${escapeHtml(block.index || "")}</h4><table>${rows}</table></section>`;
    }
    const lineNumber = findFinalMdLineNumber(block.text || "");
    const attrs = selectable ? ` role="button" tabindex="0" data-quote-text="${escapeHtml(block.text || "")}" data-line-number="${escapeHtml(lineNumber)}" onclick="quoteDocxElementToChat(this)"` : "";
    return `<p class="docx-paragraph ${selectable ? "docx-selectable" : ""}"${attrs}>${escapeHtml(block.text || "")}</p>`;
  }).join("");
}

async function loadInlineDocxPreview() {
  const box = document.getElementById("inline-docx-preview");
  if (!box) return;
  if (pendingLineEdit) {
    await loadInlineDocxPreviewRaw(true);
    showPendingLinePreview();
    return;
  }
  document.getElementById("pending-confirm-bar")?.remove();
  await loadInlineDocxPreviewRaw(false);
}

async function loadInlineDocxPreviewRaw() {
  const box = document.getElementById("inline-docx-preview");
  if (!box) return;
  box.innerHTML = "<div class='detail-empty small'>正在加载 Word 预览...</div>";
  try {
    const [docxResponse, linesResponse] = await Promise.all([
      fetch(`/api/file-preview?path=${encodeURIComponent("outputs/final.docx")}`),
      fetch("/api/final-md/lines"),
    ]);
    const linesData = await linesResponse.json().catch(() => ({ lines: [] }));
    finalMdLinesCache = linesData.lines || [];
    const response = docxResponse;
    const data = await response.json();
    if (!data.ok) {
      box.innerHTML = `<div class="detail-empty small">${escapeHtml(data.message || "Word 预览失败")}</div>`;
      return;
    }
    if (data.kind !== "docx") {
      box.innerHTML = "<div class='detail-empty small'>当前文件不是可预览 Word 文档。</div>";
      return;
    }
    box.innerHTML = `
      <div class="docx-preview inline">${renderDocxBlocks(data, true) || "<div class='detail-empty small'>Word 文档没有可抽取文本。</div>"}</div>
    `;
  } catch (error) {
    box.innerHTML = `<div class="detail-empty small">Word 预览失败：${escapeHtml(error)}</div>`;
  }
}

async function loadFinalMdEditor() {
  const box = document.getElementById("final-md-editor");
  if (!box) return;
  box.innerHTML = "<div class='detail-empty small'>正在加载 final.md 内容...</div>";
  try {
    const response = await fetch("/api/final-md/lines");
    const data = await response.json();
    if (!data.ok) {
      box.innerHTML = `<div class="detail-empty small">${escapeHtml(data.message || "final.md 加载失败")}</div>`;
      return;
    }
    const rows = (data.lines || []).map((line) => `
      <button class="final-line" data-line-number="${line.number}" data-line-text="${escapeHtml(line.text || "")}" onclick="selectFinalMdLine(this)">
        <span>${line.number}</span>
        <strong>${escapeHtml(line.text || " ")}</strong>
      </button>
    `).join("");
    box.innerHTML = `
      <div class="final-editor-grid">
        <div class="final-line-list">${rows || "<div class='detail-empty small'>final.md 为空。</div>"}</div>
        <div class="final-line-edit">
          <div class="mini-kv"><span>选中行</span><strong id="final-selected-line">未选择</strong></div>
          <label>修改指令</label>
          <textarea id="final-edit-instruction" placeholder="输入用户生成要求，例如：让这一行更具体，补充评分点响应，语气更正式"></textarea>
          <label>修改后内容</label>
          <textarea id="final-edit-text" placeholder="先在左侧选择一行，再修改这里的内容"></textarea>
          <div class="final-edit-actions">
            <button class="primary" onclick="submitFinalMdLineRegenerate()">AI 按要求重生成</button>
            <button onclick="submitFinalMdLineEdit()">保存手动修改并重建 Word</button>
          </div>
          <div id="final-edit-result" class="detail-empty small">保存后会在这里显示审查结果。</div>
        </div>
      </div>
    `;
  } catch (error) {
    box.innerHTML = `<div class="detail-empty small">final.md 加载失败：${escapeHtml(error)}</div>`;
  }
}

function selectFinalMdLine(button) {
  document.querySelectorAll(".final-line.is-selected").forEach((item) => item.classList.remove("is-selected"));
  button.classList.add("is-selected");
  const lineNumber = button.getAttribute("data-line-number") || "";
  const lineText = button.getAttribute("data-line-text") || "";
  const selected = document.getElementById("final-selected-line");
  const textArea = document.getElementById("final-edit-text");
  if (selected) selected.textContent = `第 ${lineNumber} 行`;
  if (textArea) {
    textArea.value = lineText;
    textArea.dataset.lineNumber = lineNumber;
  }
}

async function submitFinalMdLineEdit() {
  const textArea = document.getElementById("final-edit-text");
  const instruction = document.getElementById("final-edit-instruction");
  const result = document.getElementById("final-edit-result");
  const lineNumber = Number(textArea?.dataset.lineNumber || 0);
  const newText = (textArea?.value || "").trimEnd();
  if (!lineNumber) {
    alert("请先选择要修改的行。");
    return;
  }
  if (!newText.trim()) {
    alert("请填写修改后的内容。");
    return;
  }
  if (result) result.textContent = "正在保存并触发 Word 重建...";
  try {
    const response = await fetch("/api/final-md/line-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line_number: lineNumber, new_text: newText, instruction: instruction?.value || "" }),
    });
    const data = await response.json();
    if (!data.ok) {
      if (result) result.textContent = data.message || "保存失败。";
      return;
    }
    if (result) {
      const notes = (data.review?.review_notes || []).join("；");
      result.textContent = `${data.message || "已保存。"} 审查：${notes}`;
    }
    appendLog(`[人工改写] final.md 第 ${lineNumber} 行已保存，正在重新生成 Word。`);
    setTimeout(loadStatus, 800);
    await loadFinalMdEditor();
    setTimeout(loadInlineDocxPreview, 1500);
  } catch (error) {
    if (result) result.textContent = `保存失败：${error}`;
  }
}

async function submitFinalMdLineRegenerate() {
  const textArea = document.getElementById("final-edit-text");
  const instruction = document.getElementById("final-edit-instruction");
  const result = document.getElementById("final-edit-result");
  const lineNumber = Number(textArea?.dataset.lineNumber || 0);
  const requirement = (instruction?.value || "").trim();
  if (!lineNumber) {
    alert("请先选择要重生成的行。");
    return;
  }
  if (!requirement) {
    alert("请先填写用户生成要求。");
    return;
  }
  if (result) result.textContent = "AI 正在按要求重生成并触发 Word 重建...";
  try {
    const response = await fetch("/api/final-md/line-regenerate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line_number: lineNumber, instruction: requirement }),
    });
    const data = await response.json();
    if (!data.ok) {
      if (result) result.textContent = data.message || "AI 重生成失败。";
      return;
    }
    if (textArea) textArea.value = data.generated_text || textArea.value;
    if (result) {
      const notes = (data.review?.review_notes || []).join("；");
      result.textContent = `${data.message || "AI 已重生成。"} 审查：${notes}`;
    }
    appendLog(`[AI改写] final.md 第 ${lineNumber} 行已重生成，正在重新生成 Word。`);
    setTimeout(loadStatus, 800);
    await loadFinalMdEditor();
    setTimeout(loadInlineDocxPreview, 1500);
  } catch (error) {
    if (result) result.textContent = `AI 重生成失败：${error}`;
  }
}

function renderStepDetail(data) {
  if (!stepDetailPanel) return;
  if (!data?.ok) {
    stepDetailPanel.innerHTML = `<button class="detail-back" onclick="closeStepDetail()">关闭详情</button><div class="detail-empty">${escapeHtml(data?.message || "节点详情加载失败")}</div>`;
    return;
  }
  const step = data.step || {};
  const summary = data.summary || {};
  const timing = data.timing || {};
  const history = data.history || [];
  const details = data.details || {};
  const stageMetrics = data.stage_metrics || {};
  const agentRuns = data.agent_runs || [];
  const budgetHits = data.budget_hits || [];
  const summaryRows = Object.entries(summary).map(([key, value]) => {
    const isList = Array.isArray(value);
    return `
      <div class="${isList ? "detail-kv wide" : "detail-kv"}">
        <span>${escapeHtml(key)}</span>
        ${isList ? `<ul>${formatValue(value)}</ul>` : `<strong>${formatValue(value)}</strong>`}
      </div>
    `;
  }).join("");
  const historyRows = history.map((item) => `
    <li>
      <span>${escapeHtml(item.updated_at || "")}</span>
      <strong>${escapeHtml(item.status || "")}</strong>
      <em>${escapeHtml(item.message || "")}</em>
    </li>
  `).join("");
  const metricsRows = Object.entries(stageMetrics).map(([key, value]) => `
    <div class="detail-kv">
      <span>${escapeHtml(key)}</span>
      <strong>${escapeHtml(typeof value === "object" ? JSON.stringify(value) : String(value))}</strong>
    </div>
  `).join("");
  const agentRunRows = agentRuns.map((run) => `
    <li>
      <span>${escapeHtml(run.agent_name || "")}${run.chapter_id ? ` / ${escapeHtml(run.chapter_id)}` : ""}</span>
      <strong>${escapeHtml(run.prompt_file || "")} · ${escapeHtml(run.prompt_version || "")}</strong>
      <em>${escapeHtml((run.prompt_checksum || "").slice(0, 12))} · ${escapeHtml(run.model || "")} · ${escapeHtml(String(run.duration_ms || 0))}ms</em>
    </li>
  `).join("");
  const budgetRows = budgetHits.map((item) => `
    <li>
      <span>${escapeHtml(item.chapter_id || item.agent_name || item.metric || "")}</span>
      <strong>${escapeHtml(item.metric || "")}</strong>
      <em>${escapeHtml(JSON.stringify(item).slice(0, 220))}</em>
    </li>
  `).join("");

  const promptSummary = data.prompt_summary || [];
  const promptRows = promptSummary.length
    ? promptSummary.map((p) => {
        const budget = p.context_budget ? ` (budget ${JSON.stringify(p.context_budget)})` : "";
        const state = p.missing ? "缺注册" : p.file_exists ? "存在" : "缺文件";
        return `<li class="${p.file_exists ? "ok" : "missing"}">
          <span>${escapeHtml(p.agent_name)}</span>
          <strong>${escapeHtml(p.prompt_file)} v${escapeHtml(p.version)}${budget}</strong>
          <em>${escapeHtml(state)}</em>
        </li>`;
      }).join("")
    : "<li><span>无 LLM 提示词</span><strong>纯程序阶段</strong><em>本阶段不调用 LLM，无需提示词文件。</em></li>";

  if ((step.command || selectedStepCommand) === "build-docx") {
    stepDetailPanel.innerHTML = `
      <div class="detail-head">
        <div>
          <span class="meta-label">生成 Word</span>
          <h3>文档预览</h3>
        </div>
        <div class="detail-head-actions">
          <button class="detail-back" onclick="closeStepDetail()">关闭详情</button>
        </div>
      </div>
      ${renderFinalMdEditorShell("build-docx")}
    `;
    updateSelectedStepAttachment();
    loadInlineDocxPreview();
    return;
  }

  stepDetailPanel.innerHTML = `
    <div class="detail-head">
      <div>
        <span class="meta-label">节点详情</span>
        <h3>${escapeHtml(step.label || selectedStepCommand)}</h3>
      </div>
      <div class="detail-head-actions">
        <span class="status-pill">${escapeHtml(step.message || "")}</span>
        <button class="detail-back" onclick="closeStepDetail()">关闭详情</button>
      </div>
    </div>
    <div class="detail-command">命令 <code>${escapeHtml(step.command || selectedStepCommand)}</code>${timing.duration_label ? ` · 用时 ${escapeHtml(timing.duration_label)}` : ""}</div>
    <section class="detail-block">
      <h4>关键结果</h4>
      <div class="detail-grid">${summaryRows || "<div class='detail-empty small'>暂无摘要，通常表示该节点还未生成产物。</div>"}</div>
    </section>
    <section class="detail-block">
      <h4>阶段指标</h4>
      <div class="detail-grid">${metricsRows || "<div class='detail-empty small'>暂无阶段指标。</div>"}</div>
    </section>
    <section class="detail-block">
      <h4>Agent Runs</h4>
      <ul class="history-list">${agentRunRows || "<li><span>暂无</span><strong></strong><em>该阶段尚无 agent run。</em></li>"}</ul>
    </section>
    <section class="detail-block">
      <h4>Budget 命中</h4>
      <ul class="history-list">${budgetRows || "<li><span>暂无</span><strong></strong><em>该阶段暂无 budget 命中记录。</em></li>"}</ul>
    </section>
    <section class="detail-block">
      <h4>阶段提示词</h4>
      <ul class="history-list">${promptRows}</ul>
    </section>
    ${renderFinalMdEditorShell(step.command || selectedStepCommand)}
    ${renderScoreRows(details.score_point_rows, details.score_requirement_rows)}
    ${renderReviewRows(details.review_rows)}
    ${renderArtifactList("前置条件", data.requires)}
    ${renderArtifactList("输出产物", data.produces)}
    <section class="detail-block">
      <h4>运行记录</h4>
      <ul class="history-list">${historyRows || "<li><span>暂无</span><strong></strong><em>还没有该节点的运行记录。</em></li>"}</ul>
    </section>
  `;
  updateSelectedStepAttachment();
  if ((step.command || selectedStepCommand) === "build-docx") {
    loadInlineDocxPreview();
  }
}

async function previewArtifact(path) {
  const previewBox = artifactPreviewPanel;
  if (!previewBox) return;
  document.body.classList.add("preview-open");
  previewBox.innerHTML = "正在加载预览...";
  try {
    const response = await fetch(`/api/file-preview?path=${encodeURIComponent(path)}`);
    const data = await response.json();
    if (!data.ok) {
      previewBox.innerHTML = `<div class="detail-empty small">${escapeHtml(data.message || "预览失败")}</div>`;
      return;
    }
    if (data.kind === "list") {
      const rows = (data.items || []).map((item) => `
        <li>
          <button class="artifact-link" onclick="event.stopPropagation();previewArtifact('${escapeHtml(item.path)}')">${escapeHtml(item.name)}</button>
          <strong>${formatBytes(item.size || 0)}</strong>
          <em>${escapeHtml(item.modified || "")}</em>
        </li>
      `).join("");
      previewBox.innerHTML = `
        <div class="preview-title">${escapeHtml(data.path)} · ${data.total || 0} 个文件</div>
        <ul class="artifact-list preview-list">${rows || "<li><span>无文件</span><strong></strong><em></em></li>"}</ul>
      `;
      return;
    }
    if (data.kind === "binary") {
      previewBox.innerHTML = `
        <div class="preview-title">${escapeHtml(data.path)}</div>
        <div class="detail-empty small">${escapeHtml(data.message || "该文件暂不支持内嵌预览。")}</div>
        <div class="preview-meta">${formatBytes(data.metadata?.size || 0)} · ${escapeHtml(data.metadata?.modified || "")}</div>
      `;
      return;
    }
    if (data.kind === "docx") {
      previewBox.innerHTML = `
        <div class="preview-title">${escapeHtml(data.path)}${data.truncated ? " · 已截取前 300 个内容块" : ""}</div>
        <div class="docx-preview">${renderDocxBlocks(data) || "<div class='detail-empty small'>Word 文档没有可抽取文本。</div>"}</div>
        <div class="preview-meta">${formatBytes(data.metadata?.size || 0)} · ${escapeHtml(data.metadata?.modified || "")}</div>
      `;
      return;
    }
    previewBox.innerHTML = `
      <div class="preview-title">${escapeHtml(data.path)}${data.truncated ? " · 已截取前 30000 字符" : ""}</div>
      <pre class="preview-content">${escapeHtml(data.content || "")}</pre>
    `;
  } catch (error) {
    previewBox.innerHTML = `<div class="detail-empty small">预览失败：${escapeHtml(error)}</div>`;
  }
}

function updateSelectedStepAttachment() {
  if (!stepDetailPanel) return;
  stepDetailPanel.style.top = "";
  stepDetailPanel.style.maxHeight = "";
}

async function createWorkspaceOnly() {
  if (currentStatus?.global_running) {
    alert("当前已有步骤正在运行，请稍后再新建工作空间。");
    return;
  }
  if (!getRunName()) {
    alert("请先填写新工作空间名称。");
    runNameInput?.focus();
    updateStartButtonState();
    return;
  }
  try {
    stopAutoRun();
    resetCurrentRunStarted();
    const run = await createRunWorkspace();
    workspaceEntered = true;
    appendLog(`--- 已创建工作空间：${run.relative_root || run.root || ""} ---`);
    if (runNameInput) runNameInput.value = "";
    await loadStatus();
    await loadRuns(true);
  } catch (error) {
    alert("创建失败: " + error.message);
  } finally {
    updateStartButtonState();
  }
}

async function loadManualReviewItems(category) {
  if (!category) return;
  try {
    const response = await fetch(`/api/manual-review/items?category=${encodeURIComponent(category)}`);
    const data = await response.json();
    renderManualReviewItems(data.items || []);
  } catch (error) {
    if (manualReviewItemsPanel) {
      manualReviewItemsPanel.innerHTML = `<div class="detail-empty small">人工复核项加载失败：${escapeHtml(error)}</div>`;
    }
  }
}

async function submitManualReview(category, itemId, status) {
  const noteEl = document.getElementById(`manual-note-${itemId}`);
  const note = noteEl ? noteEl.value.trim() : "";
  try {
    const response = await fetch("/api/manual-review/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category,
        payload: {
          item_id: itemId,
          status,
          operator_note: note,
          operator_instruction: note,
        },
      }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "人工复核更新失败");
      return;
    }
    const confirmed = await confirmV2MaterialAction(data, `确认将 ${itemId} 更新为 ${status}？`);
    if (!confirmed.ok) return;
    appendLog(`[人工复核] ${category}/${itemId} -> ${status}`);
    await loadStatus();
    await loadManualReviewItems(category);
  } catch (error) {
    alert("人工复核更新失败: " + error);
  }
}

async function updateProjectProfile(projectType) {
  if (!projectType || !currentStatus?.active_run?.isolated) return;
  try {
    const response = await fetch("/api/project-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_type: projectType }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "项目类型更新失败");
      return;
    }
    const confirmed = await confirmV2MaterialAction(data, `确认切换项目类型为 ${projectType}？`);
    if (!confirmed.ok) return;
    appendLog(`[项目类型] 已切换为 ${projectType}`);
    await loadStatus();
  } catch (error) {
    alert("项目类型更新失败: " + error);
  }
}

async function showStepDetail(command) {
  if (!stepDetailPanel || stepDetailInFlight) return;
  if (command === "build-docx") {
    selectedStepCommand = command;
    document.body.classList.add("detail-open");
    renderWorkflow();
    switchDetailTab("doc");
    return;
  }
  const changed = selectedStepCommand !== command;
  selectedStepCommand = command;
  document.body.classList.add("detail-open");
  if (detailTab !== "step") switchDetailTab("step");
  if (changed) {
    document.body.classList.remove("preview-open");
    if (artifactPreviewPanel) {
      artifactPreviewPanel.innerHTML = "<div class='preview-placeholder'>点击中间详情里的产物文件进行预览。</div>";
    }
  }
  renderWorkflow();
  if (changed || stepDetailPanel.querySelector(".detail-empty")) {
    stepDetailPanel.innerHTML = "<div class='detail-empty'>正在加载节点详情...</div>";
  }
  stepDetailInFlight = true;
  try {
    const response = await fetch(`/api/workflow-step-detail?command=${encodeURIComponent(command)}`);
    const data = await response.json();
    renderStepDetail(data);
  } catch (error) {
    stepDetailPanel.innerHTML = `<div class="detail-empty">节点详情加载失败：${escapeHtml(error)}</div>`;
  } finally {
    stepDetailInFlight = false;
    updateSelectedStepAttachment();
  }
}

function closeStepDetail() {
  selectedStepCommand = "";
  if (detailTab !== "step") switchDetailTab("step");
  document.body.classList.remove("detail-open", "preview-open");
  logDockExpanded = false;
  if (logBand) logBand.classList.remove("is-expanded");
  if (artifactPreviewPanel) {
    artifactPreviewPanel.innerHTML = "<div class='preview-placeholder'>点击中间详情里的产物文件进行预览。</div>";
  }
  if (stepDetailPanel) {
    stepDetailPanel.style.top = "";
    stepDetailPanel.style.maxHeight = "";
    stepDetailPanel.innerHTML = "<div class='detail-empty'>点击任一流程节点查看详情。</div>";
  }
  renderWorkflow();
}

function toggleLogDock() {
  logDockExpanded = !logDockExpanded;
  if (logBand && logDockExpanded) logBand.classList.add("is-expanded");
  if (logBand && !logDockExpanded) logBand.classList.remove("is-expanded");
}

function updateWorkspaceMeta(runs, activeRunId) {
  if (!workspaceMeta) return;
  const active = runs.find((run) => run.id === activeRunId);
  if (!active) {
    workspaceMeta.textContent = runs.length ? "请选择工作空间" : "暂无工作空间";
    return;
  }
  const progress = active.progress || {};
  const message = progress.message ? ` · ${progress.message}` : "";
  workspaceMeta.textContent = `${active.relative_root || active.root || active.id} · 最近更新 ${progress.modified || "未知"}${message}`;
}

function getRunName() {
  return (runNameInput?.value || "").trim();
}

function updateStartButtonState() {
  const disabled = !!currentStatus?.global_running || !getRunName();
  if (startButton) startButton.disabled = disabled;
  if (createWorkspaceButton) createWorkspaceButton.disabled = disabled;
  if (pauseButton) pauseButton.disabled = !currentStatus?.global_running;
}

function hasActiveWorkspace() {
  return !!currentStatus?.active_run?.isolated && !!currentStatus?.active_run?.id;
}

function updateWorkspaceShell() {
  const active = workspaceEntered && hasActiveWorkspace();
  document.body.classList.toggle("workspace-ready", active);
  document.body.classList.toggle("workspace-locked", !active);
  if (workspaceGate) workspaceGate.hidden = active;
  if (sourceBand) sourceBand.hidden = !active;
  if (mainWorkbench) mainWorkbench.hidden = !active;
}

async function enterSelectedWorkspace() {
  const runId = workspaceSelect?.value || "";
  if (!runId) {
    alert("请先选择工作空间。");
    return;
  }
  if (runId === currentStatus?.active_run?.id) {
    workspaceEntered = true;
    updateWorkspaceShell();
    return;
  }
  await selectWorkspace(runId);
}

async function loadRuns(force = false) {
  if (!workspaceSelect || runsLoadInFlight) return;
  if (!force && Date.now() - lastRunsLoadedAt < 5000) return;
  runsLoadInFlight = true;
  try {
    const response = await fetch("/api/runs");
    const data = await response.json();
    if (!data.ok) return;
    const runs = data.runs || [];
    const activeRunId = data.active_run_id || currentStatus?.active_run?.id || "";
    workspaceSelect.innerHTML = runs.length
      ? runs.map((run) => `<option value="${escapeHtml(run.id)}">${escapeHtml(formatRunOption(run))}</option>`).join("")
      : "<option value=''>暂无工作空间</option>";
    workspaceSelect.value = activeRunId;
    workspaceSelect.disabled = !runs.length;
    updateWorkspaceMeta(runs, activeRunId);
    lastRunsLoadedAt = Date.now();
  } catch (error) {
    appendLog("[前端] 工作空间列表加载失败: " + error);
  } finally {
    runsLoadInFlight = false;
  }
}

function countDoneSteps(workflow) {
  const steps = coreSteps(workflow);
  if (currentStatus?.running) {
    const activeIndex = getActiveIndex(steps);
    return activeIndex > 0 ? activeIndex : 0;
  }
  return steps.filter((step) => displayStepDone(step)).length;
}

function coreSteps(workflow) {
  return workflow.filter((step) => step.kind !== "utility");
}

function getActiveCommand() {
  if (!currentStatus) return "";
  const stage = currentStatus.run_state?.stage || "";
  if (currentStatus.running && currentStatus.current_task !== "graph-run" && currentStatus.current_task !== "run") {
    return currentStatus.current_task;
  }
  if (currentStatus.running && (currentStatus.current_task === "graph-run" || currentStatus.current_task === "run")) {
    return stageToCommand(stage);
  }
  return "";
}

function hasStartedCurrentRun() {
  if (sessionStorage.getItem(RUN_STARTED_KEY) === "1" || !!currentStatus?.running) return true;
  if (!currentStatus?.active_run?.isolated) return false;
  if (currentStatus?.run_state?.updated_at) return true;
  return coreSteps(currentStatus.workflow || []).some((step) => step.done);
}

function markCurrentRunStarted() {
  sessionStorage.setItem(RUN_STARTED_KEY, "1");
}

function resetCurrentRunStarted() {
  sessionStorage.removeItem(RUN_STARTED_KEY);
  stopAutoRun();
}

function isAutoRunActive() {
  if (!autoRunActive) return false;
  if (!currentStatus) return true;
  const activeRunId = currentStatus.active_run?.id || "";
  return !!activeRunId && activeRunId === autoRunId && !!currentStatus.active_run?.isolated;
}

function startAutoRunQueue(runId, startIndex = 0) {
  markCurrentRunStarted();
  autoRunActive = true;
  autoRunId = runId || "";
  autoRunIndex = Math.max(0, startIndex);
  autoLastCommand = "";
  autoLastStartedAt = 0;
}

function stopAutoRun() {
  autoRunActive = false;
  autoRunId = "";
  autoRunIndex = 0;
  autoLastCommand = "";
  autoLastStartedAt = 0;
  sessionStorage.removeItem(AUTO_RUN_KEY);
  sessionStorage.removeItem(AUTO_RUN_ID_KEY);
  sessionStorage.removeItem(AUTO_INDEX_KEY);
  sessionStorage.removeItem(AUTO_LAST_COMMAND_KEY);
  sessionStorage.removeItem(AUTO_LAST_STARTED_AT_KEY);
}

function getAutoIndex() {
  return autoRunIndex;
}

function getWorkflowStep(command) {
  return (currentStatus?.workflow || []).find((step) => step.command === command);
}

function isCommandComplete(command) {
  const stage = currentStatus?.run_state?.stage || "";
  const runStateCommand = stageToCommand(stage) || stage;
  if (["error", "paused"].includes(currentStatus?.run_state?.status) && runStateCommand === command) return false;
  if (command === "init") return true;
  const step = getWorkflowStep(command);
  return !!step?.done;
}

function displayStepDone(step) {
  return hasStartedCurrentRun() && !!step.done;
}

function displayStepState(step) {
  if (!hasStartedCurrentRun()) return "pending";
  return step.state;
}

function getActiveIndex(steps) {
  const activeCommand = getActiveCommand();
  return steps.findIndex((step) => step.command === activeCommand);
}

function workflowStateLabel(step, index, activeIndex) {
  if (step.command === getActiveCommand() && currentStatus?.running) return "运行中";
  if (currentStatus?.running) return index < activeIndex ? "已完成" : "等待";
  if (displayStepDone(step)) return "已完成";
  if (displayStepState(step) === "ready") return "待执行";
  return "等待";
}

function renderSourceFiles() {
  const sources = currentStatus?.sources || {};
  const groups = [
    ["tender", "招标文件", sources.tender || []],
    ["company", "公司资料", sources.company || []],
    ["template", "Word 模板", sources.template || []],
  ];

  groups.forEach(([key, , files]) => {
    const count = document.getElementById(`${key}-count`);
    if (count) count.textContent = String(files.length);
    const uploadTile = document.querySelector(`[data-upload-category="${key}"]`);
    if (uploadTile) uploadTile.hidden = files.length > 0;
  });

  const pendingUploads = groups.filter(([, , files]) => !files.length).length;
  const uploadGrid = document.querySelector(".upload-grid");
  if (uploadGrid) uploadGrid.hidden = pendingUploads === 0;

  sourceFilesBox.innerHTML = groups.map(([, title, files]) => {
    const items = files.length
      ? files.map((file) => `
          <li>
            <strong>${escapeHtml(file.name)}</strong>
            <span>${formatBytes(file.size)} · ${escapeHtml(file.modified)}</span>
          </li>
        `).join("")
      : "<li class='empty-source'><strong>暂无文件</strong><span>请上传或放入 sources 目录</span></li>";
    return `
      <article class="source-group ${files.length ? "has-files" : ""}">
        <h3>${title}</h3>
        ${files.length ? "<div class='source-uploaded'>已上传，上传入口已隐藏</div>" : ""}
        <ul>${items}</ul>
      </article>
    `;
  }).join("");
}

function updateProgressSummary(workflow) {
  const steps = coreSteps(workflow);
  const done = countDoneSteps(workflow);
  const total = steps.length || 1;
  const percent = Math.round((done / total) * 100);
  const activeCommand = getActiveCommand();
  const activeStep = steps.find((step) => step.command === activeCommand);
  const nextStep = hasStartedCurrentRun() ? currentStatus?.next_step : steps[0];
  const stale = currentStatus?.sync?.source_stale;
  const failed = ["error", "paused"].includes(currentStatus?.run_state?.status);

  progressPercent.textContent = `${percent}%`;
  progressFill.style.width = `${percent}%`;
  currentStage.textContent = activeStep?.label || nextStep?.label || "暂无待执行步骤";

  if (!hasStartedCurrentRun()) {
    progressCaption.textContent = "尚未开始本次生成，点击顶部“开始生成”后会从第一步自动执行。";
    currentStage.textContent = "待开始";
  } else if (currentStatus?.running) {
    progressCaption.textContent = `正在执行：${activeStep?.label || commandLabel(currentStatus.current_task)}`;
  } else if (failed) {
    progressCaption.textContent = `流程已暂停：${currentStatus.run_state?.message || "上一步执行失败"}`;
  } else if (stale) {
    progressCaption.textContent = "检测到 sources 资料有更新，请先重新导入资料。";
  } else if (nextStep) {
    progressCaption.textContent = `下一步：${nextStep.label}`;
  } else {
    progressCaption.textContent = "完整流程已完成。";
  }
}

function renderWorkflow() {
  const workflow = currentStatus?.workflow || [];
  const steps = coreSteps(workflow);
  workflowList.innerHTML = "";

  if (!steps.length) {
    workflowList.innerHTML = "<div class='empty-state'>暂无流程数据，请刷新状态。</div>";
    updateProgressSummary([]);
    return;
  }

  updateProgressSummary(workflow);
  const activeCommand = getActiveCommand();
  const activeIndex = getActiveIndex(steps);

  workflowList.innerHTML = steps.map((step, index) => {
    const active = currentStatus?.running && step.command === activeCommand;
    const done = currentStatus?.running ? index < activeIndex : displayStepDone(step);
    const state = currentStatus?.running
      ? (done ? "done" : active ? "ready" : "pending")
      : displayStepState(step);
    const classes = ["flow-step", `state-${state}`];
    if (active) classes.push("is-active");
    if (done) classes.push("is-done");
    const missing = hasStartedCurrentRun() && step.missing_requires?.length ? step.missing_requires.join("、") : "";
    const message = hasStartedCurrentRun() ? step.message : "待开始";

    return `
      <article class="${classes.join(" ")}${selectedStepCommand === step.command ? " is-selected" : ""}" role="button" tabindex="0" onclick="showStepDetail('${escapeHtml(step.command)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();showStepDetail('${escapeHtml(step.command)}')}">
        <div class="step-marker">
          <span>${String(index + 1).padStart(2, "0")}</span>
        </div>
        <div class="step-body">
          <div class="step-title-row">
            <h3>${escapeHtml(step.label)}</h3>
            <span class="status-pill">${workflowStateLabel(step, index, activeIndex)}</span>
          </div>
          <p>${escapeHtml(message || "")}</p>
          <div class="step-meta">
            <span>命令 <code>${escapeHtml(step.command)}</code></span>
            ${missing ? `<span>缺少 ${escapeHtml(missing)}</span>` : `<span>产物 ${escapeHtml((step.produces || []).join("、"))}</span>`}
          </div>
        </div>
      </article>
    `;
  }).join("");
  updateSelectedStepAttachment();
}

function updateChrome() {
  if (!currentStatus) return;
  const running = !!currentStatus.running;
  const globalRunning = !!currentStatus.global_running;
  const activeCommand = getActiveCommand();
  const workflow = coreSteps(currentStatus.workflow || []);
  const activeStep = workflow.find((step) => step.command === activeCommand);
  const nextStep = hasStartedCurrentRun() ? currentStatus.next_step : workflow[0];
  const failed = ["error", "paused"].includes(currentStatus.run_state?.status);

  if (currentStatus.running) markCurrentRunStarted();
  updateWorkspaceShell();

  heroTask.textContent = running
    ? `运行中：${activeStep?.label || commandLabel(currentStatus.current_task)}`
    : globalRunning
      ? `后台运行中：${currentStatus.running_run?.relative_root || currentStatus.running_run?.id || ""}`
    : failed
      ? `已暂停：${currentStatus.run_state?.message || "上一步执行失败"}`
    : (hasStartedCurrentRun() ? (nextStep ? `等待执行：${nextStep.label}` : "本次流程已完成") : "待开始：点击开始生成");
  heroTask.title = currentStatus.active_run?.relative_root || currentStatus.active_run?.root || "";
  heroDot.classList.toggle("running", running);
  heroDot.classList.toggle("error", !running && failed);

  runningNotice.hidden = !globalRunning;
  runningTask.textContent = globalRunning ? heroTask.textContent : "";
  disableActionButtons(globalRunning);
  updateStartButtonState();

  renderSourceFiles();
  renderWorkflow();
  renderProjectProfile();
  renderLatestAgentRuns();
  renderManualReviewSummary();
  if (manualReviewCategory) {
    loadManualReviewItems(manualReviewCategory.value || "template_evidence");
  }
  updateSelectedStepAttachment();
  scheduleAutoRunTick();
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    currentStatus = await response.json();
    updateChrome();
    loadRuns();
    maybeAutoReloadDocEditor();
    loadAgentWorkbench(false);
  } catch (error) {
    appendLog("[前端] 状态加载失败: " + error);
  }
}

let agentWorkbenchCache = { goal: null, decisions: [], materials: null, snapshot: null };

function planStepIcon(status) {
  const s = String(status || "pending");
  if (s === "done" || s === "skipped") return "✓";
  if (s === "running") return "●";
  if (s === "blocked" || s === "failed") return "!";
  return "○";
}

function renderAgentGoalCard(goal, summary) {
  const el = document.getElementById("agent-goal-card");
  if (!el) return;
  if (!goal) {
    el.innerHTML = `<p class="muted">暂无活动目标。在聊天中提出如「补齐评分点并出 Word」即可创建。</p>`;
    return;
  }
  const progress = goal.progress || {};
  const criteria = goal.criteria_results || [];
  const failed = criteria.filter((c) => !c.ok).slice(0, 5);
  const criteriaHtml = criteria.length
    ? `<ul class="agent-list">${criteria
        .map((c) => `<li class="${c.ok ? "ok" : "bad"}">${escapeHtml(c.check)}: ${escapeHtml(c.detail || "")}</li>`)
        .join("")}</ul>`
    : "<p class='muted'>无成功条件</p>";
  el.innerHTML = `
    <div class="agent-kv"><span>原始目标</span><strong>${escapeHtml(goal.raw_user_goal || "")}</strong></div>
    <div class="agent-kv"><span>状态</span><strong class="status-${escapeHtml(goal.status || "")}">${escapeHtml(goal.status || "")}</strong></div>
    <div class="agent-kv"><span>完成度</span><strong>${escapeHtml(
      progress.plan_total
        ? `${progress.plan_done || 0}/${progress.plan_total}`
        : progress.criteria_total
          ? `${progress.criteria_ok || 0}/${progress.criteria_total}`
          : goal.all_criteria_ok
            ? "已达成"
            : "进行中"
    )}</strong></div>
    ${goal.blocked_reason ? `<div class="agent-block">阻断：${escapeHtml(goal.blocked_reason)}</div>` : ""}
    ${goal.status === "awaiting_confirmation" ? `<div class="agent-block">等待确认后执行变更类操作</div>` : ""}
    <div class="agent-sub">成功条件</div>
    ${criteriaHtml}
    <p class="muted small">${escapeHtml(summary || "")}</p>
  `;
}

function renderAgentPlanCard(goal) {
  const el = document.getElementById("agent-plan-card");
  if (!el) return;
  const plan = (goal && goal.plan) || [];
  if (!plan.length) {
    el.innerHTML = `<p class="muted">当前目标没有结构化计划（只读查询/对话类）。</p>`;
    return;
  }
  const idx = Number(goal.current_plan_index || 0);
  el.innerHTML = `<ol class="agent-plan-list">${plan
    .map((step, i) => {
      const st = step.status || "pending";
      const active = i === idx && !["done", "skipped"].includes(st);
      return `<li class="${active ? "is-active" : ""} ${st}">
        <span class="plan-icon">${planStepIcon(active ? "running" : st)}</span>
        <span>${escapeHtml(step.label || step.step_id || step.tool || "")}</span>
        <span class="muted small">${escapeHtml(st)}</span>
      </li>`;
    })
    .join("")}</ol>`;
}

function renderAgentDecisions() {
  const el = document.getElementById("agent-decisions-card");
  if (!el) return;
  const debug = document.getElementById("agent-debug-trace")?.checked;
  const items = agentWorkbenchCache.decisions || [];
  if (!items.length) {
    el.innerHTML = `<p class="muted">暂无决策记录。</p>`;
    return;
  }
  el.innerHTML = `<ul class="agent-list decisions">${items
    .slice()
    .reverse()
    .slice(0, 12)
    .map((d) => {
      const summary = d.user_summary || d.thought_summary || d.selected_tool || "观察";
      if (!debug) {
        return `<li>${escapeHtml(summary)}</li>`;
      }
      return `<li>
        <div>${escapeHtml(summary)}</div>
        <div class="muted small">tool=${escapeHtml(d.selected_tool || "")} ok=${escapeHtml(String(d.ok))} exec=${escapeHtml(String(d.executed))}</div>
        <div class="muted small">${escapeHtml((d.observation_summary || "").slice(0, 160))}</div>
      </li>`;
    })
    .join("")}</ul>`;
}

function renderAgentHumanCard(materials, goal, snapshot) {
  const el = document.getElementById("agent-human-card");
  if (!el) return;
  const missing = (materials && materials.missing) || (snapshot && snapshot.materials && snapshot.materials.missing) || [];
  const blocked = goal && (goal.status === "blocked_human" || goal.blocked_reason);
  if (!missing.length && !blocked) {
    el.innerHTML = `<p class="muted">当前无需人工补料。若导出前检查未过，请处理质量问题单。</p>
      <div class="toolbar"><button onclick="sendQuickChat('出稿前检查')">出稿前检查</button></div>`;
    return;
  }
  const rows = (missing || []).slice(0, 8).map((m) => {
    const id = m.item_id || "";
    return `<li>
      <strong>${escapeHtml(m.requirement || id)}</strong>
      <div class="muted small">缺什么：${escapeHtml(m.suggested_attachment || "对应证明材料")}；影响：${escapeHtml((m.target_chapter_hints || []).join("、") || "相关章节")}；等级：${escapeHtml(m.severity || "")}</div>
      <button onclick="markMaterialUploaded('${escapeHtml(id)}')">标记已上传并恢复</button>
    </li>`;
  });
  el.innerHTML = `
    ${blocked ? `<div class="agent-block">${escapeHtml(goal.blocked_reason || "需要人工处理")}</div>` : ""}
    <ul class="agent-list">${rows.join("") || "<li>见材料清单</li>"}</ul>
    <div class="toolbar">
      <button onclick="showStepDetail('build-materials-checklist')">打开材料清单</button>
      <button class="primary" onclick="refillMaterials()">补料后局部回填</button>
      <button onclick="resumeAgentGoal()">从阻断继续</button>
    </div>
    <p class="muted small">补充后只重跑受影响章节，不会全量重跑。</p>
  `;
}

async function loadAgentWorkbench(force) {
  if (!hasActiveWorkspace() && !force) return;
  try {
    const [goalRes, decRes, matRes, snapRes] = await Promise.all([
      fetch("/api/agent/goal").then((r) => r.json()).catch(() => ({})),
      fetch("/api/agent/decisions?tail=20").then((r) => r.json()).catch(() => ({})),
      fetch("/api/materials-checklist").then((r) => r.json()).catch(() => ({})),
      fetch("/api/agent/snapshot").then((r) => r.json()).catch(() => ({})),
    ]);
    agentWorkbenchCache.goal = goalRes.goal || null;
    agentWorkbenchCache.decisions = decRes.decisions || [];
    agentWorkbenchCache.materials = matRes;
    agentWorkbenchCache.snapshot = snapRes.snapshot || null;
    const snapMats = (snapRes.snapshot && snapRes.snapshot.materials) || {};
    renderAgentGoalCard(goalRes.goal, goalRes.summary);
    renderAgentPlanCard(goalRes.goal);
    renderAgentDecisions();
    renderAgentHumanCard(snapMats, goalRes.goal, snapRes.snapshot);
  } catch (error) {
    // non-fatal
  }
}

async function resumeAgentGoal() {
  try {
    const response = await fetch("/api/agent/goal/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: "web_resume" }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "恢复失败");
      return;
    }
    await loadAgentWorkbench(true);
    sendQuickChat("继续");
  } catch (error) {
    alert("恢复失败: " + error);
  }
}

async function confirmV2MaterialAction(data, promptText) {
  if (!data?.action) return data;
  if (!window.confirm(promptText || data.action.label || "确认执行此操作？")) {
    const workspaceId = encodeURIComponent(data.action.workspace_id || "");
    const actionId = encodeURIComponent(data.action.action_id || data.action.confirmation_id || "");
    if (workspaceId && actionId) {
      await fetch(`/api/v2/workspaces/${workspaceId}/actions/${actionId}/decline`, { method: "POST" });
    }
    return { ok: false, cancelled: true, message: "已取消操作" };
  }
  const workspaceId = encodeURIComponent(data.action.workspace_id || "");
  const actionId = encodeURIComponent(data.action.action_id || data.action.confirmation_id || "");
  if (!workspaceId || !actionId) throw new Error("确认信息不完整");
  const response = await fetch(`/api/v2/workspaces/${workspaceId}/actions/${actionId}/confirm`, {
    method: "POST",
  });
  const confirmed = await response.json();
  if (!confirmed.ok) throw new Error(confirmed.message || confirmed.error?.message || "执行失败");
  return confirmed;
}

async function markMaterialUploaded(itemId) {
  if (!itemId) return;
  try {
    const response = await fetch("/api/materials-checklist/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: itemId, note: "用户在工作台标记已上传" }),
    });
    let data = await response.json();
    if (!data.ok) {
      alert(data.message || "标记失败");
      return;
    }
    data = await confirmV2MaterialAction(data, "确认登记并验证这份材料？");
    if (data.cancelled) return;
    appendLog("[材料] " + (data.message || itemId));
    await loadAgentWorkbench(true);
  } catch (error) {
    alert("标记失败: " + error);
  }
}

async function refillMaterials() {
  try {
    const response = await fetch("/api/materials-checklist/refill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replan_jobs: true, max_chapters: 10 }),
    });
    let data = await response.json();
    if (!data.ok) throw new Error(data.message || "回填失败");
    data = await confirmV2MaterialAction(data, "确认将已验证材料回填正文？");
    if (data.cancelled) return;
    alert(data.message || (data.ok ? "回填完成" : "回填失败"));
    await loadAgentWorkbench(true);
    setTimeout(loadStatus, 500);
  } catch (error) {
    alert("回填失败: " + error);
  }
}

function refreshStatus() {
  loadStatus();
}

function disableActionButtons(disabled) {
  document.querySelectorAll("button").forEach((button) => {
    if (button.classList.contains("danger")) return;
    if (button.id === "btn-pause") return;
    if (button.closest(".workspace-overlay")) return;
    if (button.closest(".workspace-switcher")) return;
    if (button.getAttribute("onclick") === "refreshStatus()") return;
    button.disabled = disabled;
  });
  if (pauseButton) pauseButton.disabled = !currentStatus?.global_running;
}

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

function connectLogStream() {
  if (streamSource) streamSource.close();
  streamSource = new EventSource("/api/logs/stream");
  streamSource.onmessage = function (event) {
    try {
      appendLog(JSON.parse(event.data).line);
    } catch (_) {}
  };
  streamSource.onerror = function () {
    streamSource.close();
    streamSource = null;
    setTimeout(connectLogStream, 2000);
  };
}

async function runCommand(command) {
  try {
    const response = await fetch("/api/run-command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, run_id: currentStatus?.active_run?.id || "" }),
    });
    const data = await response.json();
    if (data.ok && data.action) {
      const confirmed = await confirmV2MaterialAction(data, data.action.label || `确认执行 ${command}？`);
      if (!confirmed.ok) return;
    }
    if (!data.ok) {
      alert(data.message);
      return;
    }
    if (command !== "validate") markCurrentRunStarted();
    appendLog("--- 触发: " + commandLabel(command) + " ---");
    connectLogStream();
    setTimeout(loadStatus, 500);
  } catch (error) {
    alert("请求失败: " + error);
  }
}

async function createRunWorkspace() {
  const response = await fetch("/api/start-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: getRunName(), project_type: projectTypeSelect?.value || "general" }),
  });
  const data = await response.json();
  if (!data.ok) throw new Error(data.message || "创建运行工作空间失败");
  await loadRuns(true);
  return data.run;
}

async function selectWorkspace(runId) {
  if (!runId || runId === currentStatus?.active_run?.id) return;
  try {
    stopAutoRun();
    closeStepDetail();
    sessionStorage.removeItem(RUN_STARTED_KEY);
    const response = await fetch("/api/select-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message);
      await loadRuns(true);
      return;
    }
    workspaceEntered = true;
    appendLog(`--- 已切换工作空间：${data.run?.relative_root || data.run?.root || runId} ---`);
    await loadStatus();
    await loadRuns(true);
  } catch (error) {
    alert("切换工作空间失败: " + error);
    await loadRuns(true);
  }
}

async function startAutoRun() {
  if (currentStatus?.global_running) {
    alert("当前已有步骤正在运行，请稍后再开始。");
    return;
  }
  if (!getRunName()) {
    alert("请先填写新工作空间名称。");
    runNameInput?.focus();
    updateStartButtonState();
    return;
  }
  try {
    stopAutoRun();
    resetCurrentRunStarted();
    const run = await createRunWorkspace();
    workspaceEntered = true;
    startAutoRunQueue(run.id);
    appendLog(`--- 自动流程已启动：${run.relative_root || run.root || ""} ---`);
    if (runNameInput) runNameInput.value = "";
    await loadStatus();
    runNextAutoCommand();
  } catch (error) {
    alert("启动失败: " + error.message);
    stopAutoRun();
  }
}

async function resumeAutoRun() {
  if (currentStatus?.global_running) {
    alert("当前已有步骤正在运行，请先暂停或等待完成。");
    return;
  }
  if (!currentStatus?.active_run?.isolated || !currentStatus.active_run?.id) {
    alert("当前没有可恢复的运行工作空间，请先点击“开始生成”。");
    return;
  }
  if (!currentStatus?.next_step) {
    alert("当前没有可继续执行的步骤。");
    return;
  }
  const index = autoRunCommands().indexOf(currentStatus.next_step.command);
  if (index < 0) {
    alert("当前下一步不在自动流程中。");
    return;
  }
  stopAutoRun();
  startAutoRunQueue(currentStatus.active_run.id, index);
  appendLog(`--- 从断点继续：${currentStatus.active_run.relative_root || currentStatus.active_run.root || ""} / ${currentStatus.next_step.label} ---`);
  await loadStatus();
  runNextAutoCommand();
}

async function pauseRun() {
  try {
    stopAutoRun();
    const response = await fetch("/api/pause-run", { method: "POST" });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "暂停失败");
      return;
    }
    appendLog("[前端] " + (data.message || "已发送暂停指令。"));
    setTimeout(loadStatus, 500);
    setTimeout(() => loadRuns(true), 800);
  } catch (error) {
    alert("暂停失败: " + error);
  }
}

function runRecommendedStep() {
  if (!currentStatus?.next_step) {
    alert("当前没有可执行的下一步。");
    return;
  }
  runCommand(currentStatus.next_step.command);
}

async function uploadFiles(category) {
  const input = document.getElementById("upload-" + category);
  if (!input || !input.files.length) {
    alert("请先选择文件。");
    return;
  }

  const form = new FormData();
  for (const file of input.files) form.append("files", file);

  try {
    const response = await fetch("/api/upload?category=" + category, { method: "POST", body: form });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message);
      return;
    }
    appendLog("上传成功: " + data.saved.join(", "));
    input.value = "";
    loadStatus();
  } catch (error) {
    alert("上传失败: " + error);
  }
}

function downloadFinalMd() {
  window.open("/api/download/final-md", "_blank");
}

async function downloadFinalDocx() {
  const runId = currentStatus?.active_run?.id || "";
  if (!runId) {
    alert("缺少活动工作空间，无法下载正式稿");
    return;
  }
  const target = window.open("", "_blank");
  try {
    const workspace = encodeURIComponent(runId);
    const snapshotResponse = await fetch(`/api/v2/workspaces/${workspace}/snapshot`);
    const snapshotData = await snapshotResponse.json();
    if (!snapshotData.ok) throw new Error(snapshotData.message || "读取控制状态失败");
    const commandId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const gateResponse = await fetch(`/api/v2/workspaces/${workspace}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: commandId,
        kind: "gate.revalidate",
        payload: {},
        expected_revision: Number(snapshotData.snapshot?.revision || 0),
        idempotency_key: commandId,
      }),
    });
    const gate = await gateResponse.json();
    if (!gate.ok) throw new Error(gate.message || gate.receipt?.error?.message || "正式稿门禁未通过");
    const latestResponse = await fetch(`/api/v2/workspaces/${workspace}/gates/latest`);
    const latest = await latestResponse.json();
    const receiptId = latest.gate_receipt?.receipt_id || "";
    if (!latest.ok || !receiptId) throw new Error(latest.message || "未取得 GateReceipt");
    const url = `/api/v2/workspaces/${workspace}/exports/final?gate_receipt_id=${encodeURIComponent(receiptId)}`;
    if (target) target.location.href = url;
    else window.open(url, "_blank");
  } catch (error) {
    if (target) target.close();
    alert("正式稿下载失败: " + error);
  }
}

async function viewGlobalReview() {
  try {
    const response = await fetch("/api/file/global-review");
    const data = await response.json();
    if (!data.ok) {
      alert(data.message);
      return;
    }
    const win = window.open("", "_blank");
    win.document.write("<pre style='font-size:12px;white-space:pre-wrap;word-break:break-all'>" + escapeHtml(JSON.stringify(data.data, null, 2)) + "</pre>");
  } catch (error) {
    alert("请求失败: " + error);
  }
}

function confirmClean() {
  if (!confirm("确认清理 workspace/ 和 outputs/？现有内容将移入可恢复归档区。")) return;
  fetch("/api/clean-workspace", { method: "POST" })
    .then((response) => response.json())
    .then(async (data) => {
      const confirmed = await confirmV2MaterialAction(data, "确认将现有工作区产物移入可恢复归档区？");
      if (confirmed.ok) appendLog("工作区产物已清理并归档");
      resetCurrentRunStarted();
      loadStatus();
    });
}

function scheduleAutoRunTick() {
  if (!isAutoRunActive() || currentStatus?.running) return;
  setTimeout(runNextAutoCommand, 300);
}

async function runNextAutoCommand() {
  if (!isAutoRunActive() || currentStatus?.running) return;

  const lastCommand = autoLastCommand;
  if (lastCommand) {
    const startedAt = autoLastStartedAt;
    if (Date.now() - startedAt < 2000) return;
    if (!isCommandComplete(lastCommand)) {
      appendLog(`[自动流程] ${commandLabel(lastCommand)} 未完成，流程已暂停。`);
      stopAutoRun();
      loadStatus();
      return;
    }
    const completedIndex = autoRunCommands().indexOf(lastCommand);
    autoRunIndex = Math.max(completedIndex + 1, getAutoIndex());
    autoLastCommand = "";
  }

  const index = getAutoIndex();
  const command = autoRunCommands()[index];
  if (!command) {
    appendLog("--- 自动流程已完成 ---");
    stopAutoRun();
    loadStatus();
    return;
  }

  autoLastCommand = command;
  autoLastStartedAt = Date.now();
  await runCommand(command);
}

loadStatus();
connectLogStream();
if (logBand) logBand.classList.remove("is-expanded");
updateStartButtonState();
loadChatHistory();
renderChatMessages();
if (projectTypeSelect) {
  projectTypeSelect.addEventListener("change", () => {
    if (currentStatus?.active_run?.isolated) {
      updateProjectProfile(projectTypeSelect.value);
    }
  });
}
if (chatInput) {
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitChat();
    }
  });
}
setInterval(loadStatus, 2000);

// ===============================================================
//  Middle WYSIWYG Word-style editor
// ===============================================================

let detailTab = "step";
let docBlocksCache = [];
let docEditorLoading = false;
let pendingDocEdit = null;
let docChatEditState = null;
let prevGlobalRunning = false;

function isAnythingRunning() {
  return !!(currentStatus && (currentStatus.running || currentStatus.global_running));
}

function maybeAutoReloadDocEditor() {
  if (!currentStatus) return;
  const now = !!currentStatus.global_running;
  if (prevGlobalRunning && !now) {
    if (detailTab === "doc") {
      const page = document.getElementById("doc-editor-page");
      const hasEditFocus = !!(page && page.contains(document.activeElement) && document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute("contenteditable") === "true");
      if (!hasEditFocus) {
        setTimeout(loadDocEditor, 600);
      }
    }
  }
  prevGlobalRunning = now;
}

function switchDetailTab(tab) {
  detailTab = tab;
  const stepTab = document.getElementById("tab-step-detail");
  const docTab = document.getElementById("tab-doc-editor");
  if (stepTab) stepTab.classList.toggle("is-active", tab === "step");
  if (docTab) docTab.classList.toggle("is-active", tab === "doc");
  document.body.classList.toggle("doc-editor-mode", tab === "doc");
  const stepPanel = document.getElementById("step-detail-panel");
  const docPanel = document.getElementById("doc-editor-panel");
  if (stepPanel) stepPanel.hidden = (tab !== "step");
  if (docPanel) docPanel.hidden = (tab !== "doc");
  if (tab === "doc") loadDocEditor();
}

async function loadDocEditor() {
  const page = document.getElementById("doc-editor-page");
  const state = document.getElementById("doc-editor-state");
  if (!page) return;
  if (docEditorLoading) return;
  docEditorLoading = true;
  if (state) state.textContent = "正在加载 final.md...";
  try {
    const [renderResponse, pendingResponse] = await Promise.all([
      fetch("/api/final-doc/render"),
      fetch("/api/final-doc/pending"),
    ]);
    const render = await renderResponse.json();
    const pendingData = await pendingResponse.json().catch(() => ({ pending: null }));
    pendingDocEdit = pendingData.pending || null;
    if (!render.final_md_exists) {
      page.innerHTML = "<div class='detail-empty small'>尚未生成 final.md，请先执行到 build-md / build-docx 阶段。</div>";
      if (state) state.textContent = "等待 final.md 生成";
      docBlocksCache = [];
      renderDocPendingBar();
      return;
    }
    docBlocksCache = render.blocks || [];
    renderDocEditor(docBlocksCache);
    if (state) state.textContent = `final.md ${render.final_md_len || 0} 字符 · ${docBlocksCache.length} 块 · Word ${render.final_docx_exists ? "已生成" : "未生成"}`;
    renderDocPendingBar();
    if (pendingDocEdit && pendingDocEdit.kind === "chat_edit" && pendingDocEdit.new_md) {
      docChatEditState = { instruction: pendingDocEdit.instruction || "", preview: pendingDocEdit.new_md || "", loading: false };
    }
  } catch (error) {
    page.innerHTML = `<div class="detail-empty small">加载失败：${escapeHtml(String(error))}</div>`;
    if (state) state.textContent = "加载失败";
  } finally {
    docEditorLoading = false;
  }
}

function reloadDocEditor() {
  return loadDocEditor();
}

function renderDocEditor(blocks) {
  const page = document.getElementById("doc-editor-page");
  if (!page) return;
  if (!blocks.length) {
    page.innerHTML = "<div class='detail-empty small'>final.md 为空。</div>";
    return;
  }
  const html = [];
  let i = 0;
  while (i < blocks.length) {
    const b = blocks[i];
    if (b.type === "bullet") {
      const group = [];
      while (i < blocks.length && blocks[i].type === "bullet") {
        group.push(blocks[i]);
        i++;
      }
      html.push(renderDocListBlock(group, "ul"));
      continue;
    }
    if (b.type === "numbered") {
      const group = [];
      while (i < blocks.length && blocks[i].type === "numbered") {
        group.push(blocks[i]);
        i++;
      }
      html.push(renderDocListBlock(group, "ol"));
      continue;
    }
    html.push(renderDocSingleBlock(b));
    i++;
  }
  page.innerHTML = html.join("");
  page.onmouseup = handleDocSelection;
  page.querySelectorAll(".doc-block[data-block-id]").forEach(setDocBlockEditable);
  if (pendingDocEdit && pendingDocEdit.block_id) {
    setTimeout(() => {
      const target = page.querySelector(`.doc-block[data-block-id="${cssEscape(pendingDocEdit.block_id)}"]`);
      if (target) {
        target.classList.add("is-pending");
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }, 100);
  }
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, (m) => "\\" + m);
}

function docBlockBadges(b) {
  const label = b.type + (b.level ? " H" + b.level : "");
  return `<span class="doc-block-badges"><span class="badge">${escapeHtml(label)}</span></span>`;
}

function renderDocSingleBlock(b) {
  const id = b.block_id;
  if (b.type === "heading") {
    const lvl = Math.min(6, Math.max(1, b.level || 2));
    return `<h${lvl} class="doc-block" data-block-id="${escapeHtml(id)}" data-block-type="heading" data-block-text="${escapeHtml(b.text || "")}">${docBlockBadges(b)}${escapeHtml(b.text || "")}</h${lvl}>`;
  }
  if (b.type === "paragraph") {
    return `<p class="doc-block" data-block-id="${escapeHtml(id)}" data-block-type="paragraph" data-block-text="${escapeHtml(b.text || "")}">${docBlockBadges(b)}${escapeHtml(b.text || "")}</p>`;
  }
  if (b.type === "table") {
    const header = b.header || [];
    const rows = b.rows || [];
    const thead = `<thead><tr>${header.map((c) => `<th>${escapeHtml(c || "")}</th>`).join("")}</tr></thead>`;
    const body = rows.slice(1).map((r) => `<tr>${(r || []).map((c) => `<td>${escapeHtml(c || "")}</td>`).join("")}</tr>`).join("");
    return `<table class="doc-block" data-block-id="${escapeHtml(id)}" data-block-type="table">${docBlockBadges(b)}${thead}<tbody>${body}</tbody></table>`;
  }
  return `<p class="doc-block" data-block-id="${escapeHtml(id)}" data-block-type="${escapeHtml(b.type)}" data-block-text="${escapeHtml(b.text || "")}">${docBlockBadges(b)}${escapeHtml(b.text || "")}</p>`;
}

function renderDocListBlock(group, tag) {
  const items = group.map((b) => `<li class="doc-block" data-block-id="${escapeHtml(b.block_id)}" data-block-type="${escapeHtml(b.type)}" data-block-text="${escapeHtml(b.text || "")}">${escapeHtml(b.text || "")}</li>`).join("");
  return `<${tag} class="doc-list-group">${items}</${tag}>`;
}

function setDocBlockEditable(el) {
  el.setAttribute("contenteditable", "true");
  el.addEventListener("blur", () => saveDocBlockOnBlur(el));
}

function getDocBlockTextFromElement(el) {
  const type = el.getAttribute("data-block-type");
  if (type === "table") {
    const rows = [];
    el.querySelectorAll("thead tr, tbody tr").forEach((tr) => {
      const cells = [];
      tr.querySelectorAll("th, td").forEach((c) => {
        cells.push((c.textContent || "").trim());
      });
      rows.push(cells);
    });
    if (!rows.length) return "";
    const colCount = Math.max(...rows.map((r) => r.length));
    const normRows = rows.map((r) => {
      const out = r.slice();
      while (out.length < colCount) out.push("");
      return out;
    });
    const lines = [];
    lines.push("| " + normRows[0].join(" | ") + " |");
    lines.push("| " + normRows[0].map(() => "---").join(" | ") + " |");
    for (let i = 1; i < normRows.length; i++) {
      lines.push("| " + normRows[i].join(" | ") + " |");
    }
    return lines.join("\n");
  }
  return (el.innerText || el.textContent || "").trim();
}

async function saveDocBlockOnBlur(el) {
  const blockId = el.getAttribute("data-block-id");
  const original = el.getAttribute("data-block-text") || "";
  const current = getDocBlockTextFromElement(el);
  if (current === original || current === "") return;
  if (isAnythingRunning()) {
    appendLog("[WYSIWYG] 当前正运行流程，暂不写入块修改。");
    el.setAttribute("data-block-text", original);
    el.textContent = original;
    return;
  }
  el.setAttribute("data-block-text", current);
  appendLog(`[WYSIWYG] 块 ${blockId} 已手动修改，正在保存并重建 Word。`);
  try {
    const response = await fetch("/api/final-doc/block-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block_id: blockId, new_text: current, instruction: "manual inline edit" }),
    });
    const data = await response.json();
    if (!data.ok) {
      appendLog("[WYSIWYG] 保存失败：" + (data.message || ""));
      alert(data.message || "保存失败");
      return;
    }
    pollRunningCompletionThenReload();
  } catch (e) {
    appendLog("[WYSIWYG] 保存失败：" + e);
    alert("保存失败：" + e);
  }
}

function pollRunningCompletionThenReload() {
  let count = 0;
  const timer = setInterval(() => {
    count++;
    if (count > 120) { clearInterval(timer); return; }
    if (!isAnythingRunning()) {
      clearInterval(timer);
      loadStatus();
      setTimeout(() => { if (detailTab === "doc") loadDocEditor(); }, 700);
      return;
    }
    if (count % 4 === 0) loadStatus();
  }, 1500);
}

function handleDocSelection(event) {
  const sel = window.getSelection();
  const selectionBar = document.getElementById("doc-selection-bar");
  if (!selectionBar) return;
  const text = (sel && sel.toString() || "").trim();
  if (!text) return;
  let node = sel.anchorNode;
  let blockEl = null;
  const root = event.currentTarget;
  while (node && node !== root) {
    if (node.nodeType === 1 && node.classList && node.classList.contains("doc-block")) {
      blockEl = node;
      break;
    }
    node = node.parentNode;
  }
  if (!blockEl) return;
  selectionBar.hidden = false;
  selectionBar.dataset.blockId = blockEl.getAttribute("data-block-id");
  selectionBar.dataset.selectedText = text;
  const quoted = document.getElementById("doc-selection-quoted");
  if (quoted) quoted.textContent = text;
  const inst = document.getElementById("doc-selection-instruction");
  if (inst) inst.value = "";
}

function closeDocSelectionBar() {
  const selectionBar = document.getElementById("doc-selection-bar");
  if (selectionBar) {
    selectionBar.hidden = true;
    selectionBar.dataset.blockId = "";
    selectionBar.dataset.selectedText = "";
  }
}

async function submitDocSelectionRewrite() {
  const selectionBar = document.getElementById("doc-selection-bar");
  if (!selectionBar) return;
  const blockId = selectionBar.dataset.blockId;
  const selectedText = selectionBar.dataset.selectedText;
  const inst = document.getElementById("doc-selection-instruction");
  const instruction = (inst && inst.value || "").trim();
  if (!blockId || !selectedText) {
    alert("请先在文档中选中要修改的文字。");
    return;
  }
  if (!instruction) {
    alert("请填写批注要求。");
    inst && inst.focus();
    return;
  }
  if (isAnythingRunning()) {
    alert("当前正运行流程，请稍后再 AI 改写。");
    return;
  }
  appendLog("[WYSIWYG] 调用 AI 改写选区：" + instruction);
  try {
    const response = await fetch("/api/final-doc/selection-rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block_id: blockId, selected_text: selectedText, instruction }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "AI 改写失败");
      return;
    }
    pendingDocEdit = {
      block_id: data.block_id,
      instruction: data.instruction,
      selected_text: data.selected_text,
      old_text: data.old_text,
      new_text: data.new_text,
      source: "ai_selection_rewrite",
    };
    renderDocPendingBar();
    closeDocSelectionBar();
    appendLog("[WYSIWYG] AI 已生成选区改写预览，请在中间确认。");
    pushChatMessage("assistant", `AI 已按你的批注生成选区改写预览（块 ${data.block_id}）。请在中间"文档编辑(Word)"里确认或放弃。`, []);
  } catch (e) {
    alert("AI 改写失败：" + e);
  }
}

function renderDocPendingBar() {
  const bar = document.getElementById("doc-pending-bar");
  if (!bar) return;
  document.querySelectorAll(".doc-block.is-pending").forEach((el) => el.classList.remove("is-pending"));
  if (!pendingDocEdit) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  bar.hidden = false;
  if (pendingDocEdit.kind === "chat_edit") {
    bar.innerHTML = `
      <div class="pending-text">全文改写预览已生成</div>
      <div class="doc-pending-actions">
        <button class="primary" onclick="openDocChatPreviewModal()">查看预览并确认</button>
        <button class="danger" onclick="discardDocChatApply()">放弃</button>
      </div>
    `;
    return;
  }
  const oldText = pendingDocEdit.old_text || "";
  const newText = pendingDocEdit.new_text || "";
  bar.innerHTML = `
    <div class="pending-text">块 ${escapeHtml(pendingDocEdit.block_id || "")} 的 AI 改写预览已生成（命中的批注：${escapeHtml(pendingDocEdit.instruction || "")}）</div>
    <div class="doc-pending-diff">
      <div class="col"><h6>改写前</h6>${escapeHtml(oldText)}</div>
      <div class="col new"><h6>改写后</h6>${escapeHtml(newText)}</div>
    </div>
    <div class="doc-pending-actions">
      <button class="primary" onclick="confirmDocSelectionApply()">确认改写并重建 Word</button>
      <button class="danger" onclick="discardDocSelectionApply()">放弃</button>
    </div>
  `;
  const page = document.getElementById("doc-editor-page");
  const target = page && page.querySelector(`.doc-block[data-block-id="${cssEscape(pendingDocEdit.block_id || "")}"]`);
  if (target) target.classList.add("is-pending");
}

async function confirmDocSelectionApply() {
  if (isAnythingRunning()) { alert("当前正运行流程，请稍后再写入。"); return; }
  if (!pendingDocEdit || !pendingDocEdit.block_id) { alert("没有待确认的选区改写。"); return; }
  try {
    const response = await fetch("/api/final-doc/selection-apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_text: pendingDocEdit.new_text || "" }),
    });
    const data = await response.json();
    if (!data.ok) { alert(data.message || "写入失败"); return; }
    pendingDocEdit = null;
    appendLog("[WYSIWYG] 已确认写入选区改写，正在重建 Word。");
    pushChatMessage("assistant", "已确认写入并开始重新生成 Word。", []);
    renderDocPendingBar();
    pollRunningCompletionThenReload();
  } catch (e) {
    alert("写入失败：" + e);
  }
}

async function discardDocSelectionApply() {
  try { await fetch("/api/final-doc/selection-discard", { method: "POST" }); } catch (_) {}
  pendingDocEdit = null;
  renderDocPendingBar();
  appendLog("[WYSIWYG] 已放弃选区改写。");
}

function openDocChatEdit() {
  docChatEditState = { instruction: "", preview: "", loading: false };
  showDocChatEditModal(docChatEditState);
}

function showDocChatEditModal(state) {
  const existingOverlay = document.getElementById("doc-chat-edit-overlay");
  if (existingOverlay) existingOverlay.remove();
  const overlay = document.createElement("div");
  overlay.className = "doc-chat-edit-overlay";
  overlay.id = "doc-chat-edit-overlay";
  const previewHtml = state.preview
    ? `<h6>改写后预览（${state.preview.length} 字符）</h6><div class="doc-chat-edit-preview">${escapeHtml(state.preview.slice(0, 8000))}${state.preview.length > 8000 ? "...(已截断显示)" : ""}</div>`
    : "";
  const actionHtml = state.preview
    ? "<button class='primary' onclick='confirmDocChatApply()'>确认写入并重建 Word</button><button class='danger' onclick='discardDocChatApply()'>放弃</button>"
    : "<button class='primary' onclick='submitDocChatEdit()'>让 AI 生成预览</button>";
  overlay.innerHTML = `
    <div class="doc-chat-edit-modal" id="doc-chat-edit-modal">
      <h3>AI 全文改写</h3>
      <p class="meta-label">基于 final.md 生成改写预览，确认后会写入并自动重建 Word。</p>
      <textarea id="doc-chat-edit-inst" placeholder="例如：把第三章改成更正式的语气；把评分点响应明确到对应列表里；删除空话套话；统一标题层级">${escapeHtml(state.instruction || "")}</textarea>
      ${previewHtml}
      ${state.loading ? "<div class='detail-empty small'>正在调用 AI 改写...</div>" : ""}
      <div class="doc-chat-edit-actions">
        ${actionHtml}
        <button onclick='closeDocChatEditModal()'>关闭</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay && !state.loading) closeDocChatEditModal();
  });
}

function closeDocChatEditModal() {
  const overlay = document.getElementById("doc-chat-edit-overlay");
  if (overlay) overlay.remove();
}

async function submitDocChatEdit() {
  const instArea = document.getElementById("doc-chat-edit-inst");
  const instruction = (instArea && instArea.value || "").trim();
  if (!instruction) { alert("请填写改写要求。"); instArea && instArea.focus(); return; }
  if (isAnythingRunning()) { alert("当前正运行流程，请稍后再改写。"); return; }
  showDocChatEditModal({ instruction, preview: "", loading: true });
  try {
    const response = await fetch("/api/final-doc/chat-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    });
    const data = await response.json();
    if (!data.ok) {
      alert(data.message || "AI 改写失败");
      showDocChatEditModal({ instruction, preview: "", loading: false });
      return;
    }
    docChatEditState = { instruction, preview: data.new_md || "", loading: false };
    pendingDocEdit = {
      kind: "chat_edit",
      instruction,
      new_md: data.new_md || "",
      source: "ai_chat_edit",
    };
    showDocChatEditModal(docChatEditState);
    renderDocPendingBar();
    appendLog("[WYSIWYG] AI 已生成全文改写预览。");
  } catch (e) {
    alert("AI 改写失败：" + e);
    showDocChatEditModal({ instruction, preview: "", loading: false });
  }
}

async function confirmDocChatApply() {
  if (isAnythingRunning()) { alert("当前正运行流程，请稍后再写入。"); return; }
  const newMd = docChatEditState && docChatEditState.preview || (pendingDocEdit && pendingDocEdit.new_md) || "";
  if (!newMd) { alert("没有可写入的内容。"); return; }
  const instruction = (docChatEditState && docChatEditState.instruction) || (pendingDocEdit && pendingDocEdit.instruction) || "";
  try {
    const response = await fetch("/api/final-doc/chat-apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_md: newMd, instruction }),
    });
    const data = await response.json();
    if (!data.ok) { alert(data.message || "写入失败"); return; }
    pendingDocEdit = null;
    docChatEditState = null;
    closeDocChatEditModal();
    renderDocPendingBar();
    appendLog("[WYSIWYG] 全文改写已写入，开始重建 Word。");
    pushChatMessage("assistant", "全文改写已写入并开始重新生成 Word。", []);
    pollRunningCompletionThenReload();
  } catch (e) {
    alert("写入失败：" + e);
  }
}

async function discardDocChatApply() {
  try { await fetch("/api/final-doc/chat-discard", { method: "POST" }); } catch (_) {}
  pendingDocEdit = null;
  docChatEditState = null;
  closeDocChatEditModal();
  renderDocPendingBar();
  appendLog("[WYSIWYG] 已放弃全文改写。");
}

function openDocChatPreviewModal() {
  if (!pendingDocEdit || pendingDocEdit.kind !== "chat_edit") return;
  docChatEditState = { instruction: pendingDocEdit.instruction || "", preview: pendingDocEdit.new_md || "", loading: false };
  showDocChatEditModal(docChatEditState);
}
