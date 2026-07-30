<template>
  <section class="v3-workspace" :aria-busy="running || uploading">
    <header class="workspace-header">
      <div>
        <p class="eyebrow">标书编制工作台</p>
        <div class="title-row">
          <h1>{{ workspaceName }}</h1>
          <span class="status-pill" :class="`status-${planningStatus}`">
            {{ planningStatusLabel }}
          </span>
        </div>
        <p class="header-copy">上传招标与公司资料，先生成可追溯到评分点的章节目录草案。</p>
      </div>
      <div class="header-actions">
        <button class="btn" type="button" :disabled="loading || running" @click="refresh(true)">
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <path d="M20 11a8 8 0 1 0-2.34 5.66M20 4v7h-7" />
          </svg>
          {{ loading ? '刷新中' : '刷新状态' }}
        </button>
        <button
          v-if="deliveryReady"
          class="btn btn-primary"
          type="button"
          @click="download"
        >
          下载 Word
        </button>
      </div>
    </header>

    <!-- 实时步骤进度链 (Step Progress Tracker) -->
    <section v-if="topPipelineStages.length" class="pipeline-stepper-panel" aria-label="后台处理流程进度链">
      <div class="stepper-bar-header">
        <div class="stepper-bar-title">
          <p class="section-kicker">流水线进度</p>
          <h3>{{ topPipelineTitle }}（{{ topPipelineStages.length }} 步）</h3>
          <p class="pipeline-context-copy">{{ topPipelineDescription }}</p>
        </div>
        <div class="stepper-right-info">
          <span class="pipeline-state-pill" :class="`pipeline-state-${topPipelineStatus}`">
            {{ topPipelineStatusLabel }}
          </span>
          <button
            v-if="planningStatus !== 'confirmed' && hasTender"
            class="btn btn-primary"
            type="button"
            :disabled="outlineActionDisabled"
            @click="prepareOutline"
          >
            <span v-if="outlineBusy" class="spinner" aria-hidden="true" />
            {{ outlineBusy ? outlineRunningLabel : outlineActionLabel }}
          </button>
          <button
            v-else-if="planningStatus === 'confirmed'"
            class="btn btn-primary"
            type="button"
            :disabled="running || generationBusy"
            @click="runDocument"
          >
            {{ generationBusy ? '正在生成，不要重复提交' : (generation.status === 'failed' ? '重新生成完整标书' : '生成完整标书') }}
          </button>
          <button
            class="text-button log-toggle-btn"
            type="button"
            @click="activeTab = activeTab === 'pipeline' ? 'planning' : 'pipeline'"
          >
            {{ activeTab === 'pipeline' ? '返回结果主视窗' : '查看完整步骤产物明细 →' }}
          </button>
        </div>
      </div>

      <div v-if="showGenerationPipeline" class="pipeline-prerequisite-note">
        <strong>前置规划不会重复展示</strong>
        <span>
          已完成 {{ generationPrerequisiteCompleted }}/{{ generationPrerequisiteStages.length }} 个目录前置步骤，
          其中 {{ generationPrerequisiteReused }} 个直接复用；下方只展示正文生成与交付步骤。
        </span>
      </div>

      <ol class="stepper-bar-list" aria-label="处理步骤节点">
        <li
          v-for="(stage, index) in topPipelineStages"
          :key="stage.stage_id"
        >
          <button
            class="stepper-node"
            :class="[
              `stage-${stage.status}`,
              { selected: selectedDrawerStage?.stage_id === stage.stage_id, 'has-warning': stage.warning_count > 0 }
            ]"
            type="button"
            :aria-pressed="selectedDrawerStage?.stage_id === stage.stage_id"
            @click="openStageDrawer(stage)"
          >
            <div class="node-badge">
              <span v-if="stage.warning_count > 0">!</span>
              <span v-else-if="stage.status === 'succeeded' || stage.status === 'reused'">✓</span>
              <span v-else-if="stage.status === 'failed'">×</span>
              <span v-else-if="stage.status === 'running' || stage.status === 'queued'" class="spinner-dot" />
              <span v-else>{{ index + 1 }}</span>
            </div>
            <div class="node-content">
              <strong>{{ stage.label }}</strong>
              <small>{{ stage.warning_count > 0 ? '已带警告继续' : pipelineStageStatus(stage) }}</small>
              <small class="stage-operation">{{ pipelineStageOperation(stage) }}</small>
            </div>
            <span v-if="stage.llm_request_count > 0" class="node-req-tag">
              {{ stage.llm_request_count }}次 LLM 请求
            </span>
          </button>
        </li>
      </ol>
    </section>

    <!-- 步骤细节诊断 Drawer 抽屉/弹窗 -->
    <div v-if="selectedDrawerStage" class="stage-drawer-overlay" @click.self="closeStageDrawer">
      <aside
        class="stage-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="stage-drawer-heading"
      >
        <header class="drawer-header">
          <div>
            <p class="section-kicker">节点详情 · 可审计执行轨迹</p>
            <h3 id="stage-drawer-heading">{{ selectedDrawerStage.label }}</h3>
          </div>
          <button class="drawer-close-btn" type="button" aria-label="关闭步骤详情" @click="closeStageDrawer">
            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div class="drawer-body">
          <div class="drawer-status-box" :class="`status-${selectedDrawerStage.status}`">
            <strong>步骤状态：{{ pipelineStageStatus(selectedDrawerStage) }}</strong>
            <span>尝试 {{ selectedDrawerStage.attempt || 0 }} 次</span>
          </div>
          <dl
            v-if="selectedDrawerStage.started_at || selectedDrawerStage.completed_at"
            class="drawer-timestamps"
          >
            <div v-if="selectedDrawerStage.started_at">
              <dt>开始时间</dt>
              <dd>{{ formatTimestamp(selectedDrawerStage.started_at) }}</dd>
            </div>
            <div v-if="selectedDrawerStage.completed_at">
              <dt>完成时间</dt>
              <dd>{{ formatTimestamp(selectedDrawerStage.completed_at) }}</dd>
            </div>
          </dl>

          <section
            v-if="stageDetail?.current_writing"
            class="current-writing-card"
            :class="{
              'current-writing-paused': ['blocked_human', 'failed', 'paused'].includes(
                stageDetail.current_writing.unit_status
                  || selectedDrawerStage.status
                  || ''
              ) || ['model_output_invalid', 'research_blocked', 'paused', 'failed'].includes(
                stageDetail.current_writing.phase || ''
              )
            }"
            aria-live="polite"
          >
            <p class="section-kicker">实时写作位置</p>
            <strong>
              {{ writingPhaseLabel(stageDetail.current_writing.phase, stageDetail.current_writing.unit_status || selectedDrawerStage.status) }}：
              {{
                stageDetail.current_writing.chapter_title
                  || stageDetail.current_writing.unit_title
                  || '正在确定章节'
              }}
            </strong>
            <p>
              写作单元 {{ stageDetail.current_writing.unit_id || '—' }}
              <template v-if="stageDetail.current_writing.chapter_id">
                · 章节 {{ stageDetail.current_writing.chapter_id }}
              </template>
            </p>
            <p
              v-if="stageDetail.current_writing.error"
              class="current-writing-error"
            >
              {{ stageDetail.current_writing.error }}
            </p>
            <small v-if="stageDetail.current_writing.updated_at">
              最近更新：{{ formatTimestamp(stageDetail.current_writing.updated_at) }}
            </small>
          </section>
          <section
            v-else-if="selectedDrawerStage.stage_id === 'execute_content_plan' && selectedDrawerStage.status === 'running'"
            class="current-writing-card current-writing-pending"
            aria-live="polite"
          >
            <p class="section-kicker">实时写作位置</p>
            <strong>正在初始化写作单元…</strong>
            <p>写作器写入首个章节后会自动显示具体章节与当前阶段。</p>
          </section>

          <p v-if="stageDetailLoading" class="drawer-empty-hint">正在读取该步骤的完整详情…</p>
          <div v-else-if="stageDetailError" class="drawer-error-alert" role="alert">
            <strong>节点详情暂时无法读取</strong>
            <p>{{ stageDetailError }}</p>
          </div>

          <div v-if="stageDetail?.warning_count" class="drawer-warning-alert" role="status">
            <strong>已带 {{ stageDetail.warning_count }} 项风险继续</strong>
            <ul>
              <li v-for="warning in stageDetail.warnings" :key="`${warning.code}-${warning.message}`">
                <b>{{ warning.code }}</b>
                <span>{{ warning.message }}</span>
              </li>
            </ul>
          </div>

          <div v-if="selectedDrawerStage.status === 'failed' && selectedDrawerStage.error?.message" class="drawer-error-alert">
            <strong>错误明细：</strong>
            <p>{{ pipelineStageError(selectedDrawerStage) }}</p>
          </div>

          <section
            v-if="stageDetail?.research_trace?.length"
            class="drawer-trace-panel"
            aria-labelledby="research-trace-heading"
          >
            <header class="drawer-trace-heading">
              <div>
                <p class="section-kicker">章节写作中的判断与工具调用</p>
                <h4 id="research-trace-heading">Agent 执行轨迹</h4>
              </div>
              <span>{{ stageDetail.research_trace.length }} 个写作单元</span>
            </header>
            <p v-if="stageDetail.trace_disclosure" class="trace-disclosure">
              {{ stageDetail.trace_disclosure }}
            </p>

            <article
              v-for="trace in stageDetail.research_trace"
              :key="trace.decision_id || trace.unit_id"
              class="research-trace-card"
            >
              <header class="research-trace-summary">
                <div>
                  <span
                    class="trace-stage-label"
                    :class="`trace-unit-${trace.unit_status || 'unknown'}`"
                  >
                    {{ contentUnitTraceLabel(trace.unit_status) }}
                  </span>
                  <h5>{{ trace.chapter_titles?.join('、') || trace.unit_id }}</h5>
                  <small>
                    写作单元 {{ trace.unit_id || '—' }}
                    <template v-if="trace.unit_attempt"> · 第 {{ trace.unit_attempt }} 次执行</template>
                  </small>
                </div>
                <span
                  class="research-decision-pill"
                  :class="trace.needs_research ? 'decision-search' : 'decision-skip'"
                >
                  {{ trace.needs_research ? '需要联网' : '无需联网' }}
                </span>
              </header>

              <ol class="research-trace-steps">
                <li>
                  <span class="trace-step-index">1</span>
                  <div>
                    <strong>启动写作单元</strong>
                    <p>
                      包含章节：{{ trace.chapter_titles?.join('、') || '当前写作单元' }}
                    </p>
                    <small v-if="trace.created_at">
                      联网判断记录于 {{ formatTimestamp(trace.created_at) }}
                    </small>
                  </div>
                </li>
                <li>
                  <span class="trace-step-index">2</span>
                  <div>
                    <strong>判断是否需要联网搜索</strong>
                    <p>
                      <b>决策依据摘要：</b>
                      {{ trace.decision_summary || '未记录判断摘要。' }}
                    </p>
                    <small>
                      {{ researchStatusLabel(trace.decision_status) }}
                    </small>
                  </div>
                </li>
                <li v-if="trace.needs_research">
                  <span class="trace-step-index">3</span>
                  <div>
                    <strong>执行公开资料搜索</strong>
                    <div
                      v-for="query in trace.queries"
                      :key="query.query_id || query.question"
                      class="research-query-card"
                    >
                      <header>
                        <span>查询内容</span>
                        <em>{{ researchStatusLabel(query.status) }}</em>
                      </header>
                      <p>{{ query.question }}</p>
                      <small v-if="query.applicability">
                        用于：{{ query.applicability }}
                      </small>
                      <small v-if="query.batch_id">证据批次：{{ query.batch_id }}</small>
                      <ol v-if="query.attempts?.length" class="research-attempt-list">
                        <li
                          v-for="attempt in query.attempts"
                          :key="`${query.query_id}-${attempt.attempt}`"
                        >
                          <strong>第 {{ attempt.attempt }} 次</strong>
                          <span>{{ researchStatusLabel(attempt.status) }}</span>
                          <small>
                            来源 {{ attempt.source_count ?? 0 }} 个 ·
                            证据 {{ attempt.evidence_count ?? 0 }} 条 ·
                            {{ attempt.duration_ms ?? 0 }} ms
                          </small>
                          <p v-if="attempt.error">{{ attempt.error }}</p>
                        </li>
                      </ol>

                      <div v-if="query.results?.length" class="research-result-list">
                        <article
                          v-for="result in query.results"
                          :key="result.evidence_id || result.source_url"
                          class="research-result-card"
                          :class="{ used: result.used_in_bid }"
                        >
                          <header>
                            <a
                              v-if="result.source_url"
                              :href="result.source_url"
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {{ result.title || result.source_url }}
                            </a>
                            <strong v-else>{{ result.title || result.evidence_id }}</strong>
                            <span
                              :class="result.usage_status === 'used' ? 'result-used' : 'result-unused'"
                            >
                              {{ researchUsageLabel(result.usage_status) }}
                            </span>
                          </header>
                          <small>
                            {{ result.publisher || '公开来源' }}
                            <template v-if="result.evidence_id"> · {{ result.evidence_id }}</template>
                          </small>
                          <p v-if="result.answer_excerpt">
                            <b>检索回答摘要：</b>{{ result.answer_excerpt }}
                          </p>
                          <small v-if="result.used_in_chapters?.length" class="result-usage">
                            写入：
                            {{ result.used_in_chapters.map(item => item.chapter_title).join('、') }}
                          </small>
                        </article>
                      </div>
                      <p v-else class="trace-empty-result">
                        尚未取得可核验来源，或本次查询仍在执行。
                      </p>
                      <p v-if="query.error" class="trace-query-error">{{ query.error }}</p>
                    </div>
                  </div>
                </li>
                <li>
                  <span class="trace-step-index">{{ trace.needs_research ? 4 : 3 }}</span>
                  <div>
                    <strong>写入标书时采用哪些证据</strong>
                    <template v-if="trace.used_by_chapter?.length">
                      <p
                        v-for="usage in trace.used_by_chapter"
                        :key="usage.chapter_id"
                        class="trace-evidence-usage"
                      >
                        <b>{{ usage.chapter_title }}</b>
                        <span>{{ usage.evidence_ids.join('、') }}</span>
                      </p>
                    </template>
                    <p v-else>
                      {{
                        trace.needs_research
                          ? (
                            trace.unit_status === 'completed'
                              ? '本写作单元完成后未记录正文采用。'
                              : '采用情况待写作单元完成后确认。'
                          )
                          : '本单元未调用公开搜索。'
                      }}
                    </p>
                  </div>
                </li>
              </ol>
            </article>
          </section>

          <div v-if="selectedDrawerStage.llm_requests?.length" class="drawer-llm-list">
            <h4>大模型请求历史明细 ({{ selectedDrawerStage.llm_requests.length }} 次)</h4>
            <article
              v-for="request in selectedDrawerStage.llm_requests"
              :key="request.request_id"
              class="pipeline-llm-request"
            >
              <header>
                <strong>第 {{ request.request_index }} 次请求</strong>
                <span :class="`llm-request-${request.status}`">
                  {{ llmRequestStatus(request.status) }}
                </span>
              </header>
              <p>{{ llmRequestSummary(request) }}</p>
              <pre>{{ formatLlmRequest(request) }}</pre>
            </article>
          </div>
          <section v-if="stageDetail && Object.keys(stageDetail.details || {}).length" class="drawer-business-detail">
            <h4>阶段结果</h4>
            <dl>
              <template v-for="(value, key) in stageDetail.details" :key="key">
                <dt>{{ stageDetailLabel(key) }}</dt>
                <dd><pre v-if="typeof value === 'object'">{{ stageDetailValue(value) }}</pre><span v-else>{{ stageDetailValue(value) }}</span></dd>
              </template>
            </dl>
          </section>

          <section v-if="stageDetail?.items?.length" class="drawer-item-list">
            <h4>内容明细（{{ stageDetail.items.length }}）</h4>
            <article v-for="item in stageDetail.items" :key="item.id">
              <header><strong>{{ item.title }}</strong><span>{{ item.status || '已记录' }}</span></header>
              <p v-if="item.description">{{ item.description }}</p>
              <dl v-if="item.meta">
                <template v-for="(value, key) in item.meta" :key="key">
                  <dt>{{ stageDetailLabel(key) }}</dt><dd>{{ stageDetailValue(value) }}</dd>
                </template>
              </dl>
              <ol v-if="item.attempts?.length" class="research-attempt-list">
                <li v-for="attempt in item.attempts" :key="attempt.attempt">
                  <strong>第 {{ attempt.attempt }} 次</strong>
                  <span>{{ researchStatusLabel(attempt.status) }}</span>
                  <small>证据 {{ attempt.evidence_count ?? attempt.item_count ?? 0 }} 条 · 批次 {{ attempt.batch_id || '—' }}</small>
                  <p v-if="attempt.error">{{ attempt.error }}</p>
                </li>
              </ol>
            </article>
          </section>

          <p v-if="!stageDetailLoading && !stageDetailError && !stageDetail?.research_trace?.length && !stageDetail?.items?.length && !Object.keys(stageDetail?.details || {}).length && !selectedDrawerStage.llm_requests?.length" class="drawer-empty-hint">
            该步骤尚未产生可展示的业务明细。
          </p>
        </div>
      </aside>
    </div>

    <nav class="workflow-tabs" aria-label="标书处理步骤与视图">
      <button
        class="workflow-tab-btn"
        :class="{ active: activeTab === 'upload' }"
        type="button"
        @click="activeTab = 'upload'"
      >
        <span class="tab-step-num">01</span>
        <div class="tab-text">
          <strong>1. 输入资料与配置</strong>
          <small>{{ hasTender ? `${activeInputs.length} 个文件已登记` : '请上传招标文件' }}</small>
          <span class="tab-action-hint">操作：上传、替换或补充项目资料</span>
        </div>
      </button>

      <button
        class="workflow-tab-btn"
        :class="{ active: activeTab === 'planning', highlight: hasOutline }"
        type="button"
        @click="activeTab = 'planning'"
      >
        <span class="tab-step-num">02</span>
        <div class="tab-text">
          <strong>2. 审阅评分目录草案</strong>
          <small>{{ hasOutline ? `${planningView.summary.chapter_count} 章节 · ${planningView.summary.score_point_count} 评分点` : (outlineBusy ? '正在解析目录…' : '结果主视窗') }}</small>
          <span class="tab-action-hint">操作：生成目录、检查评分覆盖、确认目录</span>
        </div>
        <span v-if="hasOutline" class="tab-badge">结果就绪</span>
      </button>

      <button
        class="workflow-tab-btn"
        :class="{ active: activeTab === 'generation', highlight: generation.operation_id }"
        type="button"
        @click="activeTab = 'generation'"
      >
        <span class="tab-step-num">03</span>
        <div class="tab-text">
          <strong>3. 完整标书生成</strong>
          <small>{{ generationTabLabel }}</small>
          <span class="tab-action-hint">操作：整本生成或只生成所选章节、补充人工材料、预览并下载 Word</span>
        </div>
        <span v-if="generationBusy" class="tab-badge">生成中</span>
      </button>
    </nav>

    <div class="announcer" aria-live="polite" aria-atomic="true">
      <div v-if="error" class="message error" role="alert">
        <p><strong>处理失败：</strong>{{ error }}</p>
        <div v-if="errorDetails.length" class="error-detail-list">
          <strong>详细问题（{{ errorDetails.length }} 项）</strong>
          <ol>
            <li v-for="item in errorDetails" :key="`${item.title}:${item.description}`">
              <b>{{ item.title }}</b>
              <span>{{ item.description }}</span>
            </li>
          </ol>
        </div>
      </div>
      <p v-else-if="message" class="message success">
        {{ message }}
      </p>
    </div>

    <div v-show="activeTab === 'upload'" class="workspace-tab-view tab-upload">
      <div class="workspace-grid upload-grid-layout">
        <section class="panel upload-panel" aria-labelledby="upload-heading">
        <div class="panel-heading">
          <div>
            <p class="section-kicker">01 · 输入资料</p>
            <h2 id="upload-heading">上传项目文件</h2>
          </div>
          <span class="file-count">{{ activeInputs.length }} 个已登记文件</span>
        </div>
        <p class="panel-description">
          支持 PDF、DOCX、Markdown 和 TXT。完整招标文件中的评分章节也会参与评分解析。
        </p>

        <div class="upload-zones">
          <article
            v-for="zone in uploadZones"
            :key="zone.role"
            class="upload-zone"
            :class="{ required: zone.required }"
          >
            <div class="zone-heading">
              <div class="zone-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6M12 18v-6m-3 3 3-3 3 3" />
                </svg>
              </div>
              <div>
                <h3>
                  {{ zone.title }}
                  <span v-if="zone.required" class="required-mark">必传</span>
                </h3>
                <p>{{ zone.description }}</p>
              </div>
            </div>

            <input
              :id="`upload-${zone.role}`"
              class="visually-hidden"
              type="file"
              accept=".pdf,.docx,.md,.txt"
              multiple
              :disabled="uploading || running"
              @change="selectFiles(zone.role, $event)"
            />
            <label
              class="file-picker"
              :class="{ disabled: uploading || running }"
              :for="`upload-${zone.role}`"
            >
              选择一个或多个文件
            </label>

            <ul v-if="pendingUploads[zone.role].length" class="pending-files" aria-label="待上传文件">
              <li v-for="(file, index) in pendingUploads[zone.role]" :key="`${file.name}-${file.size}`">
                <span>
                  <strong>{{ file.name }}</strong>
                  <small>{{ formatBytes(file.size) }}</small>
                </span>
                <button
                  class="remove-file"
                  type="button"
                  :aria-label="`移除 ${file.name}`"
                  @click="removePendingFile(zone.role, index)"
                >
                  移除
                </button>
              </li>
            </ul>

            <button
              v-if="pendingUploads[zone.role].length"
              class="btn upload-button"
              type="button"
              :disabled="uploading || running"
              @click="uploadRole(zone.role)"
            >
              {{ uploadingRole === zone.role ? '正在上传…' : `上传 ${pendingUploads[zone.role].length} 个文件` }}
            </button>

            <ul v-if="inputsForRole(zone.role).length" class="registered-files" aria-label="已登记文件">
              <li v-for="item in inputsForRole(zone.role)" :key="item.input_id">
                <span class="file-state-dot" :class="sourceStatusClass(item)" aria-hidden="true" />
                <span class="registered-file-name">
                  <strong>{{ item.filename }}</strong>
                  <small>{{ sourceStatusLabel(item) }} · v{{ item.version }}</small>
                </span>
                <label v-if="deepSeekEligible(item)" class="research-check">
                  <input v-model="deepSeekAttachmentIds" type="checkbox" :value="item.input_id" />
                  允许研究时使用
                </label>
              </li>
            </ul>
          </article>
        </div>

        <p class="upload-note">
          扫描版公司附件会标记为“待 OCR”，不会阻断招标评分和目录主链；扫描版招标文件仍需先完成 OCR。
        </p>
      </section>

      <aside class="panel action-panel" aria-labelledby="action-heading">
        <div class="panel-heading compact">
          <div>
            <p class="section-kicker">02 · 解析规划</p>
            <h2 id="action-heading">生成评分目录</h2>
          </div>
        </div>

        <div class="analysis-summary">
          <div>
            <span>招标文件</span>
            <strong>{{ tenderInputs.length }}</strong>
          </div>
          <div>
            <span>公司资料</span>
            <strong>{{ companyInputs.length }}</strong>
          </div>
          <div>
            <span>评分点</span>
            <strong>{{ planningView.summary.score_point_count }}</strong>
          </div>
          <div>
            <span>章节节点</span>
            <strong>{{ planningView.summary.chapter_count }}</strong>
          </div>
        </div>

        <div v-if="analysisStale" class="inline-warning">
          {{ analysisStaleMessage }}
        </div>

        <button
          class="btn btn-primary primary-action"
          type="button"
          :disabled="outlineActionDisabled"
          @click="prepareOutline"
        >
          <span v-if="outlineBusy" class="spinner" aria-hidden="true" />
          {{ outlineBusy ? outlineRunningLabel : outlineActionLabel }}
        </button>
        <p v-if="!hasTender" class="action-hint">请先上传至少一份招标文件。</p>
        <p v-else class="action-hint">本操作只生成目录草案，不会开始写正文。</p>

        <div v-if="hasOutline" class="planning-actions">
          <button
            v-if="planningStatus === 'needs_human'"
            class="btn confirm-button"
            type="button"
            :disabled="running"
            @click="confirmPlanning"
          >
            确认当前目录
          </button>
          <button
            v-else-if="planningStatus === 'confirmed'"
            class="btn"
            type="button"
            :disabled="running || generationBusy"
            @click="runDocument"
          >
            {{ generationBusy || (running && runningAction === 'document') ? '正在生成，不要重复提交' : '生成完整标书' }}
          </button>
        </div>

        <dl class="delivery-list">
          <div>
            <dt>目录状态</dt>
            <dd>{{ planningStatusLabel }}</dd>
          </div>
          <div>
            <dt>目录模式</dt>
            <dd>{{ document.mode === 'template_strict' ? '严格模板' : '自动目录' }}</dd>
          </div>
          <div>
            <dt>交付状态</dt>
            <dd>{{ deliveryStatusLabel }}</dd>
          </div>
        </dl>
      </aside>
      </div>

      <div class="workspace-grid support-grid-layout">
        <section class="panel support-panel" aria-labelledby="evidence-heading">
          <div class="panel-heading compact">
            <div>
              <p class="section-kicker">05 · 资料补充</p>
              <h2 id="evidence-heading">证据缺口</h2>
            </div>
            <span class="file-count">{{ evidenceNeeds.length }} 项</span>
          </div>
          <ul class="evidence-list">
            <li v-for="need in evidenceNeeds" :key="need.need_id">
              <span>
                <strong>{{ need.question }}</strong>
                <small>
                  {{ need.status === 'satisfied'
                    ? '已满足'
                    : '待人工补充' }}
                </small>
              </span>
              <span class="manual-evidence-badge">请上传项目真实材料</span>
            </li>
            <li v-if="!evidenceNeeds.length" class="support-empty">
              当前没有已识别的证据缺口。
            </li>
          </ul>
          <p class="support-note">
            证据缺口必须由人工提供真实的企业、人员、业绩、资质或项目材料；系统不会联网补造或代替证明文件。
          </p>
        </section>

        <section class="panel support-panel" aria-labelledby="chat-heading">
          <div class="panel-heading compact">
            <div>
              <p class="section-kicker">06 · 协作</p>
              <h2 id="chat-heading">向标书 Agent 提问</h2>
            </div>
          </div>
          <p v-if="reply" class="assistant-reply">{{ reply }}</p>
          <label class="visually-hidden" for="workspace-question">输入问题</label>
          <textarea
            id="workspace-question"
            v-model="question"
            rows="4"
            placeholder="例如：哪些评分点还没有公司材料支撑？"
          />
          <button
            class="btn btn-primary chat-button"
            type="button"
            :disabled="asking || !question.trim()"
            @click="ask"
          >
            {{ asking ? '处理中…' : '发送问题' }}
          </button>
        </section>
      </div>
    </div>

    <div v-show="activeTab === 'generation'" class="workspace-tab-view tab-generation">
      <section class="panel generation-workbench" aria-labelledby="generation-heading">
        <div class="panel-heading generation-heading">
          <div>
            <p class="section-kicker">03 · 生成工作台</p>
            <h2 id="generation-heading">完整标书生成进度</h2>
            <p>{{ generationHeadline }}</p>
          </div>
        </div>

        <section class="writer-workspace" aria-label="标书实时写作工作区">
          <aside class="writer-outline-pane">
            <header>
              <p class="section-kicker">目录章节</p>
              <h3>标书目录</h3>
              <small>点击目录定位中间 Word 正文</small>
            </header>
            <nav v-if="visibleWriterOutline.length" class="writer-word-toc" aria-label="标书目录导航">
              <button
                v-for="(chapter, chapterIndex) in visibleWriterOutline"
                :key="chapter.chapter_id"
                class="writer-toc-item"
                :class="{ active: selectedWriterChapterId === chapter.chapter_id }"
                :aria-current="selectedWriterChapterId === chapter.chapter_id ? 'location' : undefined"
                :style="{ paddingLeft: `${4 + Math.max(0, (chapter.level || 1) - 1) * 14}px` }"
                type="button"
                @click="selectWriterChapter(chapter)"
              >
                <span
                  v-if="chapter.children?.length"
                  class="writer-toc-toggle"
                  role="button"
                  :aria-label="expandedWriterChapterIds.has(chapter.chapter_id) ? '折叠子目录' : '展开子目录'"
                  @click.stop="toggleWriterChapter(chapter.chapter_id)"
                >{{ expandedWriterChapterIds.has(chapter.chapter_id) ? '▾' : '▸' }}</span>
                <span v-else class="writer-toc-spacer" aria-hidden="true"></span>
                <span>{{ chapter.number || `${chapterIndex + 1}.` }}</span>
                <strong>{{ chapter.title }}</strong>
              </button>
            </nav>
            <p v-else class="generation-empty">确认目录并启动生成后，章节会在这里按写作进度出现。</p>
          </aside>

          <main class="writer-document-pane">
            <header>
              <div>
                <p class="section-kicker">实时 Word 草稿</p>
                <h3 :id="`writer-document-${selectedWriterChapterId}`">{{ selectedWriterChapter?.title || writerUnit?.current_chapter_title || writerUnit?.title || '等待开始写作' }}</h3>
              </div>
              <div class="writer-chapter-actions">
                <span class="writer-live-status">{{ writerPhaseText }}</span>
                <button
                  class="btn btn-primary"
                  type="button"
                  :disabled="running || generationBusy || !selectedWriterChapterId"
                  @click="runSelectedChapter"
                >
                  {{ runningAction === 'selected-chapter' ? '正在生成本章…' : '只生成本章' }}
                </button>
              </div>
            </header>
            <p v-if="writerUnit?.status === 'running'" class="writer-preview-notice">
              此处是已落盘的实时草稿检查点；正在生成的句子会在本章一次写作返回后显示，尚未通过全文质量门。
            </p>
            <article v-if="writerPreviewText" class="writer-word-canvas" aria-live="polite">
              <p v-for="(paragraph, index) in writerPreviewParagraphs" :key="index">{{ paragraph }}</p>
            </article>
            <p v-else class="writer-preview-empty">
              {{ writerUnit?.status === 'running' ? 'Agent 正在生成本章内容，等待首个草稿检查点…' : '选择已完成章节，或开始生成后查看实时草稿。' }}
            </p>
          </main>

          <aside class="writer-agent-pane">
            <header>
              <div>
                <p class="section-kicker">Agent 协作</p>
                <h3>材料与写入建议</h3>
              </div>
            </header>
            <p class="agent-disclosure">显示当前章节任务和材料写入建议；证据缺口只接受人工提供的真实资料。</p>
            <div class="agent-trace-feed">
              <article v-if="writerUnit?.current_chapter_title" class="agent-trace-item">
                <strong>当前任务</strong>
                <p>{{ writingPhaseLabel(writerUnit.progress_phase, writerUnit.status) }}：{{ writerUnit.current_chapter_title }}</p>
                <small v-if="writerUnit.error">{{ writerUnit.error }}</small>
              </article>
              <article v-for="call in writerResearchCalls" :key="call.decision_id" class="agent-trace-item">
                <strong>{{ call.needs_research ? '联网检索判断' : '无需联网' }}</strong>
                <p>{{ call.reason }}</p>
                <small v-for="query in call.queries || []" :key="query.query_id">搜索：{{ query.question }}（{{ researchStatusLabel(query.status) }}）</small>
                <small v-if="Object.values(call.used_evidence_by_chapter || {}).flat().length">已用于正文：{{ Object.values(call.used_evidence_by_chapter).flat().join('、') }}</small>
              </article>
              <p v-if="!writerUnit?.current_chapter_title && !writerResearchCalls.length" class="generation-empty">选择左侧章节后，可只生成该章；缺少的事实材料由人工补充。</p>
            </div>
            <div class="writer-chat-history" aria-live="polite">
              <article v-for="turn in writerChatTurns" :key="turn.id" :class="`writer-chat-${turn.role}`">
                <strong>{{ turn.role === 'user' ? '你的补充' : 'Agent 写入分析' }}</strong>
                <p>{{ turn.content }}</p>
              </article>
            </div>
            <label class="visually-hidden" for="writer-reference">补充写作资料</label>
            <textarea id="writer-reference" v-model="question" rows="3" placeholder="补充公司能力、项目情况或写作要求；Agent 会结合当前章节说明如何写入。" />
            <button class="btn btn-primary chat-button" type="button" :disabled="asking || !question.trim()" @click="ask">
              {{ asking ? '正在分析…' : '作为本章参考发送' }}
            </button>
          </aside>
        </section>

        <details class="generation-details">
          <summary>查看生成明细（章节与材料记录）</summary>
          <div class="generation-columns">
          <section class="generation-section">
            <header>
              <div>
                <p class="section-kicker">章节写作中的 Agent 判断与调用记录</p>
                <h3>章节写作外部资料检索记录</h3>
              </div>
              <span>
                来源 {{ generationResearch.source_count || 0 }} ·
                已发布 {{ generationResearch.published_count || 0 }} ·
                等待处理 {{ generationResearch.blocked_count || 0 }}
              </span>
            </header>
            <ul v-if="generationResearch.calls?.length" class="generation-research-list">
              <li v-for="call in generationResearch.calls" :key="call.decision_id">
                <strong>
                  {{ call.applicable_chapter_titles?.join('、') || call.unit_id }} ·
                  {{ call.needs_research ? 'Agent 决定调用' : 'Agent 决定不调用' }}
                </strong>
                <small>{{ call.reason }}</small>
                <small>{{ researchStatusLabel(call.decision_status) }}</small>
                <small v-if="call.runtime?.python_executable">
                  运行时：{{ call.runtime.python_executable }} ·
                  Playwright {{ call.runtime.playwright_installed ? '已安装' : '缺失' }} ·
                  Chromium {{ call.runtime.chromium_installed ? '可用' : '不可用' }}
                </small>
                <ol v-if="call.queries?.length">
                  <li v-for="query in call.queries" :key="query.query_id">
                    <small>查询：{{ query.question }}</small>
                    <small>
                      {{ researchStatusLabel(query.status) }}
                      <template v-if="query.batch_id"> · 证据批次 {{ query.batch_id }}</template>
                    </small>
                    <small
                      v-for="attempt in query.attempts"
                      :key="`${query.query_id}-${attempt.attempt}`"
                    >
                      第 {{ attempt.attempt }} 次 · {{ researchStatusLabel(attempt.status) }} ·
                      {{ attempt.duration_ms || 0 }} ms
                      <template v-if="attempt.error"> · {{ attempt.error }}</template>
                    </small>
                    <a
                      v-for="source in query.sources"
                      :key="source.evidence_id || source.source_url"
                      :href="source.source_url"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {{ source.title || source.publisher || source.source_url }}
                    </a>
                  </li>
                </ol>
                <small v-if="Object.keys(call.used_evidence_by_chapter || {}).length">
                  正文已使用证据：
                  {{ Object.values(call.used_evidence_by_chapter).flat().join('、') || '无' }}
                </small>
              </li>
            </ul>
            <p v-else class="generation-empty">章节写作开始后，这里会显示 Agent 判断、查询、来源校验和正文使用情况。</p>
          </section>

          <section class="generation-section content-progress-section">
            <header>
              <div>
                <p class="section-kicker">章节写作</p>
                <h3>正文单元</h3>
              </div>
              <span>{{ generationContent.completed_units || 0 }}/{{ generationContent.total_units || 0 }} 完成</span>
            </header>
            <p v-if="generationContent.stale_units" class="generation-error">
              {{ generationContent.stale_units }} 个旧正文已过期，必须重新生成后才能预览、整合或下载。
            </p>
            <progress
              :value="generationContent.completed_units || 0"
              :max="generationContent.total_units || 1"
            />
            <div v-if="generationContent.units?.length" class="content-unit-list">
              <button
                v-for="unit in generationContent.units"
                :key="unit.unit_id"
                class="content-unit-card"
                :class="[`content-unit-${unit.status}`, { selected: selectedContentUnitId === unit.unit_id }]"
                type="button"
                :disabled="unit.status !== 'completed' || unit.stale"
                @click="openContentUnit(unit)"
              >
                <span class="content-unit-status">{{ contentUnitStatusLabel(unit.status) }}</span>
                <strong>{{ unit.title }}</strong>
                <small>{{ unit.character_count || 0 }} 字 · {{ unit.block_count || 0 }} 块</small>
                <p v-if="unit.preview">{{ unit.preview }}</p>
                <em v-if="unit.stale_reason">{{ unit.stale_reason }}</em>
                <em v-else-if="unit.error">{{ unit.error }}</em>
              </button>
            </div>
          </section>
          </div>

          <section v-if="selectedContentUnitId" class="content-unit-detail">
          <header>
            <div>
              <p class="section-kicker">章节全文</p>
              <h3>{{ selectedContentUnitTitle }}</h3>
            </div>
            <button class="text-button" type="button" @click="closeContentUnit">关闭</button>
          </header>
          <p v-if="contentUnitLoading" class="generation-empty">正在读取章节正文…</p>
          <template v-else-if="contentUnitDetail">
            <article class="content-unit-blocks">
              <div v-for="block in contentUnitDetail.blocks" :key="block.block_id">
                <p>{{ block.content }}</p>
              </div>
            </article>
            <aside v-if="contentUnitDetail.sources?.length" class="content-unit-sources">
              <h4>正文实际使用的公开来源</h4>
              <ul>
                <li v-for="source in contentUnitDetail.sources" :key="source.evidence_id || source.source_url">
                  <a :href="source.source_url" target="_blank" rel="noopener noreferrer">
                    {{ source.title || source.publisher || source.source_url }}
                  </a>
                </li>
              </ul>
            </aside>
          </template>
          </section>
        </details>

        <section class="document-preview-panel" aria-labelledby="document-preview-heading">
          <header class="document-preview-header">
            <div>
              <p class="section-kicker">完整标书</p>
              <h3 id="document-preview-heading">完整标书预览</h3>
              <p>在同一阅读区切换 Markdown 或 Word 排版，目录用于快速定位章节。</p>
            </div>
            <div class="document-preview-actions">
              <div class="preview-mode-switch" role="group" aria-label="预览格式">
                <button type="button" :class="{ active: previewMode === 'markdown' }" @click="previewMode = 'markdown'">Markdown</button>
                <button type="button" :class="{ active: previewMode === 'word' }" @click="previewMode = 'word'">Word 预览</button>
              </div>
              <button class="btn" type="button" :disabled="documentPreviewLoading" @click="loadDocumentPreview(true)">
                {{ documentPreviewLoading ? '读取中…' : '刷新预览' }}
              </button>
              <button class="btn btn-primary" type="button" :disabled="!deliveryReady || !documentPreview" @click="download">
                下载 Word
              </button>
            </div>
          </header>

          <div v-if="documentPreview?.warning_count" class="document-risk-banner" role="status">
            <div>
              <strong>本标书带 {{ documentPreview.warning_count }} 项风险生成</strong>
              <span>可继续预览和下载，请在正式交付前复核来源与覆盖问题。</span>
            </div>
            <ul>
              <li v-for="warning in documentPreview.warnings" :key="`${warning.stage_id}-${warning.code}-${warning.message}`">
                <button type="button" @click="openWarningStage(warning)">
                  {{ warning.message || warning.code || '查看风险详情' }}
                </button>
              </li>
            </ul>
          </div>
          <p v-if="documentPreviewLoading" class="document-preview-state">正在读取完整标书…</p>
          <div v-else-if="documentPreview" class="document-reader">
            <nav class="document-toc" aria-label="标书目录">
              <strong>目录</strong>
              <button
                v-for="entry in documentPreview.toc"
                :key="entry.id"
                type="button"
                :class="{ active: selectedPreviewSectionId === entry.id }"
                :style="{ paddingLeft: `${12 + Math.max(0, entry.level - 1) * 14}px` }"
                @click="selectPreviewSection(entry.id)"
              >
                <span>{{ entry.title }}</span>
                <small v-if="entry.content_policy === 'deferred_title_only'">
                  仅标题
                </small>
              </button>
            </nav>
            <pre v-if="previewMode === 'markdown'" class="markdown-preview-canvas" aria-label="Markdown 标书正文">{{ documentPreviewMarkdown }}</pre>
            <article v-else class="word-preview-canvas" aria-label="Word 式标书正文">
              <template v-for="block in documentPreview.blocks" :key="block.id">
                <component
                  :is="`h${Math.min(block.level || 1, 6)}`"
                  v-if="block.type === 'heading'"
                  :id="`doc-preview-${block.id}`"
                  :class="`word-heading-${block.level || 1}`"
                >
                  {{ block.text }}
                </component>
                <p v-else-if="block.type === 'paragraph'">{{ block.text }}</p>
                <ul v-else-if="block.type === 'list'">
                  <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
                </ul>
                <div v-else-if="block.type === 'table'" class="word-table-wrap">
                  <table>
                    <thead><tr><th v-for="(cell, cellIndex) in block.rows[0]" :key="cellIndex">{{ cell }}</th></tr></thead>
                    <tbody>
                      <tr v-for="(row, rowIndex) in block.rows.slice(1)" :key="rowIndex">
                        <td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
            </article>
          </div>
          <div v-else class="document-preview-state">
            <p>{{ documentPreviewError || 'Word 渲染完成后将在这里显示完整标书。' }}</p>
          </div>
        </section>
      </section>
    </div>

    <!-- 03 · 拆分过程与中间产物 (诊断与日志视图) -->
    <div v-show="activeTab === 'pipeline'" class="workspace-tab-view tab-pipeline">
      <section class="panel pipeline-panel" aria-labelledby="pipeline-heading">
        <div class="panel-heading pipeline-heading">
          <div>
            <p class="section-kicker">03 · 拆分过程</p>
            <h2 id="pipeline-heading">评分目录生成过程与中间产物</h2>
            <p class="panel-description">
              页面每 5 秒读取后台阶段记录；程序审核提示只标记需复核，不会阻塞后续目录生成。
            </p>
          </div>
          <span class="pipeline-state" :class="`pipeline-state-${pipelineStatus}`">
            {{ pipelineStatusLabel }}
          </span>
        </div>

        <div
          v-if="pipelineWarnings.length"
          class="inline-warning pipeline-audit-warning"
          role="status"
        >
          <strong>程序审核提示（不阻塞后续流程）</strong>
          <ul>
            <li v-for="warning in pipelineWarnings" :key="warning">{{ warning }}</li>
          </ul>
        </div>

        <ol class="pipeline-stage-list" aria-label="目录生成步骤">
          <li
            v-for="(stage, index) in pipelineStages"
            :key="stage.stage_id"
            class="pipeline-stage"
            :class="`pipeline-stage-${stage.status}`"
          >
            <span class="pipeline-stage-index">
              {{ stage.status === 'succeeded' || stage.status === 'reused' ? '✓' : index + 1 }}
            </span>
            <div class="pipeline-stage-body">
              <strong>{{ stage.label }}</strong>
              <small>{{ pipelineStageStatus(stage) }}</small>
              <small class="pipeline-llm-count">
                大模型请求 {{ stage.llm_request_count }} 次
              </small>
              <em v-if="stage.status === 'failed' && stage.error?.message">
                {{ pipelineStageError(stage) }}
              </em>
              <details v-if="stage.llm_requests?.length" class="pipeline-llm-requests">
                <summary>查看每次请求参数</summary>
                <article
                  v-for="request in stage.llm_requests"
                  :key="request.request_id"
                  class="pipeline-llm-request"
                >
                  <header>
                    <strong>第 {{ request.request_index }} 次请求</strong>
                    <span :class="`llm-request-${request.status}`">
                      {{ llmRequestStatus(request.status) }}
                    </span>
                  </header>
                  <p>{{ llmRequestSummary(request) }}</p>
                  <pre>{{ formatLlmRequest(request) }}</pre>
                </article>
              </details>
            </div>
          </li>
        </ol>

        <div class="pipeline-products">
          <div class="subpanel-title">
            <h3>步骤产物</h3>
            <span>{{ pipelineProducts.length }} 项</span>
          </div>
          <div v-if="pipelineProducts.length" class="pipeline-product-flow">
            <nav class="pipeline-product-nav" aria-label="选择要查看的步骤产物">
              <button
              v-for="product in pipelineProducts"
              :key="product.kind"
              class="pipeline-product-selector"
              :class="{
                active: activePipelineProduct?.kind === product.kind,
                outdated: product.status === 'outdated',
                warning: product.status === 'warning',
              }"
              type="button"
              :aria-current="activePipelineProduct?.kind === product.kind ? 'step' : undefined"
              @click="selectedPipelineProductKind = product.kind"
            >
              <span class="pipeline-product-selector-copy">
                <strong>{{ product.label || product.kind }}</strong>
                <small>{{ pipelineProductSummary(product) }}</small>
              </span>
              <em>{{ pipelineProductStatusLabel(product) }}</em>
            </button>
            </nav>

            <article
              v-if="activePipelineProduct"
              class="pipeline-product-detail"
              :class="{
                outdated: activePipelineProduct.status === 'outdated',
                warning: activePipelineProduct.status === 'warning',
              }"
            >
              <header>
                <div>
                  <p class="section-kicker">当前查看</p>
                  <h4>{{ activePipelineProduct.label || activePipelineProduct.kind }}</h4>
                </div>
                <span>{{ pipelineProductStatusLabel(activePipelineProduct) }}</span>
              </header>
              <p class="pipeline-product-summary">{{ pipelineProductSummary(activePipelineProduct) }}</p>
              <div
                v-if="pipelineProductItems(activePipelineProduct).length"
                class="pipeline-product-content"
              >
                <p class="pipeline-product-content-label">
                  内容明细 · 显示 {{ previewPipelineProductItems(activePipelineProduct).length }} /
                  {{ pipelineProductItems(activePipelineProduct).length }} 项
                </p>
                <details
                  v-for="(item, index) in previewPipelineProductItems(activePipelineProduct)"
                  :key="pipelineProductItemKey(item, index)"
                  class="pipeline-product-record"
                >
                  <summary>
                    <span>
                      <strong>{{ pipelineProductItemTitle(item, index) }}</strong>
                    </span>
                    <small>{{ pipelineProductItemMeta(item) }}</small>
                  </summary>
                  <dl v-if="pipelineProductItemFields(item).length" class="pipeline-product-fields">
                    <div
                      v-for="field in pipelineProductItemFields(item)"
                      :key="field.key"
                    >
                      <dt>{{ field.label }}</dt>
                      <dd>{{ field.value }}</dd>
                    </div>
                  </dl>
                </details>
                <details
                  v-if="remainingPipelineProductItems(activePipelineProduct).length"
                  class="pipeline-product-more"
                >
                  <summary>
                    查看其余 {{ remainingPipelineProductItems(activePipelineProduct).length }} 项
                  </summary>
                  <details
                    v-for="(item, index) in remainingPipelineProductItems(activePipelineProduct)"
                    :key="pipelineProductItemKey(item, index + previewPipelineProductItems(activePipelineProduct).length)"
                    class="pipeline-product-record"
                  >
                    <summary>
                      <span>
                        <strong>
                          {{ pipelineProductItemTitle(item, index + previewPipelineProductItems(activePipelineProduct).length) }}
                        </strong>
                      </span>
                      <small>{{ pipelineProductItemMeta(item) }}</small>
                    </summary>
                    <dl v-if="pipelineProductItemFields(item).length" class="pipeline-product-fields">
                      <div
                        v-for="field in pipelineProductItemFields(item)"
                        :key="field.key"
                      >
                        <dt>{{ field.label }}</dt>
                        <dd>{{ field.value }}</dd>
                      </div>
                    </dl>
                  </details>
                </details>
              </div>
            </article>
          </div>
          <p v-else class="pipeline-empty">任务开始后，各阶段产物会依次显示在这里。</p>
        </div>
      </section>
    </div>

    <!-- 02 · 审阅目录 (结果主视窗) -->
    <div v-show="activeTab === 'planning'" class="workspace-tab-view tab-planning">
      <div class="planning-sticky-bar">
        <div class="sticky-bar-info">
          <span class="status-pill" :class="`status-${planningStatus}`">
            {{ planningStatusLabel }}
          </span>
          <div v-if="hasScorePoints" class="sticky-stats">
            <span><strong>{{ formatPoints(planningView.summary.total_points) }}</strong> 分</span>
            <span><strong>{{ planningView.summary.score_point_count }}</strong> 评分点</span>
            <span><strong>{{ planningView.summary.chapter_count }}</strong> 章节</span>
            <span :class="{ danger: planningView.summary.uncovered_response_unit_count > 0 }">
              <strong>{{ planningView.summary.uncovered_response_unit_count }}</strong> 未覆盖
            </span>
          </div>
        </div>

        <div class="sticky-bar-actions">
          <button
            v-if="!hasOutline && hasTender"
            class="btn btn-primary"
            type="button"
            :disabled="outlineActionDisabled"
            @click="prepareOutline"
          >
            <span v-if="outlineBusy" class="spinner" aria-hidden="true" />
            {{ outlineBusy ? outlineRunningLabel : outlineActionLabel }}
          </button>
          <template v-if="hasOutline">
            <button
              v-if="planningStatus === 'needs_human'"
              class="btn btn-primary"
              type="button"
              :disabled="running"
              @click="confirmPlanning"
            >
              确认当前目录
            </button>
            <button
              v-else-if="planningStatus === 'confirmed'"
              class="btn btn-primary"
              type="button"
              :disabled="running || generationBusy"
              @click="runDocument"
            >
              {{ generationBusy || (running && runningAction === 'document') ? '正在生成，不要重复提交' : '生成完整标书' }}
            </button>
            <button
              v-if="deliveryReady"
              class="btn btn-success"
              type="button"
              @click="download"
            >
              下载 Word 交付件
            </button>
          </template>
        </div>
      </div>

      <section class="panel planning-panel" aria-labelledby="planning-heading">
        <div class="panel-heading planning-heading">
          <div>
            <p class="section-kicker">04 · 审阅目录</p>
            <h2 id="planning-heading">评分点与章节目录草案</h2>
            <p class="panel-description">
              章节直接绑定评分响应任务、满分条件和招标需求，可逐项核验目录覆盖。
            </p>
          </div>
          <div v-if="hasScorePoints" class="planning-metrics" aria-label="目录覆盖指标">
            <span><strong>{{ formatPoints(planningView.summary.total_points) }}</strong> 总分</span>
            <span><strong>{{ planningView.summary.score_point_count }}</strong> 评分点</span>
            <span><strong>{{ planningView.summary.covered_response_unit_count }}</strong> 响应任务已覆盖</span>
            <span :class="{ danger: planningView.summary.uncovered_response_unit_count > 0 }">
              <strong>{{ planningView.summary.uncovered_response_unit_count }}</strong> 响应任务未覆盖
            </span>
          </div>
        </div>

        <div v-if="pipelineStatus === 'failed' && !hasOutline" class="planning-failed-box">
          <div class="failed-icon">⚠️</div>
          <h3>评分目录生成中断</h3>
          <p class="failed-msg">{{ error || '后台大模型拆分响应未成功完成，已暂停生成目录。' }}</p>
          <div class="failed-actions">
            <button
              class="btn btn-primary"
              type="button"
              :disabled="outlineActionDisabled"
              @click="prepareOutline"
            >
              重新解析生成目录
            </button>
            <button
              class="btn"
              type="button"
              @click="activeTab = 'pipeline'"
            >
              查看 03·拆分过程 与排查日志 →
            </button>
          </div>
        </div>

        <div v-else-if="!hasScorePoints && !hasOutline" class="planning-empty">
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4v15.5A2.5 2.5 0 0 0 6.5 22H20V4z" />
            <path d="M8 8h8M8 12h5" />
          </svg>
          <h3>{{ analysisStale ? '当前没有可用目录' : '还没有目录草案' }}</h3>
          <p>
            {{
              analysisStale
                ? '旧评分点和目录已隐藏；请修正解析问题后重新生成。'
                : '上传文件后点击“解析评分点并生成目录”，结果会在这里持久化展示。'
            }}
          </p>
        </div>

        <div v-else-if="!hasScorePoints" class="zero-score-warning" role="alert">
          <strong>没有识别到评分点。</strong>
          当前结果不能视为“按评分点拆分”的目录。请检查招标文件是否包含评分办法、评分表或分值条款后重试。
        </div>

        <div v-else class="planning-content">
          <details class="score-filter-panel">
            <summary>
              <span>
                <strong>按评分点核验目录</strong>
                <small>选择评分点后，仅高亮对应的章节与满分条件。</small>
              </span>
              <b>{{ planningView.score_points.length }} 项评分点</b>
            </summary>
            <aside class="score-list" aria-label="评分点列表">
            <div class="subpanel-title">
              <h3>评分点</h3>
              <button
                v-if="selectedScoreId"
                class="text-button"
                type="button"
                @click="selectedScoreId = ''"
              >
                清除筛选
              </button>
            </div>
            <article
              v-for="point in planningView.score_points"
              :key="point.score_point_id"
              class="score-point-card"
              :class="{ selected: selectedScoreId === point.score_point_id }"
            >
              <button
                class="score-item"
                :class="{ selected: selectedScoreId === point.score_point_id }"
                type="button"
                :aria-pressed="selectedScoreId === point.score_point_id"
                @click="toggleScore(point.score_point_id)"
              >
                <span class="score-item-heading">
                  <strong>{{ point.title }}</strong>
                  <b>{{ scorePointValue(point) }}</b>
                </span>
                <span class="score-criterion">{{ point.criterion }}</span>
                <span class="score-meta">
                  {{ responseDepthLabel(point.response_depth) }}
                  <em v-if="point.review_status !== 'confirmed'">需复核</em>
                </span>
              </button>
              <details
                v-if="point.score_conditions?.length"
                class="condition-trace"
                :open="selectedScoreId === point.score_point_id"
              >
                <summary>
                  查看 {{ point.score_conditions.length }} 个满分条件与落位
                </summary>
                <div class="condition-list">
                  <article
                    v-for="condition in point.score_conditions"
                    :key="condition.condition_id"
                    class="condition-card"
                  >
                    <header>
                      <code>{{ condition.condition_id }}</code>
                      <span :class="`condition-role role-${condition.condition_role}`">
                        {{ conditionRoleLabel(condition.condition_role) }}
                      </span>
                    </header>
                    <strong>{{ condition.normalized_condition || condition.text }}</strong>
                    <p v-if="condition.text" class="condition-raw">
                      原始拆解：{{ condition.text }}
                    </p>
                    <blockquote>{{ condition.source_excerpt }}</blockquote>
                    <p class="source-location">
                      <span>来源</span>
                      {{ condition.source_location.label }}
                      <code v-if="condition.source_location.chunk_id">
                        {{ condition.source_location.chunk_id }}
                      </code>
                    </p>
                    <dl class="trace-relations">
                      <div>
                        <dt>响应任务</dt>
                        <dd v-if="condition.response_units.length">
                          <span
                            v-for="unit in condition.response_units"
                            :key="unit.unit_id"
                          >
                            <code>{{ unit.unit_id }}</code>
                            {{ unit.title }}
                          </span>
                        </dd>
                        <dd v-else class="trace-missing">未绑定 ScoreResponseUnit</dd>
                      </div>
                      <div>
                        <dt>最终落位</dt>
                        <dd v-if="condition.destinations.length">
                          <a
                            v-for="destination in condition.destinations"
                            :key="traceDestinationKey(destination)"
                            :href="traceDestinationHref(destination)"
                            @click="destination.type === 'chapter' && expandChapterPath(destination.chapter_id)"
                          >
                            {{ traceDestinationLabel(destination) }}
                          </a>
                        </dd>
                        <dd v-else class="trace-missing">尚未绑定章节或全文质量门</dd>
                      </div>
                    </dl>
                  </article>
                </div>
              </details>
            </article>
            </aside>
          </details>

          <div class="outline-list" aria-label="章节目录">
            <div class="subpanel-title">
              <h3>章节目录</h3>
              <div class="outline-actions">
                <span v-if="selectedScoreId">已高亮覆盖所选评分点的章节</span>
                <button
                  v-if="hasCollapsibleChapters"
                  class="text-button"
                  type="button"
                  @click="expandAllChapters"
                >
                  全部展开
                </button>
                <button
                  v-if="hasCollapsibleChapters"
                  class="text-button"
                  type="button"
                  @click="collapseAllChapters"
                >
                  全部收起
                </button>
              </div>
            </div>
            <article
              v-for="chapter in visibleOutline"
              :key="chapter.chapter_id"
              :id="`chapter-${chapter.chapter_id}`"
              class="chapter-row"
              :class="{
                highlighted: selectedScoreId && chapter.score_point_ids.includes(selectedScoreId),
                dimmed: selectedScoreId && !chapter.score_point_ids.includes(selectedScoreId),
              }"
              :style="{ '--chapter-depth': chapter.depth - 1 }"
            >
              <span class="chapter-number">{{ chapter.number }}</span>
              <div class="chapter-body">
                <div class="chapter-title-row">
                  <button
                    v-if="chapter.children?.length"
                    class="chapter-heading-control chapter-heading-toggle"
                    type="button"
                    :aria-expanded="isChapterExpanded(chapter.chapter_id)"
                    :aria-label="`${isChapterExpanded(chapter.chapter_id) ? '收起' : '展开'} ${chapter.title} 的子章节`"
                    @click="toggleChapter(chapter.chapter_id)"
                  >
                    <span class="chapter-disclosure" aria-hidden="true">›</span>
                    <h4>{{ chapter.title }}</h4>
                  </button>
                  <div v-else class="chapter-heading-control">
                    <span class="chapter-disclosure-spacer" aria-hidden="true" />
                    <h4>{{ chapter.title }}</h4>
                  </div>
                  <span v-if="chapter.score_point_ids.length" class="coverage-count">
                    覆盖 {{ chapter.score_point_ids.length }} 项
                  </span>
                </div>
                <p>{{ chapter.purpose }}</p>
                <div v-if="chapter.direct_score_points.length" class="chapter-scores">
                  <button
                    v-for="point in chapter.direct_score_points"
                    :key="point.score_point_id"
                    type="button"
                    :class="{ active: selectedScoreId === point.score_point_id }"
                    :aria-pressed="selectedScoreId === point.score_point_id"
                    @click="toggleScore(point.score_point_id)"
                  >
                    {{ point.title }} · {{ scorePointValue(point) }}
                  </button>
                </div>
                <ul v-if="chapter.writing_objectives?.length" class="chapter-objectives">
                  <li v-for="objective in chapter.writing_objectives.slice(0, 2)" :key="objective">
                    {{ objective }}
                  </li>
                </ul>
                <div v-if="chapter.score_conditions.length" class="chapter-conditions">
                  <strong>满分条件</strong>
                  <span
                    v-for="condition in chapter.score_conditions"
                    :key="condition.condition_id"
                  >
                    <code>{{ condition.condition_id }}</code>
                    {{ condition.normalized_condition || condition.text }}
                  </span>
                </div>
                <details v-if="chapter.requirements.length" class="chapter-requirements">
                  <summary>关联需求原文 · {{ chapter.requirements.length }} 项</summary>
                  <article
                    v-for="requirement in chapter.requirements"
                    :key="requirement.requirement_id"
                  >
                    <header>
                      <code>{{ requirement.requirement_id }}</code>
                      <small>{{ requirement.source_location.label }}</small>
                    </header>
                    <p>
                      {{
                        requirement.original_text
                          || requirement.normalized_requirement
                          || '未找到关联需求原文'
                      }}
                    </p>
                  </article>
                </details>
              </div>
            </article>
          </div>
        </div>

        <section
          v-if="planningView.quality_gates.length"
          class="quality-gate-section"
          aria-labelledby="quality-gate-heading"
        >
          <div class="subpanel-title">
            <div>
              <p class="section-kicker">不生成机械章节</p>
              <h3 id="quality-gate-heading">全文质量门</h3>
            </div>
            <span>{{ planningView.quality_gates.length }} 项</span>
          </div>
          <div class="quality-gate-list">
            <article
              v-for="gate in planningView.quality_gates"
              :id="`quality-gate-${gate.gate_id}`"
              :key="gate.gate_id"
              class="quality-gate-card"
            >
              <header>
                <code>{{ gate.gate_id }}</code>
                <span>document_quality_gate</span>
              </header>
              <ul>
                <li v-for="criterion in gate.criteria" :key="criterion">{{ criterion }}</li>
              </ul>
              <div v-if="gate.response_units.length" class="quality-gate-bindings">
                <strong>全文响应任务</strong>
                <span v-for="unit in gate.response_units" :key="unit.unit_id">
                  <code>{{ unit.unit_id }}</code>
                  {{ unit.title }}
                </span>
              </div>
              <div v-if="gate.score_conditions.length" class="quality-gate-bindings">
                <strong>满分条件</strong>
                <span v-for="condition in gate.score_conditions" :key="condition.condition_id">
                  <code>{{ condition.condition_id }}</code>
                  {{ condition.normalized_condition || condition.text }}
                </span>
              </div>
              <details v-if="gate.requirements?.length" class="chapter-requirements">
                <summary>关联需求原文 · {{ gate.requirements.length }} 项</summary>
                <article
                  v-for="requirement in gate.requirements"
                  :key="requirement.requirement_id"
                >
                  <header>
                    <code>{{ requirement.requirement_id }}</code>
                    <small>{{ requirement.source_location.label }}</small>
                  </header>
                  <p>
                    {{
                      requirement.original_text
                        || requirement.normalized_requirement
                        || '未找到关联需求原文'
                    }}
                  </p>
                </article>
              </details>
              <details v-if="gate.check_items?.length">
                <summary>查看 {{ gate.check_items.length }} 个检查项</summary>
                <ul>
                  <li v-for="item in gate.check_items" :key="item">{{ item }}</li>
                </ul>
              </details>
            </article>
          </div>
        </section>

        <div v-if="planningView.uncovered_response_units.length" class="uncovered-warning" role="alert">
          <strong>仍有 {{ planningView.uncovered_response_units.length }} 个评分响应任务未被目录或全文质量门覆盖：</strong>
          {{ planningView.uncovered_response_units.map(item => item.title).join('、') }}
        </div>
      </section>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  chatV3,
  confirmV3Planning,
  downloadV3Final,
  fetchV3ContentUnit,
  fetchV3DocumentPreview,
  fetchV3GenerationStage,
  fetchV3WorkspaceSnapshot,
  prepareV3Outline,
  resolveV3Research,
  runV3Pipeline,
  uploadV3Input,
} from '../api'
import {
  formatV3ApiError,
  isDeepSeekEligibleInput,
  normalizeV3WorkspaceSnapshot,
  projectV3Planning,
  selectDeepSeekAttachmentIds,
  v3ErrorDetails,
} from '../api/v3Contracts.js'

const props = defineProps({
  runId: { type: String, required: true },
})

const uploadZones = [
  {
    role: 'tender',
    title: '招标文件',
    description: '招标正文、采购需求、评分办法和补充说明。',
    required: true,
  },
  {
    role: 'score',
    title: '评分附件（可选）',
    description: '评分表单独成文时上传；完整招标文件内嵌评分表无需重复上传。',
    required: false,
  },
  {
    role: 'company',
    title: '公司资料',
    description: '企业资质、案例、人员、产品说明和证明文件。',
    required: false,
  },
]
const pipelineSummaryLabels = {
  input_count: '输入文件',
  block_count: '来源块',
  requirement_count: '需求',
  group_count: '评分组',
  score_rule_count: '评分规则',
  score_point_count: '评分点',
  total_points: '总分',
  response_unit_count: '独立响应任务',
  condition_count: '满分条件',
  evidence_need_count: '证据缺口',
  warning_count: '审核提示',
  chapter_count: '章节节点',
  primary_response_unit_count: '主责响应任务',
  supporting_response_unit_count: '协同响应任务',
  score_condition_count: '目录满分条件',
  quality_gate_count: '质量门',
  outline_batch_count: '目录批次',
  outline_batch_generated_count: '本次生成批次',
  outline_batch_reused_count: '复用批次',
  outline_batch_failed_count: '失败批次',
}

const snapshot = ref({})
const loading = ref(false)
const running = ref(false)
const runningAction = ref('')
const uploadingRole = ref('')
const asking = ref(false)
const researchingNeedId = ref('')
const error = ref('')
const errorDetails = ref([])
const message = ref('')
const reply = ref('')
const question = ref('')
const writerChatTurns = ref([])
const selectedScoreId = ref('')
const selectedPipelineProductKind = ref('')
const expandedChapterIds = ref(new Set())
const deepSeekAttachmentIds = ref([])
const pendingUploads = reactive({ tender: [], score: [], company: [] })
const waitingForOutlineCompletion = ref(false)
const activeTab = ref('upload')
const activeStageDrawerId = ref('')
const selectedContentUnitId = ref('')
const selectedContentUnitTitle = ref('')
const selectedWriterChapterId = ref('')
const expandedWriterChapterIds = ref(new Set())
const contentUnitDetail = ref(null)
const contentUnitLoading = ref(false)
const stageDetail = ref(null)
const stageDetailLoading = ref(false)
const stageDetailError = ref('')
const documentPreview = ref(null)
const documentPreviewLoading = ref(false)
const documentPreviewError = ref('')
const selectedPreviewSectionId = ref('')
const previewMode = ref('word')
const loadedPreviewOperationId = ref('')
let timer = null
let stageDetailRequestToken = 0

const selectedDrawerStage = computed(() => (
  topPipelineStages.value.find(s => s.stage_id === activeStageDrawerId.value) || null
))

async function loadStageDetail(stageId, { showLoading = false } = {}) {
  const normalized = String(stageId || '')
  if (!normalized) return
  const requestToken = ++stageDetailRequestToken
  if (showLoading) stageDetailLoading.value = true
  stageDetailError.value = ''
  try {
    const { data } = await fetchV3GenerationStage(props.runId, normalized)
    if (
      requestToken === stageDetailRequestToken
      && activeStageDrawerId.value === normalized
    ) {
      stageDetail.value = data?.stage || null
    }
  } catch (cause) {
    if (
      requestToken === stageDetailRequestToken
      && activeStageDrawerId.value === normalized
      && (showLoading || !stageDetail.value)
    ) {
      stageDetailError.value = formatV3ApiError(cause, '节点详情读取失败。')
    }
  } finally {
    if (requestToken === stageDetailRequestToken) {
      stageDetailLoading.value = false
    }
  }
}

async function openStageDrawer(stage) {
  if (activeStageDrawerId.value === stage.stage_id) {
    closeStageDrawer()
  } else {
    activeStageDrawerId.value = stage.stage_id
    stageDetail.value = null
    await loadStageDetail(stage.stage_id, { showLoading: true })
  }
}

async function openWarningStage(warning) {
  const stageId = String(warning?.stage_id || 'verify_document')
  const stage = topPipelineStages.value.find(item => item.stage_id === stageId)
  if (!stage) return
  if (activeStageDrawerId.value === stageId) {
    closeStageDrawer()
    await nextTick()
  }
  await openStageDrawer(stage)
}

function closeStageDrawer() {
  stageDetailRequestToken += 1
  activeStageDrawerId.value = ''
  stageDetail.value = null
  stageDetailLoading.value = false
  stageDetailError.value = ''
}

function handleWorkspaceKeydown(event) {
  if (event.key === 'Escape' && activeStageDrawerId.value) {
    closeStageDrawer()
  }
}

function markdownTable(rows) {
  if (!rows.length) return ''
  const escapeCell = value => String(value ?? '').replace(/\|/g, '\\|').replace(/\n/g, '<br>')
  const [header, ...body] = rows
  const width = Math.max(header?.length || 0, ...body.map(row => row.length))
  const normalize = row => Array.from({ length: width }, (_, index) => escapeCell(row?.[index]))
  return [
    `| ${normalize(header).join(' | ')} |`,
    `| ${Array.from({ length: width }, () => '---').join(' | ')} |`,
    ...body.map(row => `| ${normalize(row).join(' | ')} |`),
  ].join('\n')
}

const uploading = computed(() => Boolean(uploadingRole.value))
const workspaceName = computed(() => {
  const matched = props.runId.match(/^(.+?)_(\d{8}_\d{6})(?:_\d+)?$/)
  return matched ? matched[1].replace(/_/g, ' ') : props.runId
})
const inputs = computed(() => snapshot.value.inputs || {})
const activeInputs = computed(() => (inputs.value.inputs || []).filter(item => item.active))
const tenderInputs = computed(() => activeInputs.value.filter(item => item.role === 'tender'))
const companyInputs = computed(() => activeInputs.value.filter(item => item.role === 'company'))
const hasTender = computed(() => tenderInputs.value.length > 0)
const document = computed(() => snapshot.value.document || {})
const generation = computed(() => snapshot.value.generation || {})
const documentPreviewMarkdown = computed(() => (documentPreview.value?.blocks || [])
  .map((block) => {
    if (block.type === 'heading') return `${'#'.repeat(Math.min(Math.max(block.level || 1, 1), 6))} ${block.text || ''}`
    if (block.type === 'paragraph') return block.text || ''
    if (block.type === 'list') return (block.items || []).map(item => `- ${item}`).join('\n')
    if (block.type === 'table') return markdownTable(block.rows || [])
    return ''
  })
  .filter(Boolean)
  .join('\n\n'))
const generationStages = computed(() => generation.value.stages || [])
const generationContent = computed(() => generation.value.content || {})
const generationResearch = computed(() => generation.value.research || {})
const writerUnit = computed(() => {
  const units = generationContent.value.units || []
  return units.find(unit => unit.unit_id === selectedContentUnitId.value)
    || units.find(unit => unit.status === 'running')
    || units[0]
    || null
})
const selectedWriterChapter = computed(() => flatOutline.value.find(
  chapter => chapter.chapter_id === selectedWriterChapterId.value,
) || null)
const writerPreviewText = computed(() => {
  if (contentUnitDetail.value && selectedContentUnitId.value === writerUnit.value?.unit_id) {
    return (contentUnitDetail.value.blocks || []).map(block => block.content || '').filter(Boolean).join('\n\n')
  }
  return String(writerUnit.value?.draft_preview || writerUnit.value?.preview || '')
})
const writerPreviewParagraphs = computed(() => writerPreviewText.value.split(/\n{2,}/).filter(Boolean))
const writerPhaseText = computed(() => {
  const status = String(writerUnit.value?.status || 'queued')
  if (status === 'running') {
    return writingPhaseLabel(writerUnit.value.progress_phase, status)
  }
  if (['blocked_human', 'failed', 'paused'].includes(status)) {
    return writingPhaseLabel(writerUnit.value?.progress_phase, status)
  }
  return contentUnitStatusLabel(status)
})
const writerResearchCalls = computed(() => {
  const unitId = writerUnit.value?.unit_id
  const calls = generationResearch.value.calls || []
  return unitId ? calls.filter(call => call.unit_id === unitId) : calls.slice(0, 2)
})
const generationBusy = computed(() => (
  (running.value && runningAction.value === 'document')
  || ['queued', 'running', 'processing'].includes(String(generation.value.status || ''))
  || generationStages.value.some(stage => ['queued', 'running'].includes(stage.status))
))
const completedGenerationStages = computed(() => (
  generationStages.value.filter(stage => ['succeeded', 'reused'].includes(stage.status)).length
))
const generationPercent = computed(() => (
  generationStages.value.length
    ? Math.round((completedGenerationStages.value / generationStages.value.length) * 100)
    : 0
))
const currentGenerationStage = computed(() => (
  generationStages.value.find(stage => stage.stage_id === generation.value.current_stage_id)
  || generationStages.value.find(stage => stage.status === 'running')
  || null
))
const generationErrorMessage = computed(() => (
  generationStages.value.find(stage => stage.status === 'failed')?.error?.message
  || generation.value.error?.message
  || generation.value.message
  || '完整标书生成失败。'
))
const generationHeadline = computed(() => {
  if (generationBusy.value) {
    return currentGenerationStage.value?.label
      ? `正在执行“${currentGenerationStage.value.label}”，页面每 2 秒自动更新。`
      : '任务已启动，正在等待后端阶段状态。'
  }
  if (generationContent.value.stale_units) {
    return '旧正文与当前写作器、模型或研究策略不一致，必须重新生成。'
  }
  if (generation.value.status === 'succeeded') return '全部阶段已完成，可查看章节正文并下载 Word。'
  if (generation.value.status === 'blocked') return `已暂停，等待处理：${generationErrorMessage.value}`
  if (generation.value.status === 'failed') return `生成已停止：${generationErrorMessage.value}`
  return '确认目录后可生成整本标书，也可在左侧选择目录后只生成该章。'
})
const generationTabLabel = computed(() => {
  if (generationBusy.value) return currentGenerationStage.value?.label || '任务已启动'
  if (generationContent.value.stale_units) return '旧正文已过期，需重新生成'
  if (generation.value.status === 'succeeded') return '完整标书已生成'
  if (generation.value.status === 'blocked') return '等待处理后重新生成'
  if (generation.value.status === 'failed') return '生成失败，可重新生成'
  return '实时进度与章节预览'
})
const evidenceNeeds = computed(() => snapshot.value.evidence_needs || [])
const planning = computed(() => snapshot.value.planning || {})
const planningStatus = computed(() => planning.value.status || 'not_ready')
const deliveryStatus = computed(() => document.value.delivery?.status || 'new')
const deliveryReady = computed(() => (
  ['ready', 'ready_with_warnings'].includes(deliveryStatus.value)
  && Number(generationContent.value.stale_units || 0) === 0
))
const planningView = computed(() => projectV3Planning(snapshot.value))
const hasScorePoints = computed(() => planningView.value.summary.score_point_count > 0)
const hasOutline = computed(() => planningView.value.summary.chapter_count > 0)

// 当生成完成或已存在目录时，自动切至【审阅目录】Tab
watch(hasOutline, (val) => {
  if (val && activeTab.value === 'upload') {
    activeTab.value = 'planning'
  }
}, { immediate: true })
watch(
  () => generation.value.operation_id,
  (operationId) => {
    if (
      operationId
      && ['queued', 'running', 'processing', 'blocked', 'failed'].includes(generation.value.status)
    ) {
      activeTab.value = 'generation'
    }
  },
  { immediate: true },
)
watch(
  () => [
    deliveryStatus.value,
    generation.value.operation_id,
    generationContent.value.stale_units,
  ],
  ([status]) => {
    if (
      ['ready', 'ready_with_warnings'].includes(status)
      && Number(generationContent.value.stale_units || 0) === 0
    ) {
      loadDocumentPreview()
    } else if (Number(generationContent.value.stale_units || 0) > 0) {
      documentPreview.value = null
      documentPreviewError.value = '旧正文已过期，请重新生成后再预览或下载。'
    }
  },
  { immediate: true },
)
const sourceIndex = computed(() => snapshot.value.analysis?.source_index || {})
const analysisPipeline = computed(() => snapshot.value.analysis?.pipeline || {})
const pipelineStages = computed(() => analysisPipeline.value.stages || [])
const latestWorkspaceOperation = computed(() => (
  snapshot.value.analysis?.latest_operation || {}
))
const latestOperationKind = computed(() => String(
  latestWorkspaceOperation.value.kind || '',
))
const latestOperationStatus = computed(() => String(
  latestWorkspaceOperation.value.status || '',
))
const latestOutlineOperationBusy = computed(() => (
  latestOperationKind.value === 'document.prepare_outline'
  && ['queued', 'running', 'processing'].includes(latestOperationStatus.value)
))
const showGenerationPipeline = computed(() => (
  (running.value && runningAction.value === 'document')
  || latestOperationKind.value === 'document.run_pipeline'
  || (!latestOperationKind.value && Boolean(generation.value.operation_id))
))
const generationStageIds = new Set([
  'sync_material_requirements',
  'compile_document_contract',
  'plan_document',
  'execute_content_plan',
  'integrate_document',
  'verify_document',
  'render_document',
  'verify_delivery',
])
const generationPrerequisiteStages = computed(() => (
  generationStages.value.filter(stage => !generationStageIds.has(stage.stage_id))
))
const generationExecutionStages = computed(() => (
  generationStages.value.filter(stage => generationStageIds.has(stage.stage_id))
))
const generationPrerequisiteCompleted = computed(() => (
  generationPrerequisiteStages.value.filter(
    stage => ['succeeded', 'reused'].includes(stage.status),
  ).length
))
const generationPrerequisiteReused = computed(() => (
  generationPrerequisiteStages.value.filter(stage => stage.status === 'reused').length
))
const topPipelineStages = computed(() => (
  showGenerationPipeline.value
    ? generationExecutionStages.value
    : pipelineStages.value
))
const topPipelineTitle = computed(() => (
  showGenerationPipeline.value
    ? '阶段 3 · 完整标书生成'
    : '阶段 2 · 解析评分并生成目录'
))
const topPipelineDescription = computed(() => (
  showGenerationPipeline.value
    ? '使用已经确认的评分目录，执行材料同步、逐章写作、全文整合、质量审核和 Word 交付。'
    : '只解析招标要求、评分点并生成章节目录；本阶段不会写正文。'
))
const pipelineProducts = computed(() => analysisPipeline.value.products || [])
const activePipelineProduct = computed(() => (
  pipelineProducts.value.find(product => product.kind === selectedPipelineProductKind.value)
  || pipelineProducts.value.at(-1)
  || null
))
const pipelineWarnings = computed(() => (
  [...new Set(
    pipelineProducts.value
      .flatMap(product => (Array.isArray(product?.warnings) ? product.warnings : []))
      .map(pipelineWarningText)
      .filter(Boolean),
  )]
))
const pipelineStatus = computed(() => {
  const status = String(analysisPipeline.value.status || 'not_started')
  if (pipelineStages.value.some(stage => ['running', 'queued'].includes(stage.status))) {
    return 'running'
  }
  if (pipelineStages.value.some(stage => stage.status === 'failed')) return 'failed'
  if (pipelineStages.value.some(stage => stage.status === 'blocked_human')) return 'blocked_human'
  return status
})
const outlineBusy = computed(() => (
  (running.value && runningAction.value === 'outline')
  || latestOutlineOperationBusy.value
))
const pipelineBusy = computed(() => outlineBusy.value)
const outlineActionDisabled = computed(() => (
  running.value
  || outlineBusy.value
  || generationBusy.value
  || uploading.value
  || !hasTender.value
))
const activePipelineStage = computed(() => (
  pipelineStages.value.find(stage => ['running', 'queued'].includes(stage.status))
))
const outlineRunningLabel = computed(() => (
  activePipelineStage.value?.label
    ? `正在${activePipelineStage.value.label}…`
    : '正在生成评分目录…'
))
const pipelineStatusLabel = computed(() => ({
  not_started: '尚未开始',
  pending: '等待执行',
  queued: '已排队',
  processing: '处理中',
  running: '处理中',
  failed: '已失败',
  blocked_human: '等待人工确认',
  succeeded: '已完成',
  completed: '已完成',
}[pipelineStatus.value] || pipelineStatus.value))
const topPipelineStatus = computed(() => (
  showGenerationPipeline.value
    ? (generationBusy.value ? 'running' : String(generation.value.status || 'not_started'))
    : pipelineStatus.value
))
const topPipelineStatusLabel = computed(() => ({
  not_started: '尚未开始',
  pending: '等待执行',
  queued: '已排队',
  processing: '处理中',
  running: '处理中',
  failed: '已失败',
  blocked_human: '等待人工确认',
  succeeded: '已完成',
  completed: '已完成',
  cancelled: '已取消',
}[topPipelineStatus.value] || topPipelineStatus.value))
const sourceStatusById = computed(() => new Map(
  (sourceIndex.value.input_status || []).map(item => [item.input_id, item]),
))
const analysisStale = computed(() => {
  if (
    planningView.value.outdated
    || snapshot.value.analysis?.stale
    || planningStatus.value === 'outdated'
  ) return true
  return false
})
const analysisStaleMessage = computed(() => {
  const latest = snapshot.value.analysis?.latest_operation || {}
  if (latest.result_outdated || latest.status === 'failed') {
    return '最近一次评分点解析未成功，旧评分点、旧目录和旧确认状态已隐藏，请修正问题后重新解析。'
  }
  return '上传文件已变化，旧评分点、旧目录和旧确认状态已隐藏，请重新解析。'
})
const flatOutline = computed(() => {
  const result = []
  const append = chapters => {
    for (const chapter of chapters) {
      const directIds = new Set(chapter.direct_score_point_ids || [])
      result.push({
        ...chapter,
        direct_score_points: (chapter.score_points || []).filter(
          point => directIds.has(point.score_point_id),
        ),
      })
      append(chapter.children || [])
    }
  }
  append(planningView.value.outline)
  return result
})
const visibleWriterOutline = computed(() => {
  const result = []
  const append = chapters => {
    for (const chapter of chapters || []) {
      result.push(chapter)
      if (chapter.children?.length && expandedWriterChapterIds.value.has(chapter.chapter_id)) {
        append(chapter.children)
      }
    }
  }
  append(planningView.value.outline)
  return result
})
const visibleOutline = computed(() => {
  const result = []
  const appendVisible = chapters => {
    for (const chapter of chapters) {
      const directIds = new Set(chapter.direct_score_point_ids || [])
      const projected = {
        ...chapter,
        direct_score_points: (chapter.score_points || []).filter(
          point => directIds.has(point.score_point_id),
        ),
      }
      result.push(projected)
      if (projected.children?.length && expandedChapterIds.value.has(projected.chapter_id)) {
        appendVisible(projected.children)
      }
    }
  }
  appendVisible(planningView.value.outline)
  return result
})
const hasCollapsibleChapters = computed(() => flatOutline.value.some(
  chapter => chapter.children?.length,
))
watch(
  () => planningView.value.outline.map(chapter => chapter.chapter_id).join('|'),
  () => {
    expandedChapterIds.value = new Set(
      planningView.value.outline
        .filter(chapter => chapter.children?.length)
        .map(chapter => chapter.chapter_id),
    )
  },
  { immediate: true },
)
watch(
  () => planningView.value.outline.map(chapter => chapter.chapter_id).join('|'),
  () => {
    expandedWriterChapterIds.value = new Set(
      planningView.value.outline
        .filter(chapter => chapter.children?.length)
        .map(chapter => chapter.chapter_id),
    )
  },
  { immediate: true },
)
watch(
  () => pipelineProducts.value.map(product => product.kind).join('|'),
  () => {
    if (pipelineProducts.value.some(product => product.kind === selectedPipelineProductKind.value)) return
    selectedPipelineProductKind.value = pipelineProducts.value.at(-1)?.kind || ''
  },
  { immediate: true },
)
const planningStatusLabel = computed(() => ({
  not_ready: '尚未生成目录',
  needs_human: '目录待确认',
  confirmed: '目录已确认',
  blocked: '目录已阻断',
  outdated: '目录结果已失效',
}[planningStatus.value] || planningStatus.value))
const deliveryStatusLabel = computed(() => ({
  new: '尚未生成',
  ready: '可下载',
  ready_with_warnings: '带风险可下载',
  failed: '交付失败',
}[deliveryStatus.value] || deliveryStatus.value))
const outlineActionLabel = computed(() => (
  snapshot.value.analysis?.latest_operation?.kind === 'document.prepare_outline'
  && snapshot.value.analysis?.latest_operation?.status === 'failed'
    ? '继续生成目录（复用已完成节点）'
    : hasOutline.value || analysisStale.value
    ? '重新解析评分点并生成目录'
    : '解析评分点并生成目录'
))
const workflowSteps = computed(() => [
  {
    label: '上传资料',
    description: hasTender.value ? `${activeInputs.value.length} 个文件已登记` : '至少上传一份招标文件',
    status: hasTender.value ? 'done' : 'active',
  },
  {
    label: '解析评分点',
    description: hasScorePoints.value ? `${planningView.value.summary.score_point_count} 个评分点` : '等待解析',
    status: hasScorePoints.value ? 'done' : (outlineBusy.value ? 'active' : 'pending'),
  },
  {
    label: '生成章节目录',
    description: hasOutline.value ? `${planningView.value.summary.chapter_count} 个章节节点` : '等待规划',
    status: hasOutline.value
      ? 'done'
         : (
           outlineBusy.value
           && activePipelineStage.value?.stage_id === 'compile_chapter_blueprint'
             ? 'active'
             : 'pending'
         ),
  },
  {
    label: '人工确认',
    description: planningStatusLabel.value,
    status: planningStatus.value === 'confirmed'
      ? 'done'
      : (planningStatus.value === 'needs_human' ? 'active' : 'pending'),
  },
])

function inputsForRole(role) {
  return activeInputs.value.filter(item => item.role === role)
}

function selectFiles(role, event) {
  const accepted = [...(event.target.files || [])]
  const known = new Set(pendingUploads[role].map(file => `${file.name}:${file.size}`))
  for (const file of accepted) {
    const key = `${file.name}:${file.size}`
    if (!known.has(key)) {
      pendingUploads[role].push(file)
      known.add(key)
    }
  }
  event.target.value = ''
}

function removePendingFile(role, index) {
  pendingUploads[role].splice(index, 1)
}

async function refresh(resetError = false) {
  if (!props.runId || loading.value) return
  loading.value = true
  try {
    const { data } = await fetchV3WorkspaceSnapshot(props.runId)
    snapshot.value = normalizeV3WorkspaceSnapshot(data)
    if (activeStageDrawerId.value && !stageDetailLoading.value) {
      void loadStageDetail(activeStageDrawerId.value)
    }
    if (resetError) {
      clearError()
    } else if (!error.value) {
      const latest = latestOutlineOperation()
      if (latest?.status === 'failed') reportOutlineOperationFailure(latest)
    }
  } catch (cause) {
    reportError(cause, 'V3 工作区状态读取失败。')
  } finally {
    loading.value = false
  }
}

async function uploadRole(role) {
  const files = [...pendingUploads[role]]
  if (!files.length) return
  uploadingRole.value = role
  clearError()
  message.value = ''
  const failed = []
  let uploadedCount = 0
  for (const [index, file] of files.entries()) {
    try {
      await uploadV3Input(props.runId, role, file)
      uploadedCount += 1
    } catch (cause) {
      failed.push(...files.slice(index))
      reportError(cause, '上传失败。')
      error.value = `${file.name}：${error.value}`
      break
    }
  }
  pendingUploads[role].splice(0, pendingUploads[role].length, ...failed)
  uploadingRole.value = ''
  if (uploadedCount) {
    message.value = `已登记 ${uploadedCount} 个${roleLabel(role)}文件。`
    await refresh()
  }
}

async function prepareOutline() {
  running.value = true
  runningAction.value = 'outline'
  activeTab.value = 'planning'
  clearError()
  waitingForOutlineCompletion.value = false
  message.value = ''
  try {
    const { data } = await prepareV3Outline(props.runId)
    assertCommandAccepted(data, '评分点解析与章节目录生成失败。')
    await refresh()
    if (!planningView.value.summary.score_point_count) {
      throw new Error('没有识别到评分点，已停止生成评分目录。请检查评分章节或评分附件。')
    }
    message.value = data.message || data.receipt?.message || '评分点与章节目录草案已生成。'
  } catch (cause) {
    // A long-running command can outlive the browser request. Refresh is
    // best-effort here; a second 30s snapshot timeout must not replace the
    // original command timeout or leave the UI in a misleading failed state.
    let refreshError = null
    try {
      await refresh()
    } catch (error) {
      refreshError = error
    }
    const latest = latestOutlineOperation()
    if (latest?.status === 'failed') {
      reportOutlineOperationFailure(latest)
    } else if (isRequestTimeout(cause) || isRequestTimeout(refreshError)) {
      waitingForOutlineCompletion.value = true
      error.value = '评分目录请求等待超过 12 分钟，后台仍在处理；系统会自动刷新，完成后显示详细结果。'
      errorDetails.value = [{
        title: '请求等待超时',
        description: '浏览器已停止等待，但后台任务没有被取消。请勿重复点击；等待自动刷新显示最终结果。',
      }]
    } else {
      reportError(cause, '评分点解析与章节目录生成失败。')
    }
  } finally {
    running.value = false
    runningAction.value = ''
  }
}

async function confirmPlanning() {
  running.value = true
  runningAction.value = 'confirm'
  clearError()
  try {
    const { data } = await confirmV3Planning(props.runId, planning.value.snapshot)
    assertCommandAccepted(data, '目录确认失败。')
    message.value = '目录已确认；正文生成尚未启动。'
    await refresh()
  } catch (cause) {
    reportError(cause, '目录确认失败。')
  } finally {
    running.value = false
    runningAction.value = ''
  }
}

async function runDocument(chapterIds = []) {
  const normalizedChapterIds = Array.isArray(chapterIds) ? chapterIds.filter(Boolean) : []
  running.value = true
  runningAction.value = normalizedChapterIds.length ? 'selected-chapter' : 'document'
  activeTab.value = 'generation'
  clearError()
  // A regenerate is a new attempt. Do not leave the previous attempt's
  // research failures, chapter body, or Word preview visible while it starts.
  closeStageDrawer()
  closeContentUnit()
  documentPreview.value = null
  documentPreviewError.value = ''
  loadedPreviewOperationId.value = ''
  selectedPreviewSectionId.value = ''
  writerChatTurns.value = []
  snapshot.value = {
    ...snapshot.value,
    generation: {
      ...generation.value,
      status: 'queued',
      current_stage_id: '',
      stages: [],
      research: { calls: [] },
      content: { total_units: 0, completed_units: 0, units: [] },
    },
  }
  message.value = normalizedChapterIds.length
    ? `章节“${selectedWriterChapter.value?.title || normalizedChapterIds[0]}”已开始单独生成。`
    : '完整标书生成任务已启动，正在等待后端阶段状态。'
  try {
    const { data } = await runV3Pipeline(props.runId, normalizedChapterIds)
    assertCommandAccepted(data, normalizedChapterIds.length ? '本章生成失败。' : '完整标书生成失败。')
    message.value = data.message || data.receipt?.message || (normalizedChapterIds.length ? '本章已生成。' : '完整标书已生成。')
    await refresh()
  } catch (cause) {
    let refreshError = null
    try {
      await refresh()
    } catch (refreshCause) {
      refreshError = refreshCause
    }
    if (
      isRequestTimeout(cause)
      && ['queued', 'running', 'processing'].includes(String(generation.value.status || ''))
    ) {
      message.value = '浏览器已停止等待响应，但后台仍在生成；页面会继续每 2 秒刷新，请勿重复提交。'
    } else if (generation.value.status === 'failed') {
      error.value = generationErrorMessage.value
      errorDetails.value = []
    } else {
      reportError(refreshError || cause, '完整标书生成失败。')
    }
  } finally {
    running.value = false
    runningAction.value = ''
  }
}

async function runSelectedChapter() {
  if (!selectedWriterChapterId.value) {
    error.value = '请先在左侧目录选择要生成的章节。'
    return
  }
  await runDocument([selectedWriterChapterId.value])
}

async function openContentUnit(unit) {
  if (!unit?.unit_id || unit.status !== 'completed' || unit.stale) return
  selectedContentUnitId.value = unit.unit_id
  selectedContentUnitTitle.value = unit.title || unit.unit_id
  contentUnitDetail.value = null
  contentUnitLoading.value = true
  try {
    const { data } = await fetchV3ContentUnit(props.runId, unit.unit_id)
    contentUnitDetail.value = data?.content_unit || null
  } catch (cause) {
    reportError(cause, '章节正文读取失败。')
  } finally {
    contentUnitLoading.value = false
  }
}

async function selectWriterUnit(unit) {
  if (!unit?.unit_id) return
  selectedContentUnitId.value = unit.unit_id
  selectedContentUnitTitle.value = unit.title || unit.unit_id
  if (unit.status === 'completed' && !unit.stale) {
    await openContentUnit(unit)
  } else {
    contentUnitDetail.value = null
  }
}

async function selectWriterChapter(chapter) {
  selectedWriterChapterId.value = chapter?.chapter_id || ''
  const unit = (generationContent.value.units || []).find(item => (
    (item.node_ids || []).includes(chapter?.chapter_id)
    || item.current_chapter_id === chapter?.chapter_id
  ))
  if (unit) await selectWriterUnit(unit)
  await nextTick()
  globalThis.document?.getElementById(`writer-document-${selectedWriterChapterId.value}`)?.scrollIntoView({
    behavior: globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'auto' : 'smooth',
    block: 'start',
  })
}

function toggleWriterChapter(chapterId) {
  const next = new Set(expandedWriterChapterIds.value)
  if (next.has(chapterId)) next.delete(chapterId)
  else next.add(chapterId)
  expandedWriterChapterIds.value = next
}

function closeContentUnit() {
  selectedContentUnitId.value = ''
  selectedContentUnitTitle.value = ''
  contentUnitDetail.value = null
  contentUnitLoading.value = false
}

async function loadDocumentPreview(force = false) {
  if (Number(generationContent.value.stale_units || 0) > 0) {
    documentPreview.value = null
    documentPreviewError.value = '旧正文已过期，请重新生成后再预览或下载。'
    return
  }
  if (
    documentPreviewLoading.value
    || (!force && loadedPreviewOperationId.value === generation.value.operation_id)
  ) return
  documentPreviewLoading.value = true
  documentPreviewError.value = ''
  try {
    const { data } = await fetchV3DocumentPreview(props.runId)
    documentPreview.value = data?.preview || null
    loadedPreviewOperationId.value = String(
      data?.preview?.operation_id || generation.value.operation_id || '',
    )
    selectedPreviewSectionId.value = data?.preview?.toc?.[0]?.id || ''
  } catch (cause) {
    documentPreview.value = null
    documentPreviewError.value = formatV3ApiError(cause, '完整标书预览尚未就绪。')
  } finally {
    documentPreviewLoading.value = false
  }
}

async function selectPreviewSection(sectionId) {
  selectedPreviewSectionId.value = sectionId
  await nextTick()
  const reduceMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
  globalThis.document?.getElementById(`doc-preview-${sectionId}`)?.scrollIntoView({
    behavior: reduceMotion ? 'auto' : 'smooth',
    block: 'start',
  })
}

function stageDetailValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return `${value.length} 项`
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function stageDetailLabel(key) {
  return {
    provider_id: '研究服务',
    planned_count: '计划问题',
    published_count: '成功问题',
    gap_count: '无结果',
    failed_count: '失败问题',
    max_retries: '最大重试',
    blocking_policy: '失败策略',
    research_call_count: '联网判断',
    search_query_count: '搜索查询',
    research_source_count: '取得来源',
    used_evidence_count: '正文采用证据',
    research_published_count: '已完成检索',
    total_units: '正文单元',
    completed_units: '已完成',
    running_units: '执行中',
    failed_units: '失败单元',
    verdict: '审核结论',
    finding_count: '问题数量',
    status: '交付状态',
    artifact_path: '输出文件',
    warning_count: '风险数量',
    artifact_kind: '产物类型',
    revision: '版本',
  }[key] || key
}

function assertCommandAccepted(data, fallback) {
  if (data?.ok === false || data?.receipt?.status === 'rejected') {
    const detail = data?.receipt?.error?.message
      || data?.receipt?.message
      || data?.message
      || fallback
    const commandError = new Error(detail)
    commandError.v3Payload = data
    throw commandError
  }
}

function deepSeekEligible(item) {
  return isDeepSeekEligibleInput(item)
}

async function research(need) {
  researchingNeedId.value = need.need_id
  clearError()
  try {
    const attachments = selectDeepSeekAttachmentIds(activeInputs.value, deepSeekAttachmentIds.value)
    const { data } = await resolveV3Research(props.runId, need.need_id, attachments)
    assertCommandAccepted(data, 'DeepSeek 检索失败。')
    message.value = data.message || data.receipt?.message || 'DeepSeek 检索完成。'
    deepSeekAttachmentIds.value = []
    await refresh()
  } catch (cause) {
    reportError(cause, 'DeepSeek 检索失败。')
  } finally {
    researchingNeedId.value = ''
  }
}

async function ask() {
  asking.value = true
  clearError()
  const reference = question.value.trim()
  const chapter = writerUnit.value?.current_chapter_title || writerUnit.value?.title || '未选择章节'
  try {
    writerChatTurns.value.push({ id: `user-${Date.now()}`, role: 'user', content: `参考章节「${chapter}」：${reference}` })
    const { data } = await chatV3(props.runId, `当前参考章节：${chapter}\n用户补充资料：${reference}\n请先判断这条资料是否可直接写入标书；再说明应写入本章的哪个位置、如何表述、还缺哪些佐证。不要展示隐藏推理过程。`)
    reply.value = data.reply || ''
    writerChatTurns.value.push({ id: `assistant-${Date.now()}`, role: 'assistant', content: reply.value || '暂未得到写入建议。' })
    question.value = ''
  } catch (cause) {
    reportError(cause, '对话处理失败。')
  } finally {
    asking.value = false
  }
}

function apiError(cause, fallback) {
  return formatV3ApiError(cause, fallback)
}

function clearError() {
  error.value = ''
  errorDetails.value = []
}

function reportError(cause, fallback) {
  error.value = apiError(cause, fallback)
  errorDetails.value = v3ErrorDetails(cause)
}

function isRequestTimeout(cause) {
  const code = String(cause?.code || '').toUpperCase()
  const text = String(cause?.message || '')
  return code === 'ECONNABORTED' || /timeout of \d+ms exceeded/i.test(text)
}

function latestOutlineOperation() {
  const latest = snapshot.value.analysis?.latest_operation
  return latest?.kind === 'document.prepare_outline' ? latest : null
}

function reportOutlineOperationFailure(operation) {
  const operationError = operation?.error && typeof operation.error === 'object'
    ? operation.error
    : { message: operation?.message || '评分点解析与章节目录生成失败。' }
  const cause = new Error(operationError.message || operation?.message || '评分点解析与章节目录生成失败。')
  cause.v3Payload = { receipt: { status: 'rejected', error: operationError } }
  reportError(cause, '评分点解析与章节目录生成失败。')
}

function pipelineStageStatus(stage) {
  const status = {
    pending: '等待上游阶段',
    queued: '已排队',
    running: '正在执行',
    succeeded: '执行完成',
    reused: '复用已验证产物',
    failed: '执行失败',
    blocked_human: '等待人工确认',
    cancelled: '已取消',
  }[stage.status] || stage.status
  return stage.attempt > 1 ? `${status} · 第 ${stage.attempt} 次尝试` : status
}

function pipelineStageOperation(stage) {
  return {
    ingest_inputs: '操作：检查已上传文件',
    normalize_sources: '操作：解析正文、标题和表格',
    compile_template_structure: '操作：识别模板目录与可写位置',
    build_requirement_ledger: '操作：提取采购要求和约束',
    analyze_scores: '操作：解析评分点与满分条件',
    compile_chapter_blueprint: '操作：生成评分驱动章节目录',
    confirm_planning: '用户操作：审阅并确认目录',
    sync_material_requirements: '操作：列出需人工提供的真实材料',
    compile_document_contract: '操作：锁定已确认章节结构',
    plan_document: '操作：生成逐章写作计划',
    execute_content_plan: '操作：按所选章节写作；缺公开依据时自动联网检索',
    integrate_document: '操作：合并章节并统一术语',
    verify_document: '操作：检查覆盖、质量和一致性',
    render_document: '操作：生成 Word 文档',
    verify_delivery: '用户操作：预览并下载交付件',
  }[stage.stage_id] || '操作：执行并记录该步骤产物'
}

function pipelineStageError(stage) {
  const stageError = stage?.error && typeof stage.error === 'object'
    ? stage.error
    : { message: String(stage?.error || '') }
  const cause = new Error(stageError.message || '阶段执行失败。')
  cause.v3Payload = { receipt: { status: 'rejected', error: stageError } }
  const details = v3ErrorDetails(cause)
  if (details.length) {
    return details.map(item => `${item.title}：${item.description}`).join('；')
  }
  return formatV3ApiError(cause, '阶段执行失败。')
}

function generationStageSummary(stage) {
  const summary = stage?.summary && typeof stage.summary === 'object'
    ? stage.summary
    : {}
  const parts = []
  const append = (key, label, suffix = '') => {
    if (summary[key] === undefined || summary[key] === null || summary[key] === '') return
    parts.push(`${label}${summary[key]}${suffix}`)
  }
  append('content_unit_count', '章节单元 ', ' 个')
  append('block_count', '正文块 ', ' 个')
  append('character_count', '正文 ', ' 字')
  append('planned_count', '研究问题 ', ' 个')
  append('published_count', '研究成功 ', ' 个')
  append('gap_count', '无结果 ', ' 个')
  append('failed_count', '失败 ', ' 个')
  append('issue_count', '审核问题 ', ' 个')
  if (summary.verdict) parts.push(`审核结论 ${summary.verdict}`)
  if (summary.status) parts.push(`状态 ${summary.status}`)
  if (Array.isArray(summary.output_files) && summary.output_files.length) {
    parts.push(`输出 ${summary.output_files.join('、')}`)
  }
  return parts.join(' · ')
}

function contentUnitStatusLabel(status) {
  return {
    pending: '等待计划',
    queued: '等待写作',
    running: '正在写作',
    blocked_human: '等待人工处理',
    stale: '旧正文已过期',
    completed: '已完成，可查看全文',
    failed: '写作失败',
  }[status] || status
}

function contentUnitTraceLabel(status) {
  return {
    queued: '写作单元等待中',
    running: '写作单元进行中',
    blocked_human: '写作单元等待处理',
    stale: '写作单元已过期',
    completed: '写作单元已完成',
    failed: '写作单元失败',
  }[status] || '写作单元状态待确认'
}

function researchStatusLabel(status) {
  return {
    open: '等待检索',
    planned: 'Agent 已决定调用',
    researching: '正在调用 DeepSeek',
    satisfied: '已找到公开来源',
    published: '已发布可核验证据',
    skipped: 'Agent 决定不调用',
    blocked_human: '等待用户处理',
    gap: '未找到可核验结果',
    failed: '检索失败',
  }[status] || status
}

function researchUsageLabel(status) {
  return {
    used: '已用于标书',
    not_used: '未采用',
    unknown: '采用情况待确认',
  }[status] || '采用情况待确认'
}

function writingPhaseLabel(phase, unitStatus = '') {
  const status = String(unitStatus || '')
  if (
    status === 'blocked_human'
    || status === 'failed'
    || status === 'paused'
  ) {
    return {
      model_output_invalid: '模型输出无效，已暂停',
      research_blocked: '联网检索受阻，已暂停',
      paused: '写作已暂停',
      failed: '写作失败',
      drafting: '写作已暂停',
      preparing_research: '写作已暂停',
    }[phase] || '写作已暂停'
  }
  return {
    preparing_research: '正在检查人工材料',
    drafting: '正在撰写',
    model_output_invalid: '模型输出无效，已暂停',
    research_blocked: '联网检索受阻，已暂停',
    paused: '写作已暂停',
    failed: '写作失败',
  }[phase] || '正在处理'
}

function formatTimestamp(value) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value || '')
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function llmRequestStatus(status) {
  return {
    running: '请求中',
    succeeded: '接口已返回',
    failed: '接口失败',
  }[status] || status
}

function llmRequestSummary(request) {
  const parameters = request?.parameters || {}
  const attemptKind = {
    initial: '初稿',
    repair: '受控修复',
    controlled_repair: '受控修复',
  }[parameters.attempt_kind]
  const parts = [
    parameters.logical_batch_id || parameters.batch_id,
    parameters.batch_group_id,
    attemptKind,
    parameters.model,
    `温度 ${parameters.temperature ?? '-'}`,
    parameters.input_chars ? `输入 ${parameters.input_chars} 字符` : null,
  ].filter(Boolean)
  if (request.started_at) parts.push(request.started_at)
  return parts.join(' · ')
}

function formatLlmRequest(request) {
  return JSON.stringify(request?.parameters || {}, null, 2)
}

function pipelineProductSummary(product) {
  const entries = Object.entries(product?.summary || {})
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
  if (!entries.length) {
    if (product.status === 'outdated') return '该产物来自上一次任务。'
    if (product.status === 'warning') return '产物已持久化，审核提示不阻塞后续流程。'
    return '产物已持久化。'
  }
  return entries
    .map(([key, value]) => `${pipelineSummaryLabels[key] || key} ${formatPoints(value)}`)
    .join(' · ')
}

function pipelineProductStatusLabel(product) {
  return {
    outdated: '旧产物',
    warning: '需复核',
  }[product?.status] || '已生成'
}

function pipelineWarningText(warning) {
  if (typeof warning === 'string') return warning.trim()
  if (!warning || typeof warning !== 'object') return ''
  return String(warning.message || warning.description || warning.code || '').trim()
}

function pipelineProductItems(product) {
  if (product?.status === 'outdated') return []
  if (Array.isArray(product?.items)) return product.items
  if (product?.items && typeof product.items === 'object') return [product.items]
  if (product?.kind === 'SourceIndex') {
    return (snapshot.value.analysis?.source_index?.blocks || []).slice(0, 30).map(block => ({
      id: block.block_id,
      kind: block.block_kind,
      heading: block.heading_path,
      content: String(block.content || '').slice(0, 500),
    }))
  }
  if (product?.kind === 'RequirementLedger') {
    return (snapshot.value.analysis?.requirement_ledger?.requirements || []).slice(0, 50).map(item => ({
      id: item.requirement_id,
      kind: item.kind,
      requirement: item.normalized_requirement,
      source: item.original_text,
    }))
  }
  if (product?.kind === 'ScoreModel') {
    return (snapshot.value.analysis?.score_model?.points || []).slice(0, 50).map(point => ({
      id: point.score_point_id,
      title: point.title,
      max_points: point.max_points,
      response_units: point.response_units,
      score_conditions: point.score_conditions,
    }))
  }
  return []
}

function previewPipelineProductItems(product) {
  return pipelineProductItems(product).slice(0, 12)
}

function remainingPipelineProductItems(product) {
  return pipelineProductItems(product).slice(12)
}

function pipelineProductItemId(item) {
  return String(item?.id || item?.requirement_id || item?.score_point_id || '')
}

function pipelineProductItemKey(item, index) {
  return pipelineProductItemId(item) || `${pipelineProductItemTitle(item, index)}-${index}`
}

function pipelineProductItemTitle(item, index) {
  return String(
    item?.title
    || item?.requirement
    || item?.canonical_name
    || item?.intent
    || item?.name
    || item?.heading
    || item?.normalized_condition
    || `第 ${index + 1} 项`,
  )
}

function pipelineProductItemMeta(item) {
  const parts = []
  if (Number.isFinite(Number(item?.max_points))) parts.push(`${formatPoints(item.max_points)}分`)
  if (Number.isFinite(Number(item?.level_count))) parts.push(`${item.level_count} 个档次`)
  if (Number.isFinite(Number(item?.duty_count))) parts.push(`${item.duty_count} 项响应任务`)
  if (Number.isFinite(Number(item?.score_condition_count))) {
    parts.push(`${item.score_condition_count} 个满分条件`)
  }
  if (Number.isFinite(Number(item?.response_unit_count))) {
    parts.push(`${item.response_unit_count} 个响应任务`)
  }
  return parts.join(' · ') || '展开查看详情'
}

function pipelineProductItemFields(item) {
  if (!item || typeof item !== 'object') return []
  const labels = {
    kind: '类型',
    requirement: '响应要求',
    source: '来源原文',
    intent: '响应意图',
    review_status: '审核状态',
    response_units: '独立响应任务',
    full_score_conditions: '满分条件',
    score_conditions: '满分条件',
    work_packages: '工作包',
    goals: '项目目标',
    scope: '项目范围',
    identity: '项目识别信息',
    requirement_count: '关联需求数',
    primary_response_unit_count: '主责响应任务数',
    supporting_response_unit_count: '协同响应任务数',
    template_level: '模板层级',
  }
  const hidden = new Set([
    'id', 'requirement_id', 'score_point_id', 'title', 'max_points', 'level_count',
    'duty_count', 'score_condition_count', 'response_unit_count', 'parent_id',
  ])
  return Object.entries(item)
    .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => ({
      key,
      label: labels[key] || key,
      value: readableProductValue(value),
    }))
    .filter(field => field.value)
}

function readableProductValue(value) {
  if (Array.isArray(value)) {
    return value
      .map(item => readableProductValue(item))
      .filter(Boolean)
      .join('；')
  }
  if (value && typeof value === 'object') {
    return String(
      value.title
      || value.name
      || value.text
      || value.normalized_condition
      || value.response_expectation
      || value.canonical_name
      || Object.values(value)
        .filter(item => ['string', 'number'].includes(typeof item))
        .slice(0, 3)
        .join(' · '),
    )
  }
  return String(value)
}

function download() {
  downloadV3Final(props.runId)
}

function toggleScore(scorePointId) {
  selectedScoreId.value = selectedScoreId.value === scorePointId ? '' : scorePointId
  if (!selectedScoreId.value) return
  expandedChapterIds.value = new Set(
    flatOutline.value
      .filter(chapter => chapter.score_point_ids?.includes(selectedScoreId.value))
      .map(chapter => chapter.chapter_id),
  )
}

function isChapterExpanded(chapterId) {
  return expandedChapterIds.value.has(chapterId)
}

function toggleChapter(chapterId) {
  const next = new Set(expandedChapterIds.value)
  if (next.has(chapterId)) next.delete(chapterId)
  else next.add(chapterId)
  expandedChapterIds.value = next
}

function expandAllChapters() {
  expandedChapterIds.value = new Set(
    flatOutline.value
      .filter(chapter => chapter.children?.length)
      .map(chapter => chapter.chapter_id),
  )
}

function collapseAllChapters() {
  expandedChapterIds.value = new Set()
}

function expandChapterPath(chapterId) {
  const byId = new Map(flatOutline.value.map(chapter => [chapter.chapter_id, chapter]))
  const next = new Set(expandedChapterIds.value)
  let current = byId.get(chapterId)
  while (current) {
    if (current.children?.length) next.add(current.chapter_id)
    current = byId.get(current.parent_chapter_id)
  }
  expandedChapterIds.value = next
}

function scorePointValue(point) {
  const value = Number(point?.max_points)
  return Number.isFinite(value) ? `${formatPoints(value)}分` : '分值待确认'
}

function formatPoints(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return Number.isInteger(number) ? String(number) : number.toFixed(1)
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function roleLabel(role) {
  return { tender: '招标', score: '评分', company: '公司资料' }[role] || ''
}

function sourceStatus(item) {
  return sourceStatusById.value.get(item.input_id)?.status || 'pending'
}

function sourceStatusLabel(item) {
  return {
    processed: '已解析',
    partial: '部分解析',
    blocked: '待 OCR / 无法解析',
    pending: '待解析',
  }[sourceStatus(item)]
}

function sourceStatusClass(item) {
  return `file-${sourceStatus(item)}`
}

function responseDepthLabel(depth) {
  return {
    detailed: '详细响应',
    substantive: '实质响应',
    basic: '基础响应',
  }[depth] || '响应深度待确认'
}

function conditionRoleLabel(role) {
  return {
    content: '实质内容',
    evidence: '证明材料',
    constraint: '范围约束',
    quality: '写作质量',
    document: '全文要求',
  }[role] || role || '未分类'
}

function traceDestinationKey(destination) {
  return destination.type === 'chapter'
    ? `chapter:${destination.chapter_id}`
    : `document_quality_gate:${destination.gate_id}`
}

function traceDestinationHref(destination) {
  const target = destination.type === 'chapter'
    ? `chapter-${destination.chapter_id}`
    : `quality-gate-${destination.gate_id}`
  return `#${encodeURIComponent(target)}`
}

function traceDestinationLabel(destination) {
  return destination.type === 'chapter'
    ? `ChapterNode ${destination.chapter_id} · ${destination.title}`
    : `document_quality_gate ${destination.gate_id} · ${destination.title}`
}

watch(
  () => snapshot.value.analysis?.latest_operation,
  operation => {
    if (!waitingForOutlineCompletion.value || !operation) return
    if (operation.kind !== 'document.prepare_outline') return
    if (operation.status === 'failed') {
      waitingForOutlineCompletion.value = false
      reportOutlineOperationFailure(operation)
      return
    }
    if (operation.completed_outline || ['completed', 'succeeded'].includes(operation.status)) {
      waitingForOutlineCompletion.value = false
      clearError()
      message.value = '后台评分目录任务已完成。'
    }
  },
  { deep: true },
)

watch(
  () => props.runId,
  () => {
    selectedScoreId.value = ''
    selectedPipelineProductKind.value = ''
    expandedChapterIds.value = new Set()
    deepSeekAttachmentIds.value = []
    activeTab.value = 'upload'
    closeContentUnit()
    closeStageDrawer()
    pendingUploads.tender.splice(0)
    pendingUploads.score.splice(0)
    pendingUploads.company.splice(0)
    refresh()
  },
)

onMounted(() => {
  refresh()
  window.addEventListener('keydown', handleWorkspaceKeydown)
  timer = window.setInterval(() => {
    if (!uploading.value && !loading.value) refresh()
  }, 2000)
})
onUnmounted(() => {
  window.clearInterval(timer)
  window.removeEventListener('keydown', handleWorkspaceKeydown)
})
</script>

<style scoped>
.v3-workspace {
  max-width: 1500px;
  margin: 0 auto;
  padding: 28px clamp(18px, 3vw, 40px) 56px;
  color: var(--color-text);
}

.workspace-header,
.title-row,
.header-actions,
.panel-heading,
.zone-heading,
.score-item-heading,
.chapter-title-row,
.subpanel-title {
  display: flex;
  align-items: center;
}

.workspace-header {
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.eyebrow,
.section-kicker {
  margin: 0 0 6px;
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .12em;
  text-transform: uppercase;
}

.title-row {
  flex-wrap: wrap;
  gap: 12px;
}

.title-row h1 {
  margin: 0;
  font-size: clamp(24px, 3vw, 34px);
  line-height: 1.2;
  letter-spacing: -.03em;
}

.header-copy,
.panel-description {
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.header-actions {
  align-self: flex-start;
  gap: 8px;
}

.header-actions svg {
  width: 17px;
  height: 17px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
}

.status-pill,
.file-count,
.coverage-count,
.required-mark {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  font-weight: 700;
}

.status-pill {
  min-height: 28px;
  padding: 4px 10px;
  background: #eef2ff;
  color: #3730a3;
  font-size: 12px;
}

.status-confirmed { background: #ecfdf5; color: #047857; }
.status-blocked { background: #fef2f2; color: #b91c1c; }
.status-outdated { background: #fff7ed; color: #c2410c; }

.workflow {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  margin-bottom: 16px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-border);
}

.workflow-step {
  display: flex;
  min-width: 0;
  gap: 10px;
  align-items: center;
  padding: 14px 16px;
  background: #fff;
}

.step-index {
  display: grid;
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.workflow-step strong,
.workflow-step small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workflow-step strong { font-size: 13px; }
.workflow-step small { margin-top: 2px; color: #64748b; font-size: 11px; }
/* ==================== 现代化工作台 Tab 导航与视图样式 ==================== */
/* ==================== 实时步骤进度链样式 ==================== */
.pipeline-stepper-panel {
  margin-bottom: 20px;
  padding: 16px 20px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.stepper-bar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.stepper-bar-title h3 {
  margin: 0;
  font-size: 15px;
  color: #0f172a;
}

.pipeline-context-copy {
  max-width: 760px;
  margin: 5px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.pipeline-prerequisite-note {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: -2px 0 14px;
  padding: 9px 12px;
  border: 1px solid #c7d2fe;
  border-radius: 9px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 12px;
}

.pipeline-prerequisite-note span {
  color: #475569;
}

.stepper-right-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.log-toggle-btn {
  font-size: 12px;
  color: #4338ca;
  cursor: pointer;
}

.pipeline-state-pill {
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 700;
}
.pipeline-state-running { background: #e0e7ff; color: #4338ca; }
.pipeline-state-succeeded, .pipeline-state-completed { background: #ecfdf5; color: #047857; }
.pipeline-state-failed { background: #fef2f2; color: #b91c1c; }

.stepper-bar-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.stepper-node {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: #f8fafc;
  cursor: pointer;
  transition: all 0.2s ease;
  width: 100%;
  min-height: 104px;
  font: inherit;
  text-align: left;
}

.stepper-node:hover {
  border-color: #4338ca;
  background: #ffffff;
  box-shadow: 0 2px 8px rgba(67, 56, 202, 0.1);
}

.stepper-node.selected {
  border-color: #4338ca;
  background: #eef2ff;
  box-shadow: inset 0 0 0 1px #4338ca;
}

.stepper-node.stage-succeeded, .stepper-node.stage-reused {
  border-color: #a7f3d0;
  background: #f0fdf4;
}

.stepper-node.stage-failed {
  border-color: #fecaca;
  background: #fef2f2;
}

.stepper-node.stage-running {
  border-color: #c7d2fe;
  background: #eef2ff;
}
.stepper-node.has-warning {
  border-color: #f59e0b;
  background: #fffbeb;
}
.stepper-node.has-warning .node-badge {
  background: #d97706;
  color: #fff;
}
.stepper-node:focus-visible,
.generation-stage-button:focus-visible,
.document-toc button:focus-visible {
  outline: 3px solid rgb(79 70 229 / 28%);
  outline-offset: 2px;
}

.node-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 800;
  background: #e2e8f0;
  color: #475569;
}

.stage-succeeded .node-badge, .stage-reused .node-badge {
  background: #059669;
  color: #ffffff;
}

.stage-failed .node-badge {
  background: #dc2626;
  color: #ffffff;
}

.stage-running .node-badge {
  background: #4338ca;
  color: #ffffff;
}

.node-content strong {
  display: block;
  font-size: 13px;
  color: #1e293b;
  line-height: 1.3;
}

.node-content small {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: #64748b;
}

.node-content .stage-operation {
  margin-top: 5px;
  color: #334155;
  line-height: 1.4;
}

.node-req-tag {
  font-size: 10px;
  color: #6366f1;
  font-weight: 600;
}

/* ==================== 抽屉/浮层弹窗样式 ==================== */
.stage-drawer-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: flex-end;
  animation: drawerFadeIn 0.2s ease;
}

@keyframes drawerFadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.stage-drawer {
  width: 640px;
  max-width: 94vw;
  height: 100%;
  background: #ffffff;
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  animation: slideLeft 0.25s ease-out;
}

@keyframes slideLeft {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid #e2e8f0;
}

.drawer-header h3 {
  margin: 0;
  font-size: 18px;
  color: #0f172a;
}

.drawer-close-btn {
  display: grid;
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  place-items: center;
  border-radius: 50%;
  border: none;
  background: #f1f5f9;
  color: #64748b;
  font-size: 16px;
  cursor: pointer;
}
.drawer-close-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-width: 2;
}
.drawer-close-btn:hover { background: #e2e8f0; color: #0f172a; }

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.drawer-status-box {
  padding: 12px 16px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  margin-bottom: 18px;
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}
.drawer-timestamps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin: -8px 0 18px;
}
.drawer-timestamps > div {
  display: flex;
  gap: 6px;
  color: #64748b;
  font-size: 12px;
}
.drawer-timestamps dt { font-weight: 700; }
.drawer-timestamps dd { margin: 0; font-variant-numeric: tabular-nums; }

.current-writing-card {
  margin: 0 0 18px;
  padding: 14px 16px;
  border: 1px solid #a5b4fc;
  border-radius: 12px;
  background: linear-gradient(135deg, #eef2ff, #f8fafc);
}
.current-writing-card .section-kicker { margin-bottom: 5px; }
.current-writing-card strong {
  display: block;
  color: #312e81;
  font-size: 15px;
  line-height: 1.5;
}
.current-writing-card p:not(.section-kicker),
.current-writing-card small {
  display: block;
  margin: 6px 0 0;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
}
.current-writing-pending { border-style: dashed; }
.current-writing-paused {
  border-color: #f59e0b;
  background: #fffbeb;
}
.current-writing-error {
  margin: 8px 0 0;
  color: #b45309;
  font-size: 13px;
  line-height: 1.45;
  white-space: pre-wrap;
}

.drawer-error-alert {
  padding: 14px;
  border-radius: 10px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
  margin-bottom: 18px;
  font-size: 13px;
}
.drawer-warning-alert {
  padding: 14px;
  margin-bottom: 18px;
  border: 1px solid #fcd34d;
  border-radius: 10px;
  background: #fffbeb;
  color: #92400e;
  font-size: 13px;
}
.drawer-warning-alert ul { margin: 8px 0 0; padding-left: 18px; }
.drawer-warning-alert li { display: grid; gap: 2px; margin-top: 6px; }
.drawer-trace-panel { margin-top: 20px; }
.drawer-trace-heading,
.research-trace-summary,
.research-query-card > header,
.research-result-card > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.drawer-trace-heading h4 {
  margin: 0;
  color: #1e293b;
  font-size: 16px;
}
.drawer-trace-heading > span {
  flex: 0 0 auto;
  padding: 4px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 12px;
  font-weight: 700;
}
.trace-disclosure {
  margin: 9px 0 12px;
  padding: 10px 12px;
  border-left: 3px solid #6366f1;
  border-radius: 0 8px 8px 0;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
}
.research-trace-card {
  margin-top: 12px;
  overflow: hidden;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  background: #fff;
}
.research-trace-summary {
  padding: 14px 16px;
  border-bottom: 1px solid #e2e8f0;
  background: linear-gradient(135deg, #f8fafc, #eef2ff);
}
.trace-stage-label {
  display: block;
  margin-bottom: 3px;
  color: #4f46e5;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .08em;
}
.research-trace-summary h5 {
  margin: 0;
  color: #0f172a;
  font-size: 15px;
  line-height: 1.45;
}
.research-trace-summary small {
  display: block;
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
}
.research-decision-pill {
  flex: 0 0 auto;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.research-decision-pill.decision-search {
  background: #dbeafe;
  color: #1d4ed8;
}
.research-decision-pill.decision-skip {
  background: #e2e8f0;
  color: #475569;
}
.research-trace-steps {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 4px 16px 14px;
  list-style: none;
}
.research-trace-steps > li {
  position: relative;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
  padding-top: 14px;
}
.research-trace-steps > li:not(:last-child)::after {
  position: absolute;
  top: 38px;
  bottom: -10px;
  left: 14px;
  width: 1px;
  background: #cbd5e1;
  content: "";
}
.trace-step-index {
  position: relative;
  z-index: 1;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid #a5b4fc;
  border-radius: 50%;
  background: #eef2ff;
  color: #4338ca;
  font-size: 12px;
  font-weight: 800;
}
.research-trace-steps > li > div > strong {
  color: #1e293b;
  font-size: 13px;
}
.research-trace-steps > li > div > p {
  margin: 5px 0 0;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
}
.research-trace-steps > li > div > small {
  display: block;
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
}
.research-query-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid #dbeafe;
  border-radius: 10px;
  background: #f8fbff;
}
.research-query-card > header span {
  color: #1e40af;
  font-size: 12px;
  font-weight: 800;
}
.research-query-card > header em {
  color: #475569;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}
.research-query-card > p {
  margin: 7px 0 0;
  color: #1e293b;
  font-size: 12px;
  line-height: 1.65;
}
.research-query-card > small {
  display: block;
  margin-top: 5px;
  color: #64748b;
  font-size: 11px;
}
.research-result-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}
.research-result-card {
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}
.research-result-card.used {
  border-color: #86efac;
  background: #f0fdf4;
}
.research-result-card a,
.research-result-card header > strong {
  min-width: 0;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 750;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.research-result-card a:hover { text-decoration-thickness: 2px; }
.research-result-card a:focus-visible {
  border-radius: 3px;
  outline: 3px solid rgb(59 130 246 / 28%);
  outline-offset: 2px;
}
.research-result-card header > span {
  flex: 0 0 auto;
  padding: 3px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
}
.result-used { background: #dcfce7; color: #166534; }
.result-unused { background: #f1f5f9; color: #64748b; }
.research-result-card > small {
  display: block;
  margin-top: 5px;
  color: #64748b;
  font-size: 11px;
}
.research-result-card > p {
  margin: 7px 0 0;
  color: #334155;
  font-size: 12px;
  line-height: 1.65;
}
.research-result-card .result-usage {
  color: #166534;
  font-weight: 700;
}
.trace-empty-result {
  padding: 8px 10px;
  border-radius: 7px;
  background: #f1f5f9;
  color: #64748b !important;
}
.trace-query-error { color: #b91c1c !important; }
.trace-evidence-usage {
  display: grid;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f0fdf4;
}
.trace-evidence-usage b { color: #166534; }
.trace-evidence-usage span {
  color: #475569;
  overflow-wrap: anywhere;
}
.research-query-card .research-attempt-list {
  margin-top: 8px;
  padding-left: 18px;
}
.drawer-business-detail,
.drawer-item-list { margin-top: 20px; }
.drawer-business-detail h4,
.drawer-item-list h4 { margin: 0 0 10px; color: #1e293b; }
.drawer-business-detail dl,
.drawer-item-list article dl {
  display: grid;
  grid-template-columns: minmax(100px, .7fr) minmax(0, 1.5fr);
  gap: 8px 12px;
  margin: 0;
}
.drawer-business-detail dt,
.drawer-item-list dt { color: #64748b; font-size: 12px; }
.drawer-business-detail dd,
.drawer-item-list dd { min-width: 0; margin: 0; color: #1e293b; font-size: 12px; }
.drawer-business-detail pre {
  overflow-wrap: anywhere;
  margin: 0;
  white-space: pre-wrap;
}
.drawer-item-list article {
  padding: 12px;
  margin-top: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
}
.drawer-item-list article > header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.drawer-item-list article > header span { color: #64748b; font-size: 11px; }
.drawer-item-list article > p { color: #475569; line-height: 1.6; white-space: pre-wrap; }
.research-attempt-list { margin: 10px 0 0; padding-left: 22px; }
.research-attempt-list li { margin-top: 7px; color: #475569; font-size: 12px; }
.research-attempt-list span { margin-left: 8px; color: #92400e; }
.research-attempt-list small { display: block; margin-top: 3px; color: #64748b; }
.research-attempt-list p { margin: 3px 0 0; color: #b91c1c; }

.drawer-llm-list h4 {
  margin: 0 0 12px;
  font-size: 14px;
  color: #334155;
}

.drawer-empty-hint {
  color: #94a3b8;
  font-size: 13px;
  text-align: center;
  margin-top: 40px;
}

.workflow-tabs {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.workflow-tab-btn {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  cursor: pointer;
  text-align: left;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.workflow-tab-btn:hover {
  border-color: #cbd5e1;
  background: #f8fafc;
  transform: translateY(-1px);
}

.workflow-tab-btn.active {
  border-color: var(--color-primary, #4338ca);
  background: #ffffff;
  box-shadow: 0 4px 12px rgba(67, 56, 202, 0.12), inset 0 -3px var(--color-primary, #4338ca);
}

.tab-step-num {
  display: grid;
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 50%;
  background: #f1f5f9;
  color: #64748b;
  font-size: 13px;
  font-weight: 700;
  transition: all 0.2s ease;
}

.workflow-tab-btn.active .tab-step-num {
  background: var(--color-primary, #4338ca);
  color: #ffffff;
}

.tab-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.tab-text strong {
  font-size: 14px;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tab-text small {
  margin-top: 2px;
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tab-text .tab-action-hint {
  margin-top: 5px;
  color: #4338ca;
  font-size: 11px;
  line-height: 1.35;
}

.tab-badge {
  padding: 3px 8px;
  border-radius: 20px;
  background: #ecfdf5;
  color: #059669;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.workspace-tab-view {
  animation: tabFadeIn 0.25s ease-out;
}

@keyframes tabFadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 审阅目录顶部控制吸顶栏 */
.planning-sticky-bar {
  position: sticky;
  top: 12px;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
  padding: 14px 20px;
  border: 1px solid rgba(226, 232, 240, 0.8);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(12px);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
}

.sticky-bar-info {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.sticky-stats {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-left: 14px;
  border-left: 1px solid #e2e8f0;
  font-size: 13px;
  color: #475569;
}

.sticky-stats strong {
  color: #0f172a;
  font-size: 14px;
}

.sticky-stats .danger {
  color: #dc2626;
}
.sticky-stats .danger strong {
  color: #dc2626;
}

.sticky-bar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-success {
  background: #059669 !important;
  color: #ffffff !important;
}
.btn-success:hover {
  background: #047857 !important;
}

.support-grid-layout {
  margin-top: 20px;
}

.announcer { min-height: 0; }
.message {
  margin: 0 0 16px;
  padding: 12px 14px;
  border: 1px solid;
  border-radius: 10px;
  font-size: 13px;
}
.message.success { border-color: #a7f3d0; background: #ecfdf5; color: #065f46; }
.message.error { border-color: #fecaca; background: #fef2f2; color: #991b1b; }
.message.error > p { margin: 0; }
.error-detail-list {
  max-height: 300px;
  margin-top: 10px;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: rgba(255, 255, 255, .68);
  color: #7f1d1d;
}
.error-detail-list > strong { display: block; font-size: 12px; }
.error-detail-list ol { margin: 7px 0 0; padding-left: 20px; }
.error-detail-list li { margin-top: 6px; line-height: 1.5; }
.error-detail-list li:first-child { margin-top: 0; }
.error-detail-list b { margin-right: 5px; }
.error-detail-list span { color: #991b1b; }

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 18px;
}

.panel {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: 16px;
  background: #fff;
  box-shadow: var(--shadow-sm);
}

.upload-panel,
.pipeline-panel,
.planning-panel,
.support-panel,
.action-panel {
  padding: 22px;
}

.panel-heading {
  justify-content: space-between;
  gap: 18px;
}

.panel-heading.compact { align-items: flex-start; }
.panel-heading h2 { margin: 0; font-size: 19px; letter-spacing: -.01em; }
.file-count {
  padding: 5px 10px;
  background: #f1f5f9;
  color: #475569;
  font-size: 11px;
}

.upload-zones {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.upload-zone {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 16px;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  background: #fbfdff;
}

.upload-zone.required { border-color: #c7d2fe; background: #fafaff; }
.zone-heading { align-items: flex-start; gap: 10px; min-height: 76px; }
.zone-icon {
  display: grid;
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 9px;
  background: #eef2ff;
  color: var(--color-primary);
}
.zone-icon svg {
  width: 19px;
  height: 19px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
}
.zone-heading h3 { margin: 1px 0 4px; font-size: 14px; }
.zone-heading p { margin: 0; color: #64748b; font-size: 11px; line-height: 1.45; }
.required-mark { margin-left: 4px; padding: 2px 6px; background: #eef2ff; color: #4338ca; font-size: 9px; }

.file-picker,
.upload-button,
.primary-action,
.confirm-button,
.chat-button {
  min-height: 44px;
}

.file-picker {
  display: grid;
  margin-top: 10px;
  place-items: center;
  border: 1px dashed #a5b4fc;
  border-radius: 10px;
  background: #fff;
  color: #4338ca;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}
.file-picker:hover { border-color: var(--color-primary); background: #f5f7ff; }
.file-picker.disabled { opacity: .55; cursor: not-allowed; }

.pending-files,
.registered-files,
.evidence-list,
.chapter-objectives {
  margin: 0;
  padding: 0;
  list-style: none;
}

.pending-files,
.registered-files { margin-top: 10px; }
.pending-files li,
.registered-files li {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  padding: 8px 0;
  border-bottom: 1px solid #edf1f5;
}
.pending-files li:last-child,
.registered-files li:last-child { border-bottom: 0; }
.pending-files span:first-child,
.registered-file-name { min-width: 0; flex: 1; }
.pending-files strong,
.registered-file-name strong,
.pending-files small,
.registered-file-name small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending-files strong,
.registered-file-name strong { font-size: 11px; }
.pending-files small,
.registered-file-name small { margin-top: 2px; color: #64748b; font-size: 10px; }
.remove-file,
.text-button {
  min-height: 32px;
  padding: 2px 5px;
  border: 0;
  background: transparent;
  color: #4f46e5;
  cursor: pointer;
  font: inherit;
  font-size: 10px;
  font-weight: 700;
}
.upload-button { width: 100%; margin-top: 8px; }
.file-state-dot { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; }
.file-processed { background: #10b981; }
.file-partial { background: #f59e0b; }
.file-blocked { background: #ef4444; }
.research-check {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  color: #475569;
  font-size: 9px;
}
.research-check input { width: 14px; height: 14px; }
.research-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 0;
  padding: 9px 11px;
  border: 1px solid #c7d2fe;
  border-radius: 9px;
  background: #f5f7ff;
  color: #3730a3;
  font-size: 10px;
}
.research-summary span { color: #64748b; }
.upload-note,
.support-note {
  margin: 14px 0 0;
  color: #64748b;
  font-size: 11px;
  line-height: 1.5;
}

.action-panel { align-self: start; }
.analysis-summary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 18px 0;
}
.analysis-summary div {
  padding: 11px;
  border-radius: 10px;
  background: #f8fafc;
}
.analysis-summary span,
.analysis-summary strong { display: block; }
.analysis-summary span { color: #64748b; font-size: 10px; }
.analysis-summary strong { margin-top: 2px; font-size: 19px; }
.primary-action { width: 100%; white-space: normal; }
.action-hint { margin: 8px 0 0; color: #64748b; font-size: 11px; text-align: center; }
.inline-warning,
.zero-score-warning,
.uncovered-warning {
  border: 1px solid #fde68a;
  border-radius: 10px;
  background: #fffbeb;
  color: #92400e;
}
.inline-warning { margin-bottom: 12px; padding: 9px 10px; font-size: 11px; }
.pipeline-audit-warning { margin: 16px 0 0; }
.pipeline-audit-warning strong { display: block; }
.pipeline-audit-warning ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.pipeline-audit-warning li + li { margin-top: 3px; }
.spinner {
  width: 15px;
  height: 15px;
  border: 2px solid rgba(255, 255, 255, .45);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
.planning-actions { display: grid; gap: 8px; margin-top: 10px; }
.confirm-button { border-color: #a7f3d0; background: #ecfdf5; color: #047857; }
.delivery-list { margin: 18px 0 0; }
.delivery-list div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0;
  border-top: 1px solid #edf1f5;
  font-size: 11px;
}
.delivery-list dt { color: #64748b; }
.delivery-list dd { margin: 0; font-weight: 700; text-align: right; }

.pipeline-panel,
.planning-panel { grid-column: 1 / -1; }
.pipeline-heading { align-items: flex-end; }
.pipeline-state {
  flex: 0 0 auto;
  padding: 6px 10px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 11px;
  font-weight: 800;
}
.pipeline-state-running { background: #eef2ff; color: #4338ca; }
.pipeline-state-failed { background: #fef2f2; color: #b91c1c; }
.pipeline-state-blocked_human,
.pipeline-state-succeeded,
.pipeline-state-completed { background: #ecfdf5; color: #047857; }
.generation-workbench { display: grid; gap: 18px; }

.writer-workspace {
  display: grid;
  grid-template-columns: minmax(190px, .78fr) minmax(360px, 1.65fr) minmax(280px, 1fr);
  min-height: 560px;
  border: 1px solid var(--border-color, #dbe3ef);
  border-radius: 14px;
  overflow: hidden;
  background: #f7f9fc;
}
.writer-outline-pane, .writer-agent-pane { padding: 18px; background: #fff; }
.writer-outline-pane { border-right: 1px solid var(--border-color, #dbe3ef); }
.writer-agent-pane { border-left: 1px solid var(--border-color, #dbe3ef); display: flex; flex-direction: column; gap: 12px; }
.writer-outline-pane h3, .writer-document-pane h3, .writer-agent-pane h3 { margin: 3px 0 0; font-size: 16px; }
.writer-chapter-list { display: grid; gap: 7px; margin-top: 16px; }
.writer-word-toc { display: grid; gap: 2px; margin-top: 16px; }
.writer-toc-item { display: flex; width: 100%; gap: 7px; padding-top: 7px; padding-bottom: 7px; border: 0; border-radius: 5px; color: #27384d; background: transparent; text-align: left; cursor: pointer; line-height: 1.45; }
.writer-toc-item:hover, .writer-toc-item.active { background: #eaf0ff; color: #315bc4; }
.writer-toc-item > span { flex: 0 0 auto; font-family: 'Times New Roman', serif; }
.writer-toc-item strong { font-weight: 500; }
.writer-toc-toggle, .writer-toc-spacer { display: inline-grid; place-items: center; width: 14px; flex: 0 0 14px; }
.writer-toc-toggle { color: #66758a; font-family: sans-serif !important; cursor: pointer; }
.writer-chapter-item { width: 100%; text-align: left; border: 1px solid #e3e9f2; border-radius: 9px; padding: 10px; background: #fff; cursor: pointer; }
.writer-chapter-item:hover, .writer-chapter-item.active { border-color: #5876d9; background: #f0f4ff; }
.writer-chapter-item.writing { box-shadow: inset 3px 0 #3eaf7c; }
.writer-chapter-item span, .writer-chapter-item small { display: block; color: #66758a; font-size: 12px; margin-top: 4px; }
.writer-chapter-item span { color: #4564c6; margin-top: 0; }
.writer-chapter-item strong { display: block; line-height: 1.45; }
.writer-document-pane { display: flex; flex-direction: column; min-width: 0; background: #eef2f7; }
.writer-document-pane > header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; padding: 18px 22px 12px; }
.writer-chapter-actions { display: flex; align-items: center; gap: 8px; }
.writer-live-status { border-radius: 999px; padding: 5px 9px; background: #e3f6ed; color: #18754c; white-space: nowrap; font-size: 12px; }
.manual-evidence-badge { flex: 0 0 auto; border-radius: 999px; padding: 5px 9px; color: #92400e; background: #fef3c7; font-size: 12px; white-space: nowrap; }
.writer-preview-notice { margin: 0 22px 12px; color: #756236; font-size: 12px; }
.writer-word-canvas { flex: 1; margin: 0 22px 22px; padding: 42px 50px; background: #fff; box-shadow: 0 2px 12px #3142601a; font-family: 'SimSun', serif; line-height: 2; overflow: auto; max-height: 690px; }
.writer-word-canvas p { margin: 0 0 16px; text-indent: 2em; white-space: pre-wrap; }
.writer-preview-empty { display: grid; place-items: center; min-height: 300px; margin: 0 22px 22px; padding: 30px; color: #68788d; background: #fff; }
.agent-disclosure { margin: 0; color: #718096; font-size: 12px; line-height: 1.5; }
.agent-trace-feed, .writer-chat-history { display: grid; gap: 8px; overflow: auto; max-height: 220px; }
.agent-trace-item, .writer-chat-history article { padding: 10px; border-radius: 8px; background: #f5f8fc; font-size: 13px; }
.agent-trace-item p, .writer-chat-history p { margin: 5px 0 0; line-height: 1.5; white-space: pre-wrap; }
.agent-trace-item small { display: block; margin-top: 4px; color: #63738a; }
.writer-chat-user { background: #edf4ff !important; }
.writer-chat-assistant { background: #eff9f3 !important; }
.writer-agent-pane textarea { width: 100%; resize: vertical; box-sizing: border-box; }
.generation-heading { align-items: flex-start; }
.generation-heading p:not(.section-kicker) {
  max-width: 760px;
  margin: 7px 0 0;
  color: #64748b;
  line-height: 1.65;
}
.generation-progress-card {
  padding: 18px;
  border: 1px solid #c7d2fe;
  border-radius: 14px;
  background: linear-gradient(135deg, #eef2ff, #f8fafc);
}
.generation-progress-card > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.generation-progress-card strong { color: #3730a3; font-size: 26px; }
.generation-progress-card span,
.generation-progress-card p { color: #475569; font-size: 12px; }
.generation-progress-card progress,
.content-progress-section progress {
  width: 100%;
  height: 9px;
  margin: 10px 0 6px;
  accent-color: #4f46e5;
}
.generation-error { color: #b91c1c !important; }
.generation-flow {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 4px 0;
  margin: 0;
  overflow-x: auto;
  list-style: none;
}
.generation-flow li {
  min-width: 132px;
  flex: 1 0 132px;
  border: 1px solid #e2e8f0;
  background: #fff;
}
.generation-flow li:not(:last-child) { border-right: 0; }
.generation-flow li:first-child { border-radius: 11px 0 0 11px; }
.generation-flow li:last-child { border-radius: 0 11px 11px 0; }
.generation-stage-button {
  display: flex;
  width: 100%;
  min-height: 62px;
  align-items: center;
  gap: 7px;
  padding: 9px 8px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.generation-flow-marker {
  display: grid;
  flex: 0 0 24px;
  width: 24px;
  height: 24px;
  place-items: center;
  border-radius: 50%;
  background: #f1f5f9;
  color: #64748b;
  font-size: 10px;
  font-weight: 800;
}
.generation-stage-button > div { display: grid; min-width: 0; gap: 2px; }
.generation-flow strong { color: #1e293b; font-size: 11px; }
.generation-flow small { color: #64748b; font-size: 9px; }
.generation-stage-succeeded,
.generation-stage-reused { border-color: #a7f3d0 !important; background: #f0fdf4 !important; }
.generation-stage-succeeded .generation-flow-marker,
.generation-stage-reused .generation-flow-marker { background: #10b981 !important; color: #fff !important; }
.generation-stage-running,
.generation-stage-queued { border-color: #a5b4fc !important; background: #eef2ff !important; }
.generation-stage-running .generation-flow-marker,
.generation-stage-queued .generation-flow-marker { background: #4f46e5 !important; color: #fff !important; }
.generation-stage-failed { border-color: #fecaca !important; background: #fef2f2 !important; }
.generation-stage-button.has-warning { background: #fffbeb; }
.generation-stage-button.has-warning .generation-flow-marker { background: #d97706 !important; color: #fff !important; }
.generation-columns {
  display: grid;
  grid-template-columns: minmax(260px, .8fr) minmax(420px, 1.5fr);
  gap: 14px;
}
.generation-section,
.content-unit-detail {
  padding: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #f8fafc;
}
.generation-section > header,
.content-unit-detail > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.generation-section h3,
.content-unit-detail h3 { margin: 0; color: #1e293b; font-size: 16px; }
.generation-section header > span { color: #64748b; font-size: 10px; }
.generation-research-list {
  display: grid;
  gap: 8px;
  padding: 0;
  margin: 13px 0 0;
  list-style: none;
}
.generation-research-list li {
  display: grid;
  gap: 4px;
  padding: 10px;
  border-radius: 9px;
  background: #fff;
}
.generation-research-list strong {
  color: #334155;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.55;
}
.generation-research-list small,
.generation-empty { color: #64748b; font-size: 10px; }
.generation-research-list ol { display: grid; gap: 6px; margin: 4px 0; padding-left: 18px; }
.generation-research-list a { color: #4338ca; font-size: 10px; overflow-wrap: anywhere; }
.content-unit-list { display: grid; gap: 9px; margin-top: 12px; }
.content-unit-card {
  display: grid;
  gap: 5px;
  width: 100%;
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  text-align: left;
}
.content-unit-card:not(:disabled) { cursor: pointer; }
.content-unit-card:not(:disabled):hover,
.content-unit-card.selected { border-color: #818cf8; box-shadow: 0 6px 18px rgb(79 70 229 / 10%); }
.content-unit-card:disabled { cursor: default; opacity: .76; }
.content-unit-card strong { color: #1e293b; font-size: 12px; }
.content-unit-card small { color: #64748b; font-size: 9px; }
.content-unit-card p {
  display: -webkit-box;
  overflow: hidden;
  margin: 2px 0 0;
  color: #475569;
  font-size: 10px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.content-unit-card em { color: #b91c1c; font-size: 9px; font-style: normal; }
.content-unit-status {
  width: fit-content;
  padding: 2px 7px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
  font-size: 8px;
  font-weight: 800;
}
.content-unit-running .content-unit-status { background: #e0e7ff; color: #4338ca; }
.content-unit-completed .content-unit-status { background: #d1fae5; color: #047857; }
.content-unit-failed .content-unit-status { background: #fee2e2; color: #b91c1c; }
.content-unit-stale { border-color: #f59e0b; background: #fffbeb; }
.content-unit-stale .content-unit-status { background: #fef3c7; color: #92400e; }
.content-unit-blocks {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding: 18px;
  border-radius: 11px;
  background: #fff;
}
.content-unit-blocks p {
  margin: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.85;
  white-space: pre-wrap;
}
.content-unit-sources { margin-top: 14px; }
.content-unit-sources h4 { margin: 0 0 7px; color: #334155; font-size: 12px; }
.content-unit-sources ul { margin: 0; padding-left: 18px; }
.content-unit-sources a { color: #4338ca; font-size: 10px; }
.document-preview-panel {
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid #dbe3ee;
  border-radius: 16px;
  background: #f1f5f9;
}
.document-preview-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.document-preview-header h3 { margin: 0; color: #172033; font-size: 18px; }
.document-preview-header p:not(.section-kicker) {
  margin: 6px 0 0;
  color: #64748b;
  line-height: 1.6;
}
.document-preview-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.preview-mode-switch {
  display: inline-flex;
  padding: 3px;
  border: 1px solid #cbd5e1;
  border-radius: 9px;
  background: #fff;
}
.preview-mode-switch button {
  min-height: 34px;
  padding: 0 11px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #64748b;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.preview-mode-switch button.active { background: #4338ca; color: #fff; }
.preview-mode-switch button:focus-visible { outline: 3px solid rgb(79 70 229 / 28%); outline-offset: 2px; }
.document-risk-banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #fbbf24;
  border-radius: 10px;
  background: #fffbeb;
  color: #92400e;
}
.document-risk-banner strong,
.document-risk-banner span { display: block; }
.document-risk-banner span { margin-top: 3px; font-size: 12px; }
.document-risk-banner ul {
  display: grid;
  gap: 5px;
  max-width: 55%;
  margin: 0;
  padding: 0;
  list-style: none;
}
.document-risk-banner button {
  min-height: 36px;
  padding: 6px 9px;
  border: 1px solid #fcd34d;
  border-radius: 8px;
  background: #fff;
  color: #92400e;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  line-height: 1.45;
  text-align: left;
}
.document-risk-banner button:hover { background: #fef3c7; }
.document-risk-banner button:focus-visible {
  outline: 3px solid rgba(217, 119, 6, .24);
  outline-offset: 2px;
}
.document-preview-state {
  margin: 0;
  padding: 28px;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  background: #fff;
  color: #64748b;
  text-align: center;
}
.document-reader {
  display: grid;
  grid-template-columns: minmax(210px, 280px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
.document-toc {
  position: sticky;
  top: 16px;
  display: grid;
  max-height: calc(100vh - 80px);
  overflow-y: auto;
  padding: 14px 8px;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  background: #fff;
}
.document-toc > strong { padding: 0 12px 10px; color: #172033; }
.document-toc button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 40px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #475569;
  font-size: 12px;
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
}
.document-toc button span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.document-toc button small {
  flex: 0 0 auto;
  padding: 2px 6px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
  font-size: 10px;
}
.document-toc button:hover { background: #f8fafc; color: #312e81; }
.document-toc button.active { background: #eef2ff; color: #3730a3; font-weight: 700; }
.word-preview-canvas {
  width: min(100%, 850px);
  min-height: 1120px;
  margin: 0 auto;
  padding: 72px 78px;
  border: 1px solid #d5dbe5;
  background: #fff;
  box-shadow: 0 16px 42px rgb(15 23 42 / 10%);
  color: #111827;
  font-family: "SimSun", "Songti SC", "Noto Serif CJK SC", serif;
}
.markdown-preview-canvas {
  width: min(100%, 850px);
  min-height: 720px;
  box-sizing: border-box;
  margin: 0 auto;
  padding: 28px 32px;
  overflow: auto;
  border: 1px solid #d5dbe5;
  border-radius: 4px;
  background: #0f172a;
  box-shadow: 0 16px 42px rgb(15 23 42 / 10%);
  color: #e2e8f0;
  font: 13px/1.7 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.word-preview-canvas h1,
.word-preview-canvas h2,
.word-preview-canvas h3,
.word-preview-canvas h4,
.word-preview-canvas h5,
.word-preview-canvas h6 {
  scroll-margin-top: 24px;
  color: #111827;
  font-family: "SimHei", "Microsoft YaHei", sans-serif;
  line-height: 1.45;
}
.word-preview-canvas h1 { margin: 0 0 30px; font-size: 28px; text-align: center; }
.word-preview-canvas h2 { margin: 34px 0 18px; font-size: 23px; }
.word-preview-canvas h3 { margin: 28px 0 15px; font-size: 19px; }
.word-preview-canvas h4,
.word-preview-canvas h5,
.word-preview-canvas h6 { margin: 22px 0 12px; font-size: 16px; }
.word-preview-canvas p,
.word-preview-canvas li {
  font-size: 16px;
  line-height: 1.9;
  text-align: justify;
  white-space: pre-wrap;
}
.word-preview-canvas p { margin: 0 0 14px; text-indent: 2em; }
.word-preview-canvas ul { margin: 0 0 16px; padding-left: 2em; }
.word-table-wrap { overflow-x: auto; margin: 18px 0; }
.word-preview-canvas table {
  width: 100%;
  border-collapse: collapse;
  font-family: "Microsoft YaHei", sans-serif;
  font-size: 13px;
}
.word-preview-canvas th,
.word-preview-canvas td {
  padding: 9px 10px;
  border: 1px solid #64748b;
  line-height: 1.55;
  text-align: left;
  vertical-align: top;
}
.word-preview-canvas th { background: #f8fafc; font-weight: 700; }
.pipeline-stage-list {
  position: relative;
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}
.pipeline-stage-list::before {
  position: absolute;
  top: 22px;
  bottom: 22px;
  left: 13px;
  width: 2px;
  background: #dbe3ee;
  content: '';
}
.pipeline-stage {
  position: relative;
  display: flex;
  min-width: 0;
  gap: 13px;
  padding: 0 0 14px;
}
.pipeline-stage:last-child { padding-bottom: 0; }
.pipeline-stage::after {
  position: absolute;
  top: 13px;
  left: 26px;
  width: 18px;
  border-top: 1px solid #dbe3ee;
  content: '';
}
.pipeline-stage-index {
  position: relative;
  z-index: 1;
  display: grid;
  flex: 0 0 26px;
  width: 26px;
  height: 26px;
  place-items: center;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  color: #64748b;
  font-size: 10px;
  font-weight: 800;
}
.pipeline-stage-body {
  min-width: 0;
  flex: 1;
  min-height: 52px;
  padding: 11px 13px;
  border: 1px solid #e2e8f0;
  border-radius: 11px;
  background: #f8fafc;
}
.pipeline-stage-body strong,
.pipeline-stage-body small,
.pipeline-stage-body em {
  display: block;
}
.pipeline-stage-body strong { color: #1e293b; font-size: 12px; line-height: 1.4; }
.pipeline-stage-body small { margin-top: 3px; color: #64748b; font-size: 10px; }
.pipeline-stage-body .pipeline-llm-count { color: #4338ca; font-weight: 700; }
.pipeline-stage-body em {
  max-height: 76px;
  margin-top: 7px;
  overflow: auto;
  color: #991b1b;
  font-size: 10px;
  font-style: normal;
  line-height: 1.5;
}
.pipeline-stage-succeeded .pipeline-stage-body,
.pipeline-stage-reused .pipeline-stage-body { border-color: #a7f3d0; background: #f0fdf4; }
.pipeline-stage-succeeded .pipeline-stage-index,
.pipeline-stage-reused .pipeline-stage-index {
  border-color: #059669;
  background: #059669;
  color: #fff;
}
.pipeline-stage-running .pipeline-stage-body,
.pipeline-stage-queued .pipeline-stage-body {
  border-color: #a5b4fc;
  background: #f5f7ff;
  box-shadow: inset 0 -3px #4f46e5;
}
.pipeline-stage-running .pipeline-stage-index,
.pipeline-stage-queued .pipeline-stage-index { border-color: #4f46e5; color: #4f46e5; }
.pipeline-stage-failed .pipeline-stage-body { border-color: #fecaca; background: #fef2f2; }
.pipeline-stage-failed .pipeline-stage-index { border-color: #dc2626; color: #dc2626; }
.pipeline-stage-blocked_human .pipeline-stage-body { border-color: #fde68a; background: #fffbeb; }
.pipeline-stage-pending { opacity: .64; }
.pipeline-llm-requests { margin-top: 8px; }
.pipeline-llm-requests > summary {
  cursor: pointer;
  color: #4338ca;
  font-size: 10px;
  font-weight: 700;
}
.pipeline-llm-request {
  margin-top: 8px;
  padding: 8px;
  border: 1px solid #dbe3f0;
  border-radius: 8px;
  background: #fff;
}
.pipeline-llm-request header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pipeline-llm-request header span { font-size: 9px; font-weight: 700; }
.pipeline-llm-request .llm-request-running { color: #4338ca; }
.pipeline-llm-request .llm-request-succeeded { color: #047857; }
.pipeline-llm-request .llm-request-failed { color: #b91c1c; }
.pipeline-llm-request p { margin: 5px 0; color: #64748b; font-size: 9px; }
.pipeline-llm-request pre {
  max-height: 320px;
  margin: 0;
  padding: 8px;
  overflow: auto;
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
  font: 9px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.pipeline-products {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
}
.pipeline-product-flow {
  display: grid;
  grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
  align-items: start;
  gap: 14px;
  margin-top: 10px;
}
.pipeline-product-nav {
  display: grid;
  gap: 6px;
}
.pipeline-product-selector {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid #dbe3ee;
  border-radius: 9px;
  background: #fbfdff;
  color: #334155;
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color .16s ease, background .16s ease, transform .16s ease;
}
.pipeline-product-selector:hover { border-color: #a5b4fc; background: #f8faff; }
.pipeline-product-selector.active {
  border-color: #4f46e5;
  background: #f5f7ff;
  box-shadow: inset 3px 0 #4f46e5;
}
.pipeline-product-selector-copy { display: grid; min-width: 0; gap: 3px; }
.pipeline-product-selector strong { color: #1e293b; font-size: 12px; line-height: 1.4; }
.pipeline-product-selector small {
  display: -webkit-box;
  overflow: hidden;
  color: #64748b;
  font-size: 9px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.pipeline-product-selector em,
.pipeline-product-detail > header > span {
  flex: 0 0 auto;
  color: #047857;
  font-size: 9px;
  font-weight: 800;
  font-style: normal;
}
.pipeline-product-selector.outdated { opacity: .68; background: #f8fafc; }
.pipeline-product-selector.warning { border-color: #fde68a; background: #fffbeb; }
.pipeline-product-selector.outdated em,
.pipeline-product-detail.outdated > header > span { color: #c2410c; }
.pipeline-product-selector.warning em,
.pipeline-product-detail.warning > header > span { color: #b45309; }
.pipeline-product-detail {
  min-width: 0;
  padding: 15px;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  background: #fbfdff;
}
.pipeline-product-detail.warning { border-color: #fde68a; background: #fffbeb; }
.pipeline-product-detail > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.pipeline-product-detail > header .section-kicker { margin: 0 0 3px; font-size: 9px; }
.pipeline-product-detail > header h4 { margin: 0; color: #1e293b; font-size: 15px; }
.pipeline-product-summary,
.pipeline-empty {
  margin: 7px 0 0;
  color: #64748b;
  font-size: 11px;
  line-height: 1.5;
}
.pipeline-product-content { margin-top: 10px; }
.pipeline-product-content-label {
  margin: 0 0 6px;
  color: #475569;
  font-size: 10px;
  font-weight: 800;
}
.pipeline-product-record {
  margin-top: 5px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}
.pipeline-product-record summary {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 9px;
  color: #1e293b;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
  list-style: none;
}
.pipeline-product-record summary::-webkit-details-marker { display: none; }
.pipeline-product-record summary > span { min-width: 0; }
.pipeline-product-record summary strong {
  display: block;
  overflow: hidden;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pipeline-product-record summary small {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 8px;
  font-weight: 600;
  text-align: right;
}
.pipeline-product-record[open] { border-color: #c7d2fe; background: #f8faff; }
.pipeline-product-more {
  margin-top: 7px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  background: #fff;
}
.pipeline-product-more > summary {
  padding: 9px;
  color: #4338ca;
  cursor: pointer;
  font-size: 10px;
  font-weight: 800;
}
.pipeline-product-more > .pipeline-product-record { margin: 0 7px 7px; }
.pipeline-product-fields {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0 9px 9px;
}
.pipeline-product-fields > div {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 7px;
}
.pipeline-product-fields dt {
  color: #64748b;
  font-size: 8px;
  font-weight: 800;
}
.pipeline-product-fields dd {
  min-width: 0;
  margin: 0;
  color: #334155;
  font-size: 9px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.planning-heading { align-items: flex-end; }
.planning-metrics { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.planning-metrics span {
  min-width: 70px;
  padding: 7px 9px;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  color: #64748b;
  font-size: 10px;
  text-align: center;
}
.planning-metrics strong { display: block; color: #0f172a; font-size: 16px; }
.planning-metrics .danger { border-color: #fecaca; background: #fef2f2; color: #991b1b; }
.planning-failed-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 250px;
  margin-top: 18px;
  padding: 30px 20px;
  border: 1px dashed #fca5a5;
  border-radius: 14px;
  background: #fef2f2;
  text-align: center;
}
.failed-icon { font-size: 32px; margin-bottom: 8px; }
.planning-failed-box h3 { margin: 0 0 8px; color: #991b1b; font-size: 16px; font-weight: 700; }
.failed-msg { max-width: 540px; margin: 0 0 18px; color: #7f1d1d; font-size: 13px; line-height: 1.5; }
.failed-actions { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }

.planning-empty {
  display: grid;
  min-height: 250px;
  margin-top: 18px;
  place-items: center;
  align-content: center;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  background: #fafbfc;
  color: #64748b;
  text-align: center;
}
.planning-empty svg {
  width: 38px;
  height: 38px;
  margin-bottom: 10px;
  fill: none;
  stroke: #94a3b8;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.5;
}
.planning-empty h3 { margin: 0; color: #334155; font-size: 15px; }
.planning-empty p { max-width: 460px; margin: 7px 20px 0; font-size: 12px; }
.zero-score-warning { margin-top: 18px; padding: 16px; font-size: 13px; }
.planning-content {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  margin-top: 18px;
}
.score-filter-panel {
  min-width: 0;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  background: #fbfcfe;
}
.score-filter-panel > summary {
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 11px 14px;
  color: #1e293b;
  cursor: pointer;
  list-style: none;
}
.score-filter-panel > summary::-webkit-details-marker { display: none; }
.score-filter-panel > summary > span { display: grid; gap: 3px; min-width: 0; }
.score-filter-panel > summary strong { font-size: 13px; }
.score-filter-panel > summary small { color: #64748b; font-size: 10px; line-height: 1.4; }
.score-filter-panel > summary b {
  flex: 0 0 auto;
  color: #4338ca;
  font-size: 10px;
}
.score-filter-panel[open] > summary { border-bottom: 1px solid #e2e8f0; }
.score-list,
.outline-list {
  min-width: 0;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fbfcfe;
}
.subpanel-title { min-height: 34px; justify-content: space-between; gap: 10px; }
.subpanel-title h3 { margin: 0; font-size: 14px; }
.subpanel-title span { color: #64748b; font-size: 10px; }
.outline-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 6px; }
.outline-actions .text-button { min-height: 30px; padding: 4px 6px; font-size: 9px; }
.score-filter-panel .score-list {
  max-height: 440px;
  padding: 14px;
  overflow: auto;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.score-point-card { min-width: 0; margin-top: 8px; }
.score-item {
  display: block;
  width: 100%;
  min-height: 44px;
  margin-top: 0;
  padding: 11px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color .16s ease, background .16s ease;
}
.score-item:hover,
.score-item.selected { border-color: #818cf8; background: #f5f7ff; }
.score-item:focus-visible,
.file-picker:focus-visible,
.remove-file:focus-visible,
.text-button:focus-visible {
  outline: 3px solid rgba(79, 70, 229, .25);
  outline-offset: 2px;
}
.score-item-heading { justify-content: space-between; gap: 8px; }
.score-item-heading strong { font-size: 12px; }
.score-item-heading b { flex: 0 0 auto; color: #4338ca; font-size: 11px; }
.score-criterion {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 5px;
  color: #475569;
  font-size: 10px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.score-meta { display: flex; gap: 6px; margin-top: 7px; color: #64748b; font-size: 9px; }
.score-meta em { color: #b45309; font-style: normal; font-weight: 700; }
.condition-trace {
  margin: -1px 4px 0;
  border: 1px solid #c7d2fe;
  border-radius: 0 0 9px 9px;
  background: #fafaff;
}
.condition-trace > summary,
.chapter-requirements > summary,
.quality-gate-card > details > summary {
  padding: 8px 10px;
  color: #4338ca;
  cursor: pointer;
  font-size: 9px;
  font-weight: 800;
}
.condition-list { display: grid; gap: 8px; padding: 0 8px 8px; }
.condition-card {
  min-width: 0;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}
.condition-card header,
.quality-gate-card > header,
.chapter-requirements article header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.condition-card code,
.chapter-conditions code,
.chapter-requirements code,
.trace-relations code,
.quality-gate-card code {
  color: #475569;
  font: 8px/1.35 ui-monospace, SFMono-Regular, Consolas, monospace;
  overflow-wrap: anywhere;
}
.condition-card > strong {
  display: block;
  margin-top: 7px;
  color: #1e293b;
  font-size: 10px;
  line-height: 1.5;
}
.condition-role {
  flex: 0 0 auto;
  padding: 2px 6px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 8px;
  font-weight: 800;
}
.condition-role.role-evidence { background: #ecfdf5; color: #047857; }
.condition-role.role-constraint { background: #fff7ed; color: #c2410c; }
.condition-role.role-quality { background: #fdf4ff; color: #a21caf; }
.condition-role.role-document { background: #f1f5f9; color: #334155; }
.condition-raw {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 9px;
  line-height: 1.45;
}
.condition-card blockquote {
  margin: 8px 0 0;
  padding: 7px 8px;
  border-left: 2px solid #a5b4fc;
  background: #f8fafc;
  color: #475569;
  font-size: 9px;
  line-height: 1.5;
}
.source-location {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 8px;
  line-height: 1.45;
}
.source-location > span { color: #334155; font-weight: 800; }
.source-location code { display: block; margin-top: 2px; }
.trace-relations { display: grid; gap: 6px; margin: 9px 0 0; }
.trace-relations > div {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 7px;
}
.trace-relations dt { color: #64748b; font-size: 8px; font-weight: 800; }
.trace-relations dd { display: grid; gap: 3px; min-width: 0; margin: 0; font-size: 9px; }
.trace-relations dd span,
.trace-relations dd a { line-height: 1.4; }
.trace-relations dd a { color: #4338ca; text-decoration: none; }
.trace-relations dd a:hover { text-decoration: underline; }
.trace-missing { color: #b45309; font-style: normal; }
.outline-list { overflow: hidden; }
.chapter-row {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 8px;
  margin-top: 8px;
  margin-left: calc(var(--chapter-depth) * 22px);
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  transition: opacity .16s ease, border-color .16s ease, background .16s ease;
}
.chapter-row.highlighted { border-color: #6366f1; background: #f5f7ff; }
.chapter-row.dimmed { opacity: .42; }
.chapter-number {
  display: grid;
  width: 34px;
  height: 28px;
  place-items: center;
  border-radius: 7px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 10px;
  font-weight: 800;
}
.chapter-body { min-width: 0; }
.chapter-title-row { justify-content: space-between; gap: 8px; align-items: flex-start; }
.chapter-heading-control { display: flex; min-width: 0; align-items: flex-start; gap: 3px; }
.chapter-heading-toggle {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.chapter-heading-toggle:hover h4 { color: #4338ca; }
.chapter-title-row h4 { margin: 2px 0 0; font-size: 13px; line-height: 1.35; }
.chapter-disclosure,
.chapter-disclosure-spacer {
  display: inline-grid;
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  place-items: center;
}
.chapter-disclosure {
  margin: -3px 0 0;
  padding: 0;
  border-radius: 6px;
  background: transparent;
  color: #4338ca;
  font: 700 20px/1 sans-serif;
  transition: background .16s ease, transform .16s ease;
}
.chapter-heading-toggle:hover .chapter-disclosure { background: #eef2ff; }
.chapter-heading-toggle[aria-expanded="true"] .chapter-disclosure { transform: rotate(90deg); }
.chapter-heading-toggle:focus-visible,
.chapter-scores button:focus-visible,
.pipeline-product-record summary:focus-visible,
.pipeline-product-more > summary:focus-visible,
.pipeline-product-selector:focus-visible,
.score-filter-panel > summary:focus-visible {
  outline: 3px solid rgba(79, 70, 229, .25);
  outline-offset: 2px;
}
.coverage-count { flex: 0 0 auto; padding: 3px 7px; background: #ecfdf5; color: #047857; font-size: 9px; }
.chapter-body > p { margin: 5px 0 0; color: #64748b; font-size: 10px; line-height: 1.45; }
.chapter-scores { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.chapter-scores button {
  min-height: 36px;
  padding: 3px 7px;
  border: 0;
  border-radius: 999px;
  background: #f1f5f9;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-size: 9px;
}
.chapter-scores button:hover { background: #e2e8f0; }
.chapter-scores button.active { background: #e0e7ff; color: #3730a3; font-weight: 700; }
.chapter-objectives { margin-top: 8px; }
.chapter-objectives li { position: relative; margin-top: 3px; padding-left: 10px; color: #475569; font-size: 9px; }
.chapter-objectives li::before { position: absolute; left: 0; content: "·"; color: #818cf8; font-weight: 900; }
.chapter-conditions {
  display: grid;
  gap: 4px;
  margin-top: 9px;
  padding: 8px;
  border-radius: 8px;
  background: #f8fafc;
}
.chapter-conditions > strong,
.quality-gate-bindings > strong {
  color: #334155;
  font-size: 9px;
}
.chapter-conditions > span,
.quality-gate-bindings > span {
  color: #475569;
  font-size: 9px;
  line-height: 1.45;
}
.chapter-conditions code,
.quality-gate-bindings code { margin-right: 4px; color: #4338ca; }
.chapter-requirements {
  margin-top: 8px;
  border: 1px solid #dbe3ee;
  border-radius: 8px;
  background: #fff;
}
.chapter-requirements article {
  margin: 0 8px 8px;
  padding: 8px;
  border-radius: 7px;
  background: #f8fafc;
}
.chapter-requirements article header small {
  color: #64748b;
  font-size: 8px;
  line-height: 1.4;
  text-align: right;
}
.chapter-requirements article p {
  margin: 6px 0 0;
  color: #334155;
  font-size: 9px;
  line-height: 1.55;
}
.quality-gate-section {
  margin-top: 16px;
  padding: 14px;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  background: #f8fafc;
}
.quality-gate-section .subpanel-title > div { min-width: 0; }
.quality-gate-section .section-kicker { margin-bottom: 3px; }
.quality-gate-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 8px;
}
.quality-gate-card {
  min-width: 0;
  padding: 11px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
}
.quality-gate-card > header > span {
  color: #64748b;
  font-size: 8px;
  font-weight: 800;
}
.quality-gate-card > ul {
  margin: 8px 0 0;
  padding-left: 16px;
  color: #1e293b;
  font-size: 10px;
  line-height: 1.5;
}
.quality-gate-bindings { display: grid; gap: 4px; margin-top: 9px; }
.quality-gate-card > details { margin-top: 8px; border-top: 1px solid #edf1f5; }
.quality-gate-card > details > summary { padding-left: 0; }
.quality-gate-card > details ul {
  margin: 0;
  padding-left: 16px;
  color: #475569;
  font-size: 9px;
  line-height: 1.5;
}
.uncovered-warning { margin-top: 14px; padding: 11px 12px; font-size: 11px; }

.support-panel { min-height: 230px; }
.evidence-list { margin-top: 14px; }
.evidence-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 0;
  border-top: 1px solid #edf1f5;
}
.evidence-list li > span { min-width: 0; }
.evidence-list strong,
.evidence-list small { display: block; }
.evidence-list strong { font-size: 11px; line-height: 1.45; }
.evidence-list small { margin-top: 3px; color: #64748b; font-size: 9px; }
.evidence-list .support-empty { justify-content: center; border: 0; color: #64748b; font-size: 11px; }
.assistant-reply {
  margin: 14px 0 10px;
  padding: 11px;
  border-radius: 10px;
  background: #f1f5f9;
  color: #334155;
  font-size: 12px;
  line-height: 1.55;
}
.support-panel textarea {
  width: 100%;
  min-height: 96px;
  margin-top: 14px;
  padding: 11px 12px;
  resize: vertical;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  color: inherit;
  font: inherit;
  font-size: 12px;
  outline: none;
}
.support-panel textarea:focus { border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-primary-glow); }
.chat-button { margin-top: 9px; }
.visually-hidden {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
  border: 0 !important;
}

@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1180px) {
  .workspace-grid { grid-template-columns: 1fr; }
  .action-panel { grid-row: 2; }
  .pipeline-panel,
  .planning-panel { grid-column: auto; }
  .pipeline-product-flow { grid-template-columns: minmax(190px, 260px) minmax(0, 1fr); }
  .support-panel { min-height: 0; }
}

@media (max-width: 900px) {
  .upload-zones { grid-template-columns: 1fr; }
  .writer-workspace { grid-template-columns: 1fr; }
  .writer-outline-pane, .writer-agent-pane { border: 0; border-bottom: 1px solid var(--border-color, #dbe3ef); }
  .writer-agent-pane { border-top: 1px solid var(--border-color, #dbe3ef); border-bottom: 0; }
  .writer-word-canvas { max-height: 480px; }
  .generation-columns { grid-template-columns: 1fr; }
  .document-reader { grid-template-columns: 1fr; }
  .document-toc {
    position: static;
    max-height: 280px;
  }
  .word-preview-canvas {
    width: 100%;
    min-height: 0;
    padding: 44px 36px;
  }
  .zone-heading { min-height: 0; }
  .planning-content { grid-template-columns: 1fr; }
  .score-filter-panel .score-list { max-height: 420px; }
  .quality-gate-list { grid-template-columns: 1fr; }
}

@media (max-width: 680px) {
  .v3-workspace { padding: 18px 14px 36px; }
  .workspace-header { align-items: flex-start; flex-direction: column; }
  .header-actions { width: 100%; }
  .header-actions .btn { flex: 1; min-height: 44px; }
  .workflow-tabs { grid-template-columns: 1fr; }
  .workflow { grid-template-columns: 1fr 1fr; }
  .workflow-step { padding: 11px; }
  .upload-panel,
  .pipeline-panel,
  .planning-panel,
  .generation-workbench,
  .support-panel,
  .action-panel { padding: 16px; }
  .pipeline-heading,
  .planning-heading { align-items: flex-start; flex-direction: column; }
  .pipeline-product-flow { grid-template-columns: 1fr; }
  .planning-metrics { width: 100%; justify-content: flex-start; }
  .planning-metrics span { flex: 1 1 68px; }
  .outline-actions { width: 100%; justify-content: flex-start; }
  .document-preview-header,
  .document-risk-banner { align-items: stretch; flex-direction: column; }
  .document-risk-banner ul { width: 100%; max-width: none; }
  .document-preview-actions { width: 100%; }
  .document-preview-actions .btn { flex: 1; min-height: 44px; }
  .word-preview-canvas { padding: 30px 20px; }
  .word-preview-canvas h1 { font-size: 24px; }
  .word-preview-canvas h2 { font-size: 20px; }
  .word-preview-canvas p,
  .word-preview-canvas li { font-size: 15px; }
  .chapter-row { margin-left: calc(var(--chapter-depth) * 10px); }
  .evidence-list li { align-items: flex-start; flex-direction: column; }
}

@media (prefers-reduced-motion: reduce) {
  .score-item,
  .chapter-row,
  .chapter-disclosure,
  .stepper-node,
  .stage-drawer { transition: none; }
  .stage-drawer-overlay,
  .stage-drawer { animation: none; }
  .spinner { animation-duration: 1.6s; }
}
</style>
