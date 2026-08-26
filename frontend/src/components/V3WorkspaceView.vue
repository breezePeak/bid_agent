<template>
  <section class="v3-workspace" :aria-busy="running || uploading">
    <Teleport to="body">
      <div v-if="legacyPreviewOpen" class="legacy-preview-overlay" @click.self="closeLegacyPreview">
        <section class="legacy-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="legacy-preview-title">
          <header>
            <h3 id="legacy-preview-title">{{ legacyPreviewFilename }}</h3>
            <button type="button" @click="closeLegacyPreview">返回工作台</button>
          </header>
          <div class="legacy-preview-body">
            <p v-if="legacyPreviewLoading">正在读取拆解结果…</p>
            <p v-else-if="legacyPreviewError" class="legacy-preview-error" role="alert">{{ legacyPreviewError }}</p>
            <LegacyBidIndexPreview v-else :index="legacyPreviewIndex" />
          </div>
        </section>
      </div>
    </Teleport>

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
            <span>流程尝试 {{ selectedDrawerStage.attempt || 0 }} 次</span>
            <span>模型请求 {{ selectedDrawerStage.llm_request_count || 0 }} 次</span>
            <span v-if="selectedDrawerStage.inference_reuse_count">复用已校验模型结果 {{ selectedDrawerStage.inference_reuse_count }} 次</span>
          </div>
          <section
            v-if="selectedDrawerStage.stage_id === 'plan_response'"
            class="project-fact-drawer-panel"
            aria-labelledby="project-fact-drawer-heading"
          >
            <div class="project-fact-drawer-heading">
              <div>
                <p class="section-kicker">全局项目事实</p>
                <h4 id="project-fact-drawer-heading">
                  {{ selectedDrawerStage.status === 'reused' ? '复用已验证结果' : '本次生成明细' }}
                </h4>
              </div>
              <span v-if="selectedDrawerStage.status === 'reused'" class="project-fact-reused-pill">已验证复用</span>
            </div>
            <dl class="project-fact-metrics">
              <div><dt>输入字符</dt><dd>{{ selectedDrawerStageData.input_chars || 0 }}</dd></div>
              <div><dt>扫描原文</dt><dd>{{ selectedDrawerStageData.scanned_source_block_count || 0 }}</dd></div>
              <div><dt>送入模型</dt><dd>{{ selectedDrawerStageData.source_block_count || 0 }}</dd></div>
              <div><dt>模型请求</dt><dd>{{ selectedDrawerStageData.llm_request_count || 0 }}</dd></div>
              <div><dt>结果复用</dt><dd>{{ selectedDrawerStageData.inference_reuse_count || 0 }}</dd></div>
              <div v-if="selectedDrawerStageData.project_batch_count"><dt>项目批次</dt><dd>{{ selectedDrawerStageData.project_batch_reused_count || 0 }}/{{ selectedDrawerStageData.project_batch_count }} 已复用</dd></div>
              <div><dt>自动处理引用</dt><dd>{{ selectedDrawerStageData.normalized_reference_count || 0 }}</dd></div>
            </dl>
            <p
              v-if="selectedDrawerStage.status === 'reused'"
              class="project-fact-reuse-note"
            >当前结果经过依赖、版本和哈希校验，本次没有重新调用模型。</p>
            <p v-for="reuse in selectedDrawerStageData.inference_reuses || []" :key="reuse.checkpoint_id" class="project-fact-reuse-note">
              复用{{ reuse.batch_id ? `批次 ${reuse.batch_id}` : '已校验模型结果' }}，来源时间 {{ formatTimestamp(reuse.source_time) }}
            </p>
            <div v-if="selectedDrawerStageData.summary && Object.keys(selectedDrawerStageData.summary).length" class="project-fact-summary">
              <strong>事实摘要</strong>
              <p v-if="selectedDrawerStageData.summary.project_name">项目：{{ selectedDrawerStageData.summary.project_name }}</p>
              <p v-if="selectedDrawerStageData.summary.fact_count !== undefined">已确认事实 {{ selectedDrawerStageData.summary.fact_count }} 条</p>
              <p v-if="selectedDrawerStageData.summary.evidence_need_count !== undefined">待补证据 {{ selectedDrawerStageData.summary.evidence_need_count }} 条</p>
            </div>
            <div v-if="['failed', 'paused', 'blocked'].includes(selectedDrawerStage.status) && selectedDrawerStageData.validation_errors?.length" class="project-fact-validation-list">
              <strong>自动修复记录</strong>
              <ol>
                <li v-for="(item, index) in selectedDrawerStageData.validation_errors" :key="`${item.code || item.rule || index}-${index}`">
                  <span>第 {{ item.attempt || index + 1 }} 次</span>
                  <p>{{ item.message || item.error || item }}</p>
                </li>
              </ol>
            </div>
            <div v-if="['failed', 'paused', 'blocked'].includes(selectedDrawerStage.status)" class="project-fact-recovery-actions">
              <textarea
                v-model="projectFactFeedback"
                rows="3"
                placeholder="可选：告诉 Agent 哪些项目事实需要调整；不能覆盖招标文件中的正式要求"
              />
              <button class="btn btn-primary" type="button" :disabled="outlineActionDisabled" @click="retryProjectFacts(false)">复用结果继续处理</button>
              <button class="btn" type="button" :disabled="outlineActionDisabled || !projectFactFeedback.trim()" @click="retryProjectFacts(true)">带意见重试</button>
              <button class="btn" type="button" :disabled="outlineActionDisabled" title="会产生新的模型调用费用" @click="retryProjectFacts(false, true)">重新请求模型（产生费用）</button>
              <button class="btn" type="button" @click="closeStageDrawer">稍后处理</button>
            </div>
          </section>
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

          <div v-if="['failed', 'paused', 'blocked'].includes(selectedDrawerStage.status) && selectedDrawerStage.error?.message" class="drawer-error-alert">
            <strong>{{ selectedDrawerStage.status === 'failed' ? '错误明细：' : '需要处理：' }}</strong>
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
            <details
              v-for="(request, requestIndex) in selectedDrawerStage.llm_requests"
              :key="request.request_id"
              class="pipeline-llm-request"
              :open="shouldExpandLlmRequest(request, requestIndex, selectedDrawerStage.llm_requests.length)"
            >
              <summary>
                <strong>第 {{ request.request_index }} 次请求</strong>
                <span class="llm-request-purpose-summary">{{ llmRequestPurpose(request) }}</span>
                <span :class="`llm-request-${request.status}`">
                  {{ llmRequestStatus(request.status) }}
                </span>
                <svg class="llm-request-chevron" aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg>
              </summary>
              <div class="pipeline-llm-request-detail">
                <p class="llm-request-purpose"><strong>本次请求：</strong>{{ llmRequestPurpose(request) }}</p>
                <p>{{ llmRequestSummary(request) }}</p>
                <pre>{{ formatLlmRequest(request) }}</pre>
              </div>
            </details>
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

    <!-- 大模型调用明细与诊断 Modal 弹窗 -->
    <Teleport to="body">
      <Transition name="dialog">
      <div v-if="showLlmModal" class="llm-modal-overlay" @click.self="showLlmModal = false">
        <div class="llm-modal-content" role="dialog" aria-modal="true" aria-labelledby="llm-modal-title">
        <header class="llm-modal-header">
          <div>
            <p class="section-kicker">大模型调用轨迹与日志审计</p>
            <h3 id="llm-modal-title">大模型请求明细与内容诊断</h3>
          </div>
          <button class="dialog-close" type="button" aria-label="关闭" @click="showLlmModal = false">
            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
          </button>
        </header>

          <div class="llm-modal-body">
          <p v-if="!allLlmRequests.length" class="modal-empty-hint">
            当前任务尚未向大模型发起请求。
          </p>
          <div v-else class="llm-request-cards">
            <article
              v-for="(req, idx) in allLlmRequests"
              :key="req.request_id || idx"
              class="llm-detail-card"
              :class="`status-${req.status}`"
            >
              <header class="llm-card-header">
                <div class="llm-card-title">
                  <strong>第 {{ req.request_index || (idx + 1) }} 次大模型请求</strong>
                  <span class="llm-badge" :class="`badge-${req.status}`">
                    {{ llmRequestStatus(req.status) }}
                  </span>
                  <span class="attempt-type-tag">
                    {{ requestAttemptKindLabel(req) }}
                  </span>
                </div>
                <small v-if="req.started_at" class="llm-card-time">
                  {{ formatTimestamp(req.started_at) }}
                </small>
              </header>

              <div class="llm-card-meta">
                <span><strong>所属阶段:</strong> {{ getStageLabel(req.stage_id) }}</span>
                <span><strong>逻辑批次:</strong> <code>{{ req.parameters?.logical_batch_id || '默认批次' }}</code></span>
                <span><strong>后端 Skill / Provider:</strong> <code>{{ req.parameters?.capability_id || req.capability_id || 'planning.chapter_outline_split' }}</code></span>
              </div>

              <div class="llm-request-purpose-card">
                <strong>本次请求在做什么</strong>
                <p>{{ llmRequestPurpose(req) }}</p>
              </div>

              <!-- 如果属于受控修复或曾报错，显示校验诊断 -->
              <div v-if="req.error || req.parameters?.repair_feedback" class="llm-repair-alert">
                <strong>校验诊断 & 受控修复指导 (Diagnostic & Feedback):</strong>
                <p v-if="req.error" class="error-msg">{{ req.error }}</p>
                <pre v-if="req.parameters?.repair_feedback" class="repair-feedback-box">{{ req.parameters.repair_feedback }}</pre>
              </div>

              <!-- 可折叠展开查看请求参数 JSON -->
              <details class="llm-card-details">
                <summary>展开查看完整 Prompt 输入快照与模型参数</summary>
                <div class="llm-snapshot-view">
                  <pre>{{ formatLlmParameters(req) }}</pre>
                </div>
              </details>
            </article>
          </div>
          </div>
        </div>
      </div>
      </Transition>
    </Teleport>

    <!-- 目录进入人工确认时，明确打断对话并引导到可审核页面。 -->
    <Teleport to="body">
      <Transition name="dialog">
      <div v-if="showPlanningReviewPrompt" class="planning-review-overlay" role="presentation">
        <section
          class="planning-review-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="planning-review-title"
        >
          <button
            class="planning-review-close"
            type="button"
            aria-label="稍后审核"
            @click="dismissPlanningReviewPrompt"
          >
            ×
          </button>
          <span class="planning-review-icon" aria-hidden="true">✓</span>
          <p class="section-kicker">需要您的审核</p>
          <h2 id="planning-review-title">目录已生成，等待审核</h2>
          <p>
            请核验评分点覆盖、章节结构和响应任务，再确认目录并进入完整标书生成。
          </p>
          <dl class="planning-review-metrics">
            <div><dt>章节节点</dt><dd>{{ planningView.summary.chapter_count }}</dd></div>
            <div><dt>评分点</dt><dd>{{ planningView.summary.score_point_count }}</dd></div>
            <div><dt>已覆盖响应</dt><dd>{{ planningView.summary.covered_response_unit_count }}</dd></div>
            <div><dt>待处理覆盖</dt><dd>{{ planningView.summary.uncovered_response_unit_count }}</dd></div>
          </dl>
          <label class="planning-review-feedback">
            <span>发表修改意见（可选）</span>
            <textarea
              v-model="planningReviewFeedback"
              rows="3"
              placeholder="例如：调整目录层级，补充某评分项的响应章节"
            />
          </label>
          <div class="planning-review-actions">
            <button class="btn" type="button" @click="dismissPlanningReviewPrompt">稍后审核</button>
            <button class="btn" type="button" :disabled="running || !planningReviewFeedback.trim()" @click="submitPlanningFeedback">
              提交修改意见
            </button>
            <button class="btn btn-primary" type="button" @click="openPlanningReview">
              进入审核目录
            </button>
          </div>
        </section>
      </div>
      </Transition>
    </Teleport>

        <div v-show="activeTab === 'upload'" class="workspace-tab-view tab-upload">
      <div class="initial-chat-studio">
        <!-- 头部导航与状态概览 -->
        <header class="studio-header">
          <div class="bot-brand">
            <span class="bot-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M12 3v3M8 3h8M6 8h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z" />
                <path d="M8 13h.01M16 13h.01M9 16h6" />
              </svg>
            </span>
            <div class="bot-title-wrap">
              <div class="bot-title-row">
                <h3>AI 标书编制助手</h3>
                <span class="mode-pill" :class="projectMode === 'bid_rewrite' ? 'mode-pill-rewrite' : 'mode-pill-full'">
                  {{ projectMode === 'bid_rewrite' ? '标书改写' : '全量编写' }}
                </span>
              </div>
              <p>智能对话 · 材料投递 · 目录与正文生成</p>
            </div>
          </div>
          <div class="studio-header-stats">
            <span class="stat-tag">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6zM14 3v5h5" /></svg>
              招标文件 {{ tenderInputs.length }} 份
            </span>
            <span v-if="projectMode === 'full_write'" class="stat-tag">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h7l2 2h9v11H3z" /></svg>
              公司资料 {{ companyInputs.length }} 份
            </span>
            <span v-else class="stat-tag">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
              旧投标书 {{ legacyBidItems.length ? (legacyBidItems.length + ' 份' + (hasLegacyBid ? '已解析' : '解析中')) : '待上传' }}
            </span>
          </div>
        </header>

        <div ref="studioChatBody" class="studio-chat-body" @scroll.passive="updateChatFollowState">
          <div class="legacy-chat-stream">
          <!-- 阶段 1：材料投递引导 -->
          <div class="chat-msg bot-msg timeline-step-msg">
            <div class="msg-avatar step-avatar" aria-hidden="true">1</div>
            <div class="msg-bubble step-bubble">
              <header class="workflow-step-header">
                <div class="workflow-step-heading">
                  <span class="step-tag">阶段 1 · 材料投递</span>
                  <div>
                    <h4>投递项目材料</h4>
                  </div>
                </div>
                <span class="workflow-step-status" :class="initialMaterialsReady ? 'done' : 'pending'">
                  {{ initialMaterialsReady ? '已完成' : '待上传' }}
                </span>
              </header>

              <!-- 快捷投递区域（未进入第二阶段且无提纲时在步骤1中直接提供上传入口） -->
              <div v-if="!secondStageConfirmed && !hasOutline && !loading" class="step-upload-section">
                <div class="required-upload-zones">
                  <div
                    v-for="zone in uploadZones"
                    :key="zone.role"
                    class="required-upload-zone"
                    :class="{ complete: inputsForRole(zone.role).length }"
                  >
                    <label class="required-upload-zone-label">
                      <input
                        class="visually-hidden"
                        type="file"
                        accept=".pdf,.docx,.md,.txt"
                        multiple
                        :disabled="uploading || running"
                        @change="handleQuickUpload(zone.role, $event)"
                      />
                      <div class="zone-icon-box">
                        <svg v-if="zone.role === 'tender'" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                          <polyline points="14 2 14 8 20 8"/>
                          <line x1="16" y1="13" x2="8" y2="13"/>
                          <line x1="16" y1="17" x2="8" y2="17"/>
                          <polyline points="10 9 9 9 8 9"/>
                        </svg>
                        <svg v-else-if="zone.role === 'legacy_bid'" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2">
                          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
                        </svg>
                        <svg v-else viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2">
                          <path d="M3 6h7l2 2h9v11H3z"/>
                        </svg>
                      </div>
                      <div class="zone-info-box">
                        <div class="zone-title-line">
                          <strong>{{ zone.title }}</strong>
                          <span v-if="zone.required" class="zone-req-badge">必传</span>
                        </div>
                        <small>{{ zone.description }}</small>
                      </div>
                      <div class="zone-status-box">
                        <span v-if="inputsForRole(zone.role).length" class="zone-uploaded-tag">
                          ✓ 已上传 {{ inputsForRole(zone.role).length }} 份
                        </span>
                        <span v-else class="zone-upload-btn-text">
                          + 点击上传
                        </span>
                      </div>
                    </label>
                  </div>
                </div>
              </div>

              <!-- 已登记与解析材料列表（合并在步骤1中） -->
              <div v-if="displayInputs.length" class="step-materials-card">
                <div class="file-changes-header">
                  <span class="file-changes-title">已登记与解析材料（{{ displayInputs.length }} 份）</span>
                </div>
                <div class="file-changes-list">
                  <div v-for="item in displayInputs" :key="item.input_id" class="file-change-item">
                    <div class="file-item-left">
                      <span class="file-role-badge" :class="`role-${item.role}`">{{ roleLabel(item.role) }}</span>
                      <span class="file-path" :title="item.filename">
                        <span class="name">{{ formatFilename(item.filename) }}</span>
                      </span>
                    </div>
                    <div class="file-diff-stats">
                      <span class="diff-tag add">✓ {{ displayInputStatusLabel(item) }}</span>
                      <button
                        v-if="item.role === 'legacy_bid' && item.status === 'ready'"
                        class="legacy-preview-trigger"
                        type="button"
                        @click="openLegacyPreview(item)"
                      >查看拆解</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="initialMaterialsReady && !uploading && !secondStageConfirmed && !hasOutline && !outlineBusy && !planningReadyForReview" class="chat-msg bot-msg timeline-step-msg highlight-msg">
            <div class="msg-avatar step-avatar step-action-avatar" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            </div>
            <div class="msg-bubble action-launch-bubble">
              <header class="workflow-step-header">
                <div class="workflow-step-heading">
                  <span class="step-tag ready">材料已就绪</span>
                  <div>
                    <h4>是否继续第二阶段？</h4>
                    <p>{{ projectMode === 'bid_rewrite' ? '新招标书与旧投标书均已就绪。确认后将仅依据新招标材料生成目录。' : '招标文件和公司资质/参考资料均已上传。请确认后开始解析评分点并生成目录。' }}</p>
                  </div>
                </div>
                <span class="workflow-step-status action">
                  <span class="pulse-indicator"></span> 等待确认
                </span>
              </header>
              <div class="action-launch-actions">
                <button
                  type="button"
                  class="btn-primary-action"
                  :disabled="outlineBusy || running"
                  @click="sendPresetChat('继续第二阶段')"
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                  立即开始第二阶段（生成评分与目录）
                </button>
                <span class="action-launch-subtext">或在下方输入特定要求后发送</span>
              </div>
            </div>
          </div>

          <!-- 交互问答历史记录。每条 AI 回复原位展示处理状态与可展开详情。 -->
          <div
            v-for="turn in initialChatTurns"
            :key="turn.id"
            class="chat-msg"
            :class="turn.role === 'user' ? 'user-msg' : 'bot-msg'"
          >
            <div v-if="turn.role === 'bot'" class="msg-avatar conversation-avatar" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 3v3M8 3h8M6 8h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z" />
                <path d="M8 13h.01M16 13h.01M9 16h6" />
              </svg>
            </div>
            <div class="msg-bubble conversation-bubble">
              <button
                v-if="turn.role === 'user'"
                type="button"
                class="chat-delete-btn"
                title="删除此条对话"
                aria-label="删除此条对话"
                @click="deleteInitialChatTurn(turn)"
              >×</button>
              <p>{{ turn.content }}</p>
            </div>
          </div>

          <!-- 阶段 2 从开始、失败到完成始终只占一条 AI 消息。 -->
          <div v-if="initialMaterialsReady && secondStageConfirmed" class="chat-msg bot-msg timeline-step-msg outline-stage-msg">
            <div class="msg-avatar outline-avatar" aria-hidden="true">2</div>
            <div class="msg-bubble outline-card-bubble">
              <header class="workflow-step-header">
                <div class="workflow-step-heading">
                  <span class="step-tag" :class="{ ready: outlineProcessStatus === 'completed' }">阶段 2 · 评分与目录</span>
                  <div>
                    <h4>解析评分点并生成目录</h4>
                  </div>
                </div>
                <span class="workflow-step-status" :class="outlineStatusBadgeClass">
                  {{ outlineStatusBadgeText }}
                </span>
              </header>
              <AiProcessDisclosure
                :status="outlineProcessStatus"
                :seconds="outlineElapsedSeconds"
              >
                <div class="ai-process-overview">
                  <span>{{ outlineCompletedStageCount }}/{{ pipelineStages.length }} 步完成</span>
                  <span>{{ outlinePipelineLlmRequestCount }} 次模型调用</span>
                </div>
                <ol v-if="pipelineStages.length" class="ai-process-stage-list">
                  <li v-for="(stage, index) in pipelineStages" :key="stage.stage_id">
                    <button type="button" @click="openStageDrawer(stage)">
                      <span class="ai-stage-marker" :class="`stage-${stage.status}`" aria-hidden="true">
                        <span v-if="stage.status === 'succeeded' || stage.status === 'reused'">✓</span>
                        <span v-else-if="stage.status === 'failed'">×</span>
                        <span v-else-if="['running', 'queued', 'processing'].includes(stage.status)" class="spinner-dot"></span>
                        <span v-else>{{ index + 1 }}</span>
                      </span>
                      <span class="ai-stage-copy">
                        <strong>{{ stage.label }}</strong>
                        <small>{{ pipelineStageStatus(stage) }}</small>
                      </span>
                      <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m7.5 5 5 5-5 5" /></svg>
                    </button>
                  </li>
                </ol>
                <p v-else>正在建立阶段 2 执行队列，收到节点状态后会自动更新。</p>
              </AiProcessDisclosure>

              <template v-if="planningReadyForReview && planningStatus !== 'confirmed'">
                <details class="chat-outline-details outline-card-details">
                  <summary>点击查看详细目录（{{ flatOutline.length }} 个章节节点）</summary>
                  <div class="preview-tree-box full-outline-preview">
                    <div
                      v-for="node in flatOutline"
                      :key="node.chapter_id"
                      class="tree-preview-item"
                      :style="{ '--outline-depth': Math.max(0, (node.depth || 1) - 1) }"
                    >
                      <span class="node-num">{{ node.number }}</span>
                      <div class="node-content">
                        <strong class="node-title">{{ node.title }}</strong>
                        <small v-if="node.purpose">{{ node.purpose }}</small>
                        <span v-if="projectMode === 'bid_rewrite' && node.rewrite_mode" class="rewrite-mode-pill">
                          {{ rewriteModeLabel(node.rewrite_mode) }}
                        </span>
                        <span v-if="node.structure_origin === 'legacy_enriched'" class="legacy-origin-pill">旧目录细化</span>
                        <details v-if="projectMode === 'bid_rewrite' && (node.legacy_section_ids?.length || node.rewrite_reason || node.required_changes?.length)" class="rewrite-outline-detail">
                          <summary>改写依据</summary>
                          <small v-if="node.legacy_section_ids?.length">来源旧章节：{{ node.legacy_section_ids.join('、') }}</small>
                          <small v-if="node.rewrite_reason">{{ node.rewrite_reason }}</small>
                          <small v-for="change in node.required_changes || []" :key="change">需修改：{{ change }}</small>
                        </details>
                      </div>
                      <span v-if="node.score_point_ids?.length" class="node-coverage">
                        覆盖 {{ node.score_point_ids.length }} 项
                      </span>
                    </div>
                  </div>
                </details>
                <button class="workflow-result-link" type="button" @click="activeTab = 'planning'">
                  审阅并确认完整目录 →
                </button>
              </template>
            </div>
          </div>

          <!-- 阶段 3 也只保留一条消息，详情按需展开。 -->
          <div v-if="planningStatus === 'confirmed'" class="chat-msg bot-msg timeline-step-msg generation-stage-msg">
            <div class="msg-avatar generation-avatar" aria-hidden="true">3</div>
            <div class="msg-bubble generation-stage-bubble">
              <header class="workflow-step-header">
                <div class="workflow-step-heading">
                  <span class="step-tag" :class="{ ready: generationProcessStatus === 'completed' }">阶段 3 · 标书生成</span>
                  <div>
                    <h4>生成完整标书</h4>
                  </div>
                </div>
                <span class="workflow-step-status" :class="generationStatusBadgeClass">
                  {{ generationStatusBadgeText }}
                </span>
              </header>
              <p class="workflow-step-intro generation-stage-headline">{{ generationHeadline }}</p>

              <AiProcessDisclosure
                :status="generationProcessStatus"
                :seconds="generationElapsedSeconds"
              >
                <div class="ai-process-overview">
                  <span>{{ generationCompletedStageCount }}/{{ generationExecutionStages.length }} 步完成</span>
                  <span>{{ generationPipelineLlmRequestCount }} 次模型调用</span>
                </div>
                <ol v-if="generationExecutionStages.length" class="ai-process-stage-list">
                  <li v-for="(stage, index) in generationExecutionStages" :key="stage.stage_id">
                    <button type="button" @click="openStageDrawer(stage)">
                      <span class="ai-stage-marker" :class="`stage-${stage.status}`" aria-hidden="true">
                        <span v-if="stage.status === 'succeeded' || stage.status === 'reused'">✓</span>
                        <span v-else-if="stage.status === 'failed'">×</span>
                        <span v-else-if="['running', 'queued', 'processing'].includes(stage.status)" class="spinner-dot"></span>
                        <span v-else>{{ index + 1 }}</span>
                      </span>
                      <span class="ai-stage-copy">
                        <strong>{{ stage.label }}</strong>
                        <small>{{ pipelineStageStatus(stage) }}</small>
                      </span>
                      <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m7.5 5 5 5-5 5" /></svg>
                    </button>
                  </li>
                </ol>
                <p v-else>正在建立阶段 3 执行队列，收到节点状态后会自动更新。</p>
              </AiProcessDisclosure>
            </div>
          </div>

          <div v-if="error" class="chat-msg bot-msg status-chat-msg error-chat-msg" role="alert">
            <div class="msg-avatar status-avatar" aria-hidden="true">!</div>
            <div class="msg-bubble status-bubble">
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
          </div>
          <div v-else-if="message" class="chat-msg bot-msg status-chat-msg success-chat-msg" role="status">
            <div class="msg-avatar status-avatar" aria-hidden="true">✓</div>
            <div class="msg-bubble status-bubble"><p>{{ message }}</p></div>
          </div>

          </div>
        </div>

        <footer class="studio-input-footer">

          <section
            v-if="planningStatus === 'confirmed' && hasOutline"
            class="workbench-entry-card"
            aria-label="进入章节写作工作台"
          >
            <span class="workbench-entry-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5zM8 7h8M8 11h8M8 15h5" />
              </svg>
            </span>
            <span class="workbench-entry-copy">
              <strong>目录已确认，可以开始章节编写</strong>
              <small>进入三栏工作台：左侧目录、中间正文、右侧本章对话与公共上下文。</small>
            </span>
            <button type="button" class="workbench-entry-action" @click="openWritingWorkbench">
              进入写作工作台
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 5 7 7-7 7" /></svg>
            </button>
          </section>

          <div class="modern-input-card">
            <textarea
              v-model="initialChatInput"
              class="modern-textarea"
              rows="2"
              placeholder="说明您的标书编制需求，或点击下方 + 按钮投递项目材料…"
              @keydown.enter.exact.prevent="sendInitialChat"
            />
            <div class="modern-card-toolbar">
              <div class="toolbar-left">
                <input
                  id="quick-upload-tender"
                  class="visually-hidden"
                  type="file"
                  accept=".pdf,.docx,.md,.txt"
                  multiple
                  :disabled="uploading || running"
                  @change="handleQuickUpload('tender', $event)"
                />
                <input
                  id="quick-upload-company"
                  class="visually-hidden"
                  type="file"
                  accept=".pdf,.docx,.md,.txt"
                  multiple
                  :disabled="uploading || running"
                  @change="handleQuickUpload('company', $event)"
                />
                <input
                  id="quick-upload-legacy-bid"
                  class="visually-hidden"
                  type="file"
                  accept=".pdf,.docx,.md,.txt"
                  multiple
                  :disabled="uploading || running"
                  @change="handleQuickUpload('legacy_bid', $event)"
                />
                <div class="quick-chip-bar">
                  <label class="toolbar-chip-btn" for="quick-upload-tender" title="上传招标文件">
                    <span class="chip-icon">+</span>
                    <span>招标文件 <em class="tag-req">必传</em></span>
                  </label>
                  <label v-if="projectMode === 'full_write'" class="toolbar-chip-btn" for="quick-upload-company" title="上传公司资料">
                    <span class="chip-icon">+</span>
                    <span>公司资料</span>
                  </label>
                  <label v-else class="toolbar-chip-btn" for="quick-upload-legacy-bid" title="上传旧投标书">
                    <span class="chip-icon">+</span>
                    <span>旧投标书 <em class="tag-req">必传</em></span>
                  </label>
                </div>

                <div class="toolbar-attachment-menu">
                  <button
                    class="attachment-trigger"
                    type="button"
                    :aria-expanded="showQuickUploadMenu"
                    aria-controls="quick-upload-menu"
                    aria-label="更多材料上传选项"
                    title="更多材料上传选项"
                    @click="showQuickUploadMenu = !showQuickUploadMenu"
                  >
                    <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" /></svg>
                  </button>
                  <div v-if="showQuickUploadMenu" id="quick-upload-menu" class="quick-upload-menu" role="menu">
                    <label class="quick-upload-option" for="quick-upload-tender" role="menuitem" @click="showQuickUploadMenu = false">
                      上传招标文件 <span class="tag-req">必传</span>
                    </label>
                    <label v-if="projectMode === 'full_write'" class="quick-upload-option" for="quick-upload-company" role="menuitem" @click="showQuickUploadMenu = false">
                      上传公司资料
                    </label>
                    <label v-else class="quick-upload-option" for="quick-upload-legacy-bid" role="menuitem" @click="showQuickUploadMenu = false">
                      上传旧投标书 <span class="tag-req">必传</span>
                    </label>
                  </div>
                </div>

                <span v-if="uploadingRole" class="uploading-state-hint">
                  <span class="spinner"></span> 正在上传并解析 {{ roleLabel(uploadingRole) }}…
                </span>
              </div>

              <div class="toolbar-right">
                <button
                  class="modern-send-circle-btn"
                  type="button"
                  :disabled="!initialChatInput.trim()"
                  title="发送消息"
                  aria-label="发送消息"
                  @click="sendInitialChat"
                >
                  <span v-if="initialAsking && !initialChatInput.trim()" class="spinner-dot"></span>
                  <svg v-else aria-hidden="true" viewBox="0 0 24 24">
                    <path d="M5 12h14M12 5l7 7-7 7" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
          <p class="studio-compliance-note">注：证据缺口必须由人工提供真实的企业、人员、业绩、资质或项目材料；系统不会联网补造或代替证明文件。</p>
        </footer>
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
          <button class="btn btn-outline" type="button" @click="activeTab = 'upload'">
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 5px; vertical-align: -2px;">
              <path d="m15 18-6-6 6-6M9 12h10" />
            </svg>
            返回聊天助手
          </button>
        </div>

        <section class="writer-workspace" aria-label="标书实时写作工作区">
          <aside class="writer-outline-pane">
            <header>
              <p class="section-kicker">目录章节</p>
              <h3>标书目录</h3>
              <small>勾选章节后可一键生成；勾选大章节会包含其全部子章节。</small>
            </header>
            <div class="writer-batch-actions" role="group" aria-label="一键生成范围">
              <button
                class="btn btn-primary"
                type="button"
                :disabled="running || generationBusy || !flatOutline.length"
                @click="runDocument"
              >
                {{ runningAction === 'document' ? '正在一键生成…' : '一键生成全部' }}
              </button>
              <button
                class="btn"
                type="button"
                :disabled="running || generationBusy || !selectedGenerationChapterIds.length"
                @click="runSelectedChapters"
              >
                一键生成所选{{ selectedGenerationChapterIds.length ? ('（' + selectedGenerationChapterIds.length + '）') : '' }}
              </button>
              <button
                v-if="selectedGenerationChapterIds.length"
                class="text-button writer-clear-selection"
                type="button"
                @click="clearGenerationSelection"
              >清空</button>
            </div>
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
                <input
                  class="writer-chapter-check"
                  type="checkbox"
                  :checked="isGenerationChapterSelected(chapter.chapter_id)"
                  :aria-label="'选择' + (chapter.title || chapter.chapter_id) + '及其子章节生成'"
                  @click.stop
                  @change="toggleGenerationChapter(chapter)"
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
                <h3 :id="'writer-document-' + selectedWriterChapterId">{{ selectedWriterChapter?.title || writerUnit?.current_chapter_title || writerUnit?.title || '等待开始写作' }}</h3>
              </div>
              <div class="writer-chapter-actions">
                <span class="writer-live-status">{{ writerPhaseText }}</span>
                <button
                  class="btn btn-primary"
                  type="button"
                  :disabled="running || generationBusy || !selectedWriterChapterId"
                  @click="runSelectedChapter"
                >
                  {{ runningAction === 'selected-chapter' ? '正在生成所选…' : '生成当前章' }}
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
                <small v-if="call.runtime?.provider_id">
                  运行时：Tavily API · {{ call.runtime.ready ? '已就绪' : (call.runtime.reason || '不可用') }}
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
          <div class="view-heading-actions">
            <span class="pipeline-state" :class="`pipeline-state-${pipelineStatus}`">
              {{ pipelineStatusLabel }}
            </span>
            <button class="btn btn-outline" type="button" @click="activeTab = 'upload'">
              <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 5px; vertical-align: -2px;">
                <path d="m15 18-6-6 6-6M9 12h10" />
              </svg>
              返回聊天助手
            </button>
          </div>
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
              <em v-if="['failed', 'paused', 'blocked'].includes(stage.status) && stage.error?.message">
                {{ pipelineStageError(stage) }}
              </em>
              <details v-if="stage.llm_requests?.length" class="pipeline-llm-requests">
                <summary>查看每次请求参数</summary>
                <details
                  v-for="(request, requestIndex) in stage.llm_requests"
                  :key="request.request_id"
                  class="pipeline-llm-request"
                  :open="shouldExpandLlmRequest(request, requestIndex, stage.llm_requests.length)"
                >
                  <summary>
                    <strong>第 {{ request.request_index }} 次请求</strong>
                    <span class="llm-request-purpose-summary">{{ llmRequestPurpose(request) }}</span>
                    <span :class="`llm-request-${request.status}`">
                      {{ llmRequestStatus(request.status) }}
                    </span>
                    <svg class="llm-request-chevron" aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg>
                  </summary>
                  <div class="pipeline-llm-request-detail">
                    <p class="llm-request-purpose"><strong>本次请求：</strong>{{ llmRequestPurpose(request) }}</p>
                    <p>{{ llmRequestSummary(request) }}</p>
                    <pre>{{ formatLlmRequest(request) }}</pre>
                  </div>
                </details>
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
          <button class="btn-back-assistant" type="button" @click="activeTab = 'upload'">
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 5px; vertical-align: -2px;">
              <path d="m15 18-6-6 6-6M9 12h10" />
            </svg>
            返回聊天助手
          </button>
          <button
            v-if="!hasOutline && initialMaterialsReady && secondStageConfirmed"
            class="btn btn-primary"
            type="button"
            :disabled="outlineActionDisabled"
            @click="prepareOutline"
          >
            <span v-if="outlineBusy" class="spinner" aria-hidden="true"></span>
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
              {{ awaitingSourceOutlineConfirmation ? '确认当前目录并融合旧投标书' : '确认当前目录' }}
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
          <div class="planning-heading-right">
            <div v-if="hasScorePoints" class="planning-metrics" aria-label="目录覆盖指标">
              <span><strong>{{ formatPoints(planningView.summary.total_points) }}</strong> 总分</span>
              <span><strong>{{ planningView.summary.score_point_count }}</strong> 评分点</span>
              <span><strong>{{ planningView.summary.covered_response_unit_count }}</strong> 响应任务已覆盖</span>
              <span :class="{ danger: planningView.summary.uncovered_response_unit_count > 0 }">
                <strong>{{ planningView.summary.uncovered_response_unit_count }}</strong> 响应任务未覆盖
              </span>
            </div>
            <button class="btn-back-assistant" type="button" @click="activeTab = 'upload'">
              <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 5px; vertical-align: -2px;">
                <path d="m15 18-6-6 6-6M9 12h10" />
              </svg>
              返回聊天助手
            </button>
          </div>
        </div>

        <div v-if="['failed', 'needs_handling'].includes(pipelineStatus) && !hasOutline" class="planning-failed-box">
          <div class="failed-icon">⚠️</div>
          <h3>{{ pipelineStatus === 'needs_handling' ? '全局项目事实需要处理' : '评分目录生成中断' }}</h3>
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

        <div v-else class="planning-word-layout">
          <!-- 左侧：Word 风格目录导航窗格 -->
          <aside class="planning-nav-pane" aria-label="目录导航窗格" :style="{ width: `${planningNavWidth}px` }">
            <div class="nav-pane-header">
              <div class="nav-search-bar">
                <span class="nav-search-icon" aria-hidden="true">🔍</span>
                <input
                  v-model="outlineSearchQuery"
                  class="nav-search-input"
                  type="search"
                  placeholder="搜索章节标题 / 序号 / 目的..."
                />
                <button
                  v-if="outlineSearchQuery"
                  class="nav-search-clear"
                  type="button"
                  title="清除搜索"
                  @click="outlineSearchQuery = ''"
                >×</button>
              </div>
              <div class="nav-pane-toolbar">
                <button
                  class="nav-tool-btn"
                  type="button"
                  title="添加顶级章节"
                  @click="addRootChapter"
                >
                  <span class="tool-icon">➕</span> 新建章节
                </button>
                <div class="nav-tool-group">
                  <button
                    v-if="hasCollapsibleChapters"
                    class="nav-tool-link"
                    type="button"
                    title="全部展开"
                    @click="expandAllChapters"
                  >
                    全部展开
                  </button>
                  <button
                    v-if="hasCollapsibleChapters"
                    class="nav-tool-link"
                    type="button"
                    title="全部收起"
                    @click="collapseAllChapters"
                  >
                    全部收起
                  </button>
                  <button
                    v-if="planningView.outline?.length"
                    class="nav-tool-link"
                    type="button"
                    title="导出 Markdown 格式目录"
                    @click="exportOutlineMarkdown"
                  >
                    导出 MD
                  </button>
                </div>
              </div>
            </div>

            <!-- 树状目录导航列表 -->
            <nav class="nav-tree-container">
              <div
                v-if="!visibleNavOutline.length"
                class="nav-tree-empty"
              >
                {{ outlineSearchQuery ? '未找到匹配的章节' : '暂无目录节点' }}
              </div>
              <div
                v-for="chapter in visibleNavOutline"
                :key="chapter.chapter_id"
                class="nav-tree-row"
                :class="{
                  active: selectedOutlineChapterId === chapter.chapter_id,
                  highlighted: selectedScoreId && chapter.score_point_ids.includes(selectedScoreId),
                  dimmed: selectedScoreId && !chapter.score_point_ids.includes(selectedScoreId),
                }"
                :style="{ paddingLeft: ((chapter.depth - 1) * 16 + 8) + 'px' }"
                @click="selectedOutlineChapterId = chapter.chapter_id"
              >
                <!-- 折叠/展开三角按钮 -->
                <button
                  v-if="chapter.children?.length"
                  class="nav-tree-toggle"
                  type="button"
                  :aria-expanded="isChapterExpanded(chapter.chapter_id)"
                  :title="isChapterExpanded(chapter.chapter_id) ? '收起子章节' : '展开子章节'"
                  @click.stop="toggleChapter(chapter.chapter_id)"
                >
                  {{ isChapterExpanded(chapter.chapter_id) ? '▾' : '▸' }}
                </button>
                <span v-else class="nav-tree-spacer" aria-hidden="true"></span>

                <!-- 章节序号与标题（支持内联编辑） -->
                <div class="nav-tree-content">
                  <span class="nav-chapter-num">{{ chapter.number }}</span>
                  <template v-if="inlineEditingChapterId === chapter.chapter_id">
                    <input
                      v-model="inlineEditingTitle"
                      class="nav-inline-input"
                      autofocus
                      @click.stop
                      @keydown.enter="saveInlineEdit(chapter.chapter_id)"
                      @keydown.esc="cancelInlineEdit"
                      @blur="saveInlineEdit(chapter.chapter_id)"
                    />
                  </template>
                  <template v-else>
                    <span class="nav-chapter-title" :title="chapter.title">
                      {{ chapter.title }}
                    </span>
                  </template>
                </div>

                <!-- 评分覆盖徽标 -->
                <span
                  v-if="chapter.score_point_ids.length"
                  class="nav-coverage-badge"
                  :title="`覆盖 ${chapter.score_point_ids.length} 项评分点`"
                >
                  {{ chapter.score_point_ids.length }}
                </span>

                <!-- 悬浮快捷操作按钮组 -->
                <div class="nav-row-actions" @click.stop>
                  <button
                    class="nav-action-icon"
                    type="button"
                    title="内联重命名"
                    @click="startInlineEdit(chapter)"
                  >
                    ✏️
                  </button>
                  <button
                    class="nav-action-icon"
                    type="button"
                    title="添加子章节"
                    @click="addChildChapter(chapter.chapter_id)"
                  >
                    ➕
                  </button>
                  <button
                    class="nav-action-icon"
                    type="button"
                    title="向上移动"
                    @click="moveOutlineChapter(chapter.chapter_id, 'up')"
                  >
                    ⬆️
                  </button>
                  <button
                    class="nav-action-icon"
                    type="button"
                    title="向下移动"
                    @click="moveOutlineChapter(chapter.chapter_id, 'down')"
                  >
                    ⬇️
                  </button>
                  <button
                    class="nav-action-icon nav-action-delete"
                    type="button"
                    title="删除章节"
                    @click="deleteOutlineChapter(chapter.chapter_id)"
                  >
                    🗑️
                  </button>
                </div>
              </div>
            </nav>

            <!-- 底部辅助导航（全局质量门 / 评分点核验） -->
            <footer class="nav-pane-footer">
              <button
                v-if="planningView.quality_gates?.length"
                class="nav-footer-item"
                :class="{ active: selectedOutlineChapterId === '__quality_gates__' }"
                type="button"
                @click="selectedOutlineChapterId = '__quality_gates__'"
              >
                <span>🛡️ 全文质量门</span>
                <b class="nav-footer-badge">{{ planningView.quality_gates.length }} 项</b>
              </button>
              <button
                v-if="planningView.score_points?.length"
                class="nav-footer-item"
                :class="{ active: selectedOutlineChapterId === '__all_scores__' }"
                type="button"
                @click="selectedOutlineChapterId = '__all_scores__'"
              >
                <span>🏆 评分点全景核验</span>
                <b class="nav-footer-badge">{{ planningView.score_points.length }} 项</b>
              </button>
            </footer>
          </aside>

          <!-- 中间：左右可拖拽调整宽度分割线 -->
          <div
            class="planning-resizer"
            :class="{ 'is-active': isResizingNav }"
            title="按住左右拖拽调整目录宽度"
            @mousedown="startResizingNav"
            @touchstart.passive="startResizingNav"
          >
            <div class="resizer-handle" aria-hidden="true"></div>
          </div>

          <!-- 右侧：选中章节关联信息与编辑面板 -->
          <main class="planning-detail-pane" aria-label="章节关联信息与编辑">
            <!-- 视图 A：普通章节详情与编辑 -->
            <template v-if="currentOutlineChapter && selectedOutlineChapterId !== '__quality_gates__' && selectedOutlineChapterId !== '__all_scores__'">
              <!-- 顶部 Header 卡片 -->
              <header class="detail-header-card">
                <div class="detail-header-main">
                  <div class="detail-chapter-meta">
                    <span class="detail-depth-pill">第 {{ currentOutlineChapter.depth }} 级章节</span>
                    <span v-if="currentOutlineChapter.parent_chapter_id" class="detail-parent-hint">
                      所属上级：{{ flatOutline.find(c => c.chapter_id === currentOutlineChapter.parent_chapter_id)?.title || currentOutlineChapter.parent_chapter_id }}
                    </span>
                    <span v-if="currentOutlineChapter.score_point_ids?.length" class="coverage-count">
                      关联 {{ currentOutlineChapter.score_point_ids.length }} 项评分点
                    </span>
                  </div>
                  <h3 class="detail-title-display">
                    <span class="detail-num-tag">{{ currentOutlineChapter.number }}</span>
                    {{ currentOutlineChapter.title }}
                  </h3>
                </div>

                <div class="detail-header-actions">
                  <button
                    class="btn btn-sm"
                    type="button"
                    title="添加下属子章节"
                    @click="addChildChapter(currentOutlineChapter.chapter_id)"
                  >
                    ➕ 添加子章节
                  </button>
                  <button
                    class="btn btn-sm"
                    type="button"
                    title="上移此章节"
                    @click="moveOutlineChapter(currentOutlineChapter.chapter_id, 'up')"
                  >
                    ⬆️ 上移
                  </button>
                  <button
                    class="btn btn-sm"
                    type="button"
                    title="下移此章节"
                    @click="moveOutlineChapter(currentOutlineChapter.chapter_id, 'down')"
                  >
                    ⬇️ 下移
                  </button>
                  <button
                    class="btn btn-sm btn-danger-outline"
                    type="button"
                    title="删除本章节"
                    @click="deleteOutlineChapter(currentOutlineChapter.chapter_id)"
                  >
                    🗑️ 删除本章
                  </button>
                </div>
              </header>

              <div class="detail-sections-container">
                <!-- 1. 基本信息编辑 -->
                <section class="detail-card">
                  <div class="detail-card-header">
                    <h4>📝 基本信息</h4>
                    <span class="detail-hint-text">可直接在下方编辑章节序号、标题与编写目的</span>
                  </div>
                  <div class="detail-form-grid">
                    <label class="form-field-group">
                      <span class="field-label">章节序号</span>
                      <input
                        class="form-input"
                        type="text"
                        :value="currentOutlineChapter.number"
                        placeholder="例如：3.2 或 三、"
                        @input="updateOutlineChapter(currentOutlineChapter.chapter_id, { number: $event.target.value })"
                      />
                    </label>
                    <label class="form-field-group">
                      <span class="field-label">章节标题</span>
                      <input
                        class="form-input"
                        type="text"
                        :value="currentOutlineChapter.title"
                        placeholder="例如：技术方案与实施路径"
                        @input="updateOutlineChapter(currentOutlineChapter.chapter_id, { title: $event.target.value })"
                      />
                    </label>
                  </div>
                  <label class="form-field-group full-width">
                    <span class="field-label">编写目的与响应导向 (Purpose)</span>
                    <textarea
                      class="form-textarea"
                      rows="3"
                      :value="currentOutlineChapter.purpose"
                      placeholder="明确该章节的响应意图、核心论述重点及满足的招标文件要求..."
                      @input="updateOutlineChapter(currentOutlineChapter.chapter_id, { purpose: $event.target.value })"
                    ></textarea>
                  </label>
                </section>

                <!-- 2. 写作目标与要求 (Writing Objectives) -->
                <section class="detail-card">
                  <div class="detail-card-header">
                    <h4>🎯 写作目标与核心要点</h4>
                    <span class="detail-hint-text">指导后续 AI 正文编写，支持增删改</span>
                  </div>
                  <div v-if="currentOutlineChapter.writing_objectives?.length" class="objectives-edit-list">
                    <div
                      v-for="(obj, idx) in currentOutlineChapter.writing_objectives"
                      :key="idx"
                      class="objective-edit-item"
                    >
                      <span class="objective-index">{{ idx + 1 }}</span>
                      <input
                        class="form-input objective-input"
                        type="text"
                        :value="obj"
                        placeholder="填写具体写作目标或论述要点..."
                        @input="updateChapterObjective(currentOutlineChapter.chapter_id, idx, $event.target.value)"
                      />
                      <button
                        class="objective-remove-btn"
                        type="button"
                        title="删除该目标"
                        @click="removeChapterObjective(currentOutlineChapter.chapter_id, idx)"
                      >×</button>
                    </div>
                  </div>
                  <p v-else class="detail-empty-text">当前暂未设定写作目标，可在下方添加。</p>

                  <div class="objective-add-bar">
                    <input
                      v-model="newObjectiveText"
                      class="form-input objective-new-input"
                      type="text"
                      placeholder="输入新的写作目标/论述要点..."
                      @keydown.enter="addChapterObjective(currentOutlineChapter.chapter_id)"
                    />
                    <button
                      class="btn btn-sm btn-primary"
                      type="button"
                      :disabled="!newObjectiveText.trim()"
                      @click="addChapterObjective(currentOutlineChapter.chapter_id)"
                    >
                      ➕ 添加目标
                    </button>
                  </div>
                </section>

                <!-- 3. 直接关联评分点 (Direct Score Points) -->
                <section class="detail-card">
                  <div class="detail-card-header">
                    <h4>🏆 关联评分点</h4>
                    <span class="detail-badge-count">{{ currentOutlineChapter.direct_score_points?.length || currentOutlineChapter.score_points?.length || 0 }} 项</span>
                  </div>
                  <div
                    v-if="(currentOutlineChapter.direct_score_points?.length || currentOutlineChapter.score_points?.length)"
                    class="detail-score-list"
                  >
                    <article
                      v-for="point in (currentOutlineChapter.direct_score_points?.length ? currentOutlineChapter.direct_score_points : currentOutlineChapter.score_points)"
                      :key="point.score_point_id"
                      class="detail-score-card"
                    >
                      <div class="detail-score-head">
                        <strong>{{ point.title }}</strong>
                        <span class="detail-score-val">{{ scorePointValue(point) }}</span>
                      </div>
                      <p class="detail-score-crit">{{ point.criterion || '无评分细则说明' }}</p>
                      <div class="detail-score-footer">
                        <span class="score-depth-tag">{{ responseDepthLabel(point.response_depth) }}</span>
                        <em v-if="point.review_status !== 'confirmed'" class="review-tag">需复核</em>
                      </div>
                    </article>
                  </div>
                  <p v-else class="detail-empty-text">本章节未直接绑定评分点（可能由子章节承载响应或属于通用章节）。</p>
                </section>

                <!-- 4. 关联满分条件与落位 (Score Conditions) -->
                <section class="detail-card">
                  <div class="detail-card-header">
                    <h4>💎 关联满分条件与落位</h4>
                    <span class="detail-badge-count">{{ currentOutlineChapter.score_conditions?.length || 0 }} 项</span>
                  </div>
                  <div v-if="currentOutlineChapter.score_conditions?.length" class="detail-conditions-list">
                    <article
                      v-for="cond in currentOutlineChapter.score_conditions"
                      :key="cond.condition_id"
                      class="condition-card"
                    >
                      <header>
                        <code>{{ cond.condition_id }}</code>
                        <span :class="`condition-role role-${cond.condition_role}`">
                          {{ conditionRoleLabel(cond.condition_role) }}
                        </span>
                      </header>
                      <strong>{{ cond.normalized_condition || cond.text }}</strong>
                      <p v-if="cond.text && cond.text !== cond.normalized_condition" class="condition-raw">
                        原始拆解：{{ cond.text }}
                      </p>
                      <blockquote v-if="cond.source_excerpt">{{ cond.source_excerpt }}</blockquote>
                      <p v-if="cond.source_location?.label" class="source-location">
                        <span>来源</span> {{ cond.source_location.label }}
                        <code v-if="cond.source_location.chunk_id">{{ cond.source_location.chunk_id }}</code>
                      </p>
                    </article>
                  </div>
                  <p v-else class="detail-empty-text">当前章节无独立满分条件要求。</p>
                </section>

                <!-- 5. 关联招标文件需求原文 (Requirements) -->
                <section class="detail-card">
                  <div class="detail-card-header">
                    <h4>📄 关联招标文件需求原文</h4>
                    <span class="detail-badge-count">{{ currentOutlineChapter.requirements?.length || 0 }} 项</span>
                  </div>
                  <div v-if="currentOutlineChapter.requirements?.length" class="detail-requirements-list">
                    <article
                      v-for="req in currentOutlineChapter.requirements"
                      :key="req.requirement_id"
                      class="requirement-card"
                    >
                      <header>
                        <code>{{ req.requirement_id }}</code>
                        <small v-if="req.source_location?.label">{{ req.source_location.label }}</small>
                      </header>
                      <p>{{ req.original_text || req.normalized_requirement || '未找到关联需求原文' }}</p>
                    </article>
                  </div>
                  <p v-else class="detail-empty-text">当前章节无直接关联的需求原文条目。</p>
                </section>

                <!-- 6. 下属子章节概览（若有） -->
                <section v-if="currentOutlineChapter.children?.length" class="detail-card">
                  <div class="detail-card-header">
                    <h4>📂 下属子章节一览</h4>
                    <span class="detail-badge-count">{{ currentOutlineChapter.children.length }} 个子章节</span>
                  </div>
                  <div class="detail-subchapters-grid">
                    <button
                      v-for="child in currentOutlineChapter.children"
                      :key="child.chapter_id"
                      class="subchapter-item-btn"
                      type="button"
                      @click="selectedOutlineChapterId = child.chapter_id"
                    >
                      <span class="subchapter-num">{{ child.number }}</span>
                      <strong class="subchapter-title">{{ child.title }}</strong>
                      <span v-if="child.score_point_ids?.length" class="subchapter-score-pill">
                        覆盖 {{ child.score_point_ids.length }} 项
                      </span>
                    </button>
                  </div>
                </section>
              </div>
            </template>

            <!-- 视图 B：全文质量门全景 -->
            <template v-else-if="selectedOutlineChapterId === '__quality_gates__'">
              <div class="quality-gate-view">
                <header class="detail-header-card">
                  <div>
                    <p class="section-kicker">全局保障机制</p>
                    <h3>🛡️ 全文质量门 (Document Quality Gates)</h3>
                    <p class="panel-description">不生成机械章节，贯穿全文的质量把控要求与审核标准。</p>
                  </div>
                  <span class="detail-badge-count">{{ planningView.quality_gates.length }} 项质量门</span>
                </header>

                <div class="quality-gate-list">
                  <article
                    v-for="gate in planningView.quality_gates"
                    :id="'quality-gate-' + gate.gate_id"
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
                        <code>{{ unit.unit_id }}</code> {{ unit.title }}
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
                        <p>{{ requirement.original_text || requirement.normalized_requirement || '未找到关联需求原文' }}</p>
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
              </div>
            </template>

            <!-- 视图 C：评分点全景核验 -->
            <template v-else-if="selectedOutlineChapterId === '__all_scores__'">
              <div class="all-scores-view">
                <header class="detail-header-card">
                  <div>
                    <p class="section-kicker">逐项核验</p>
                    <h3>🏆 评分点全景核验与落位跟踪</h3>
                    <p class="panel-description">选择评分点后，左侧目录树将高亮对应覆盖的章节节点。</p>
                  </div>
                  <button
                    v-if="selectedScoreId"
                    class="btn btn-sm"
                    type="button"
                    @click="selectedScoreId = ''"
                  >
                    清除高亮筛选
                  </button>
                </header>

                <div class="all-scores-grid">
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
                      <summary>查看 {{ point.score_conditions.length }} 个满分条件与落位</summary>
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
                          <p v-if="condition.text" class="condition-raw">原始拆解：{{ condition.text }}</p>
                          <blockquote>{{ condition.source_excerpt }}</blockquote>
                          <p class="source-location">
                            <span>来源</span> {{ condition.source_location.label }}
                            <code v-if="condition.source_location.chunk_id">{{ condition.source_location.chunk_id }}</code>
                          </p>
                          <dl class="trace-relations">
                            <div>
                              <dt>响应任务</dt>
                              <dd v-if="condition.response_units.length">
                                <span v-for="unit in condition.response_units" :key="unit.unit_id">
                                  <code>{{ unit.unit_id }}</code> {{ unit.title }}
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
                                  @click="destination.type === 'chapter' && (selectedOutlineChapterId = destination.chapter_id)"
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
                </div>
              </div>
            </template>

            <!-- 视图 D：未选择章节提示 -->
            <template v-else>
              <div class="planning-empty">
                <h3>请在左侧选择章节</h3>
                <p>点击左侧目录树中的任意章节查看关联信息与在线修改，或点击左上角“➕ 新建章节”。</p>
              </div>
            </template>
            <div class="detail-pane-bottom-spacer" aria-hidden="true"></div>
          </main>
        </div>

        <div v-if="planningView.uncovered_response_units.length" class="uncovered-warning" role="alert">
          <strong>仍有 {{ planningView.uncovered_response_units.length }} 个评分响应任务未被目录或全文质量门覆盖：</strong>
          {{ planningView.uncovered_response_units.map(item => item.title).join('、') }}
        </div>
      </section>
    </div>
  </section>
</template>

<script setup>
// V3WorkspaceView - updated 2026-08-25
defineOptions({ name: 'V3WorkspaceView' })
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AiProcessDisclosure from './AiProcessDisclosure.vue'
import LegacyBidIndexPreview from './LegacyBidIndexPreview.vue'
import { confirmDialog } from '../composables/appDialog.js'
import {
  chatV3,
  confirmV3Planning,
  downloadV3Final,
  fetchV3ContentUnit,
  fetchV3DocumentPreview,
  fetchV3GenerationStage,
  fetchV3WorkspaceSnapshot,
  fetchLegacyBidIndex,
  prepareV3Outline,
  resolveV3Research,
  runV3Pipeline,
  subscribeV3Workspace,
  uploadLegacyBid,
  uploadV3Input,
} from '../api'
import {
  formatV3ApiError,
  normalizeV3WorkspaceSnapshot,
  projectV3Planning,
  v3ErrorDetails,
} from '../api/v3Contracts.js'

const props = defineProps({
  runId: { type: String, required: true },
})
const router = useRouter()

const uploadZones = computed(() => [
  {
    role: 'tender',
    title: '招标文件',
    description: '招标正文、采购需求、评分办法和补充说明。',
    required: true,
  },
  ...(projectMode.value === 'full_write' ? [{
    role: 'company',
    title: '公司资质/参考资料',
    description: '企业资质、案例、人员、产品说明和证明文件。',
    required: true,
  }] : [{
    role: 'legacy_bid',
    title: '旧投标书',
    description: '可一次选择多份旧投标书，上传后逐份自动解析。',
    required: true,
  }]),
])
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
const showQuickUploadMenu = ref(false)
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
const selectedOutlineChapterId = ref('')
const savedNavWidth = typeof window !== 'undefined' ? Number(localStorage.getItem('bid_agent_planning_nav_width')) : 320
const planningNavWidth = ref(savedNavWidth >= 200 && savedNavWidth <= 800 ? savedNavWidth : 320)
const isResizingNav = ref(false)
let resizeStartX = 0
let resizeStartWidth = 0

function startResizingNav(e) {
  isResizingNav.value = true
  resizeStartX = e.clientX ?? (e.touches && e.touches[0] ? e.touches[0].clientX : 0) ?? 0
  resizeStartWidth = planningNavWidth.value
  window.addEventListener('mousemove', onResizingNav)
  window.addEventListener('mouseup', stopResizingNav)
  window.addEventListener('touchmove', onResizingNav)
  window.addEventListener('touchend', stopResizingNav)
  if (document?.body) {
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
  }
}

function onResizingNav(e) {
  if (!isResizingNav.value) return
  const clientX = e.clientX ?? (e.touches && e.touches[0] ? e.touches[0].clientX : 0) ?? 0
  const deltaX = clientX - resizeStartX
  const newWidth = Math.max(200, Math.min(750, resizeStartWidth + deltaX))
  planningNavWidth.value = newWidth
}

function stopResizingNav() {
  if (!isResizingNav.value) return
  isResizingNav.value = false
  try {
    localStorage.setItem('bid_agent_planning_nav_width', String(planningNavWidth.value))
  } catch {}
  window.removeEventListener('mousemove', onResizingNav)
  window.removeEventListener('mouseup', stopResizingNav)
  window.removeEventListener('touchmove', onResizingNav)
  window.removeEventListener('touchend', stopResizingNav)
  if (document?.body) {
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
  }
}
const outlineSearchQuery = ref('')
const inlineEditingChapterId = ref('')
const inlineEditingTitle = ref('')
const newObjectiveText = ref('')
const pendingUploads = reactive({ tender: [], score: [], company: [] })
const waitingForOutlineCompletion = ref(false)
const activeTab = ref('upload')
const activeStageDrawerId = ref('')
const selectedContentUnitId = ref('')
const selectedContentUnitTitle = ref('')
const selectedWriterChapterId = ref('')
const selectedGenerationChapterIds = ref([])
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

const initialChatInput = ref('')
const initialChatTurns = ref([])
const secondStageConfirmed = ref(false)
const dismissedPlanningReviewOperationId = ref('')
const planningReviewFeedback = ref('')
const projectFactFeedback = ref('')
const initialPendingCount = ref(0)
const initialAsking = computed(() => initialPendingCount.value > 0)
const assistantClockNow = ref(Date.now())
const studioChatBody = ref(null)
const legacyPreviewOpen = ref(false)
const legacyPreviewLoading = ref(false)
const legacyPreviewIndex = ref(null)
const legacyPreviewFilename = ref('')
const legacyPreviewError = ref('')
let shouldAutoFollowChat = true

function updateChatFollowState() {
  const element = studioChatBody.value
  if (!element) return
  const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight
  shouldAutoFollowChat = distanceFromBottom < 96
}

async function scrollChatToLatest(force = false) {
  await nextTick()
  const element = studioChatBody.value
  if (!element || (!force && !shouldAutoFollowChat)) return
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  element.scrollTo({
    top: element.scrollHeight,
    behavior: reduceMotion ? 'auto' : 'smooth',
  })
}

async function handleQuickUpload(role, event) {
  const files = Array.from(event.target.files || [])
  if (!files.length) return
  const existingNames = new Set(displayInputs.value.map(item => canonicalInputFilename(item.filename)))
  const queuedNames = new Set()
  const accepted = files.filter((file) => {
    const key = canonicalInputFilename(file.name)
    if (!key || existingNames.has(key) || queuedNames.has(key)) return false
    queuedNames.add(key)
    return true
  })
  const duplicateCount = files.length - accepted.length
  event.target.value = ''
  if (!accepted.length) {
    message.value = '所选文件已在当前工作区中，无需重复上传。'
    return
  }
  uploadingRole.value = role
  clearError()
  message.value = ''
  let count = 0
  for (const file of accepted) {
    try {
      if (role === 'legacy_bid') await uploadLegacyBid(props.runId, file)
      else await uploadV3Input(props.runId, role, file)
      count += 1
    } catch (e) {
      reportError(e, `上传 ${file.name} 失败`)
    }
  }
  uploadingRole.value = ''
  if (count) {
    message.value = duplicateCount
      ? `成功上传 ${count} 份${roleLabel(role)}，已跳过 ${duplicateCount} 份重复文件。`
      : `成功上传 ${count} 份${roleLabel(role)}。`
    await refresh()
  }
}

function canonicalInputFilename(filename) {
  return String(filename || '')
    .trim()
    .replace(/^[a-f0-9]{24,}[_-]/i, '')
    .toLocaleLowerCase()
}

function dismissPlanningReviewPrompt() {
  dismissedPlanningReviewOperationId.value = planningReviewOperationId.value
}

function openPlanningReview() {
  dismissPlanningReviewPrompt()
  activeTab.value = 'planning'
}

async function deleteInitialChatTurn(turn) {
  if (!turn) return
  const confirmed = await confirmDialog({
    title: '删除对话',
    message: '确定删除此条对话吗？删除后无法恢复。',
    confirmText: '删除',
    cancelText: '取消',
    tone: 'danger',
  })
  if (!confirmed) return
  initialChatTurns.value = initialChatTurns.value.filter(t => t.id !== turn.id)
}

function formatFilename(name) {
  if (!name) return ''
  return String(name).replace(/^[\/\\]?[a-f0-9]{20,}_?/i, '').replace(/^[\/\\]/, '')
}

async function sendPresetChat(msg) {
  if (!msg || uploading.value || running.value || outlineBusy.value) return
  initialChatInput.value = msg
  await sendInitialChat()
}

async function sendInitialChat() {
  const msg = initialChatInput.value.trim()
  if (!msg) return
  initialChatInput.value = ''
  const startedAt = Date.now()
  const turnSuffix = `${startedAt}-${initialChatTurns.value.length}`
  const userTurn = { id: `u-${turnSuffix}`, role: 'user', content: msg }
  const assistantTurn = reactive({
    id: `a-${turnSuffix}`,
    role: 'bot',
    content: '正在处理您的问题…',
    processStatus: 'processing',
    processDetail: '请求已接收，正在检查当前工作区状态并准备回复。',
    startedAt,
    finishedAt: 0,
  })
  initialChatTurns.value.push(userTurn)
  initialChatTurns.value.push(assistantTurn)
  initialPendingCount.value += 1
  await nextTick()
  await scrollChatToLatest(true)
  try {
    const { data } = await chatV3(props.runId, msg)
    const answer = String(data?.reply || data?.answer || data?.message || '').trim()
    assistantTurn.content = answer || '暂未收到可显示的回复，请稍后重试。'
    if (data?.command) {
      secondStageConfirmed.value = true
      assistantTurn.processDetail = '主 Agent 已理解请求并启动对应工作流。'
      void refresh()
    } else {
      assistantTurn.processDetail = '主 Agent 已完成理解并返回回复。'
    }
    assistantTurn.processStatus = 'completed'
  } catch (e) {
    const detail = e?.response?.data?.message || e?.message || String(e)
    assistantTurn.content = `处理失败：${detail}`
    assistantTurn.processDetail = `请求未完成：${detail}`
    assistantTurn.processStatus = 'failed'
  } finally {
    assistantTurn.finishedAt = Date.now()
    initialPendingCount.value = Math.max(0, initialPendingCount.value - 1)
  }
}
let closeWorkspaceStream = null
let stageDetailRequestToken = 0

const selectedDrawerStage = computed(() => (
  topPipelineStages.value.find(s => s.stage_id === activeStageDrawerId.value) || null
))
// The list row is already a snapshot projection; the detail endpoint may add
// the persisted artifact summary and repair history.  Merge both without
// letting the client derive status or model-call counts.
const selectedDrawerStageData = computed(() => ({
  ...(selectedDrawerStage.value || {}),
  ...(stageDetail.value || {}),
}))

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
  if (event.key !== 'Escape') return
  if (legacyPreviewOpen.value) {
    closeLegacyPreview()
    return
  }
  if (showLlmModal.value) {
    showLlmModal.value = false
    return
  }
  if (activeStageDrawerId.value) closeStageDrawer()
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
const projectMode = computed(() => snapshot.value.profile?.project_mode || 'full_write')
const rewriteModeLabels = {
  copy: '直接复用',
  light_edit: '修改复用',
  restructure: '重组复用',
  new_write: '重新编写',
}
function rewriteModeLabel(value) {
  return rewriteModeLabels[String(value || '')] || String(value || '')
}
const legacyBidSummary = computed(() => snapshot.value.legacy_bid || {})
const workspaceName = computed(() => {
  const matched = props.runId.match(/^(.+?)_(\d{8}_\d{6})(?:_\d+)?$/)
  return matched ? matched[1].replace(/_/g, ' ') : props.runId
})
const inputs = computed(() => snapshot.value.inputs || {})
const activeInputs = computed(() => {
  const seen = new Set()
  return (inputs.value.inputs || []).filter((item) => {
    if (!item.active) return false
    const key = canonicalInputFilename(item.filename)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
})
const legacyBidItems = computed(() => legacyBidSummary.value.items || [])
const displayInputs = computed(() => [...activeInputs.value, ...legacyBidItems.value])
const tenderInputs = computed(() => activeInputs.value.filter(item => item.role === 'tender'))
const companyInputs = computed(() => activeInputs.value.filter(item => item.role === 'company'))
const hasTender = computed(() => tenderInputs.value.length > 0)
const hasCompanyMaterials = computed(() => companyInputs.value.length > 0)
const hasLegacyBid = computed(() => legacyBidSummary.value.status === 'ready')
const materialReadiness = computed(() => snapshot.value.material_readiness || {})
const materialReadinessDescription = computed(() => (
  projectMode.value === 'bid_rewrite'
    ? '请上传新招标书和一份或多份旧投标书。每个文件上传后自动解析；旧投标书不参与新目录生成。'
    : '请上传招标文件和公司资质/参考资料。两类材料都登记完成后，我会先询问您是否进入第二阶段。'
))
const initialMaterialsReady = computed(() => (
  materialReadiness.value.project_mode === projectMode.value
    ? materialReadiness.value.ready === true
    : hasTender.value && (
      projectMode.value === 'bid_rewrite' ? hasLegacyBid.value : hasCompanyMaterials.value
    )
))
const secondStageConfirmationNeeded = computed(() => (
  initialMaterialsReady.value
  && !secondStageConfirmed.value
  && !hasOutline.value
  && !outlineBusy.value
  && !planningReadyForReview.value
))
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
const defaultPlanningSummary = {
  total_points: 0,
  score_point_count: 0,
  covered_score_point_count: 0,
  uncovered_score_point_count: 0,
  response_unit_count: 0,
  covered_response_unit_count: 0,
  uncovered_response_unit_count: 0,
  chapter_count: 0,
}
const planningView = computed(() => {
  const projected = projectV3Planning(snapshot.value) || {}
  return {
    outdated: false,
    score_model: {},
    blueprint: {},
    score_points: [],
    score_conditions: [],
    response_units: [],
    requirements: [],
    quality_gates: [],
    outline: [],
    uncovered_score_points: [],
    uncovered_response_units: [],
    ...projected,
    summary: {
      ...defaultPlanningSummary,
      ...(projected.summary || {}),
    },
  }
})
const flatOutline = computed(() => {
  const result = []
  const append = chapters => {
    for (const chapter of chapters || []) {
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
  const status = String(writingPhaseState.value.phase_status || 'not_started')
  if (status === 'in_progress') {
    return currentGenerationStage.value?.label
      ? `正在执行“${currentGenerationStage.value.label}”。`
      : '正在编写章节正文。'
  }
  if (status === 'running') {
    return currentGenerationStage.value?.label
      ? `正在执行“${currentGenerationStage.value.label}”，页面每 2 秒自动更新。`
      : '任务已启动，正在等待后端阶段状态。'
  }
  if (status === 'outdated') return '写作结果已过期，需要根据新的业务输入重新生成。'
  if (status === 'completed') return '全部阶段已完成，可查看章节正文并下载 Word。'
  if (status === 'blocked') return `已暂停，等待处理：${writingPhaseState.value.message || generationErrorMessage.value}`
  if (status === 'failed') return `生成已停止：${writingPhaseState.value.error?.message || writingPhaseState.value.message || generationErrorMessage.value}`
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
const workflow = computed(() => snapshot.value.workflow || {})
const phaseStates = computed(() => workflow.value.phase_states || {})
const planningPhaseState = computed(() => phaseStates.value.planning || { phase_status: 'not_started' })
const writingPhaseState = computed(() => phaseStates.value.writing || { phase_status: 'not_started' })
const workflowIsWriting = computed(() => !['not_started', 'ready', 'blocked'].includes(
  String(writingPhaseState.value.phase_status || 'not_started'),
))
const pendingReviews = computed(() => workflow.value.pending_reviews || [])
const planningStatus = computed(() => planning.value.status || 'not_ready')
const deliveryStatus = computed(() => document.value.delivery?.status || 'new')
const deliveryReady = computed(() => (
  deliveryStatus.value === 'ready'
  && Number(generationContent.value.stale_units || 0) === 0
))
const hasScorePoints = computed(() => planningView.value.summary.score_point_count > 0)
const hasOutline = computed(() => (
  // A later document-generation failure must not make an already confirmed
  // outline disappear.  `workflow.status` represents the latest workflow
  // operation and may therefore be `failed` while the confirmed blueprint and
  // its chapter workspaces are still valid.
  (
    ['needs_human', 'confirmed'].includes(planningStatus.value)
    || workflowIsWriting.value
  )
  && planningView.value.summary.chapter_count > 0
))
watch(
  () => [
    deliveryStatus.value,
    generation.value.operation_id,
    generationContent.value.stale_units,
  ],
  ([status]) => {
    if (
      status === 'ready'
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
const activeBlueprintPlanningModel = computed(() => String(
  snapshot.value.analysis?.chapter_blueprint?.planning_model || '',
))
const awaitingSourceOutlineConfirmation = computed(() => (
  projectMode.value === 'bid_rewrite'
  && activeBlueprintPlanningModel.value !== 'rewrite_merge'
))
const rawPipelineStages = computed(() => (
  workflow.value.phase === 'planning' || workflow.value.phase === 'planning_review'
    ? (workflow.value.stages || analysisPipeline.value.stages || [])
    : (analysisPipeline.value.stages || [])
))
const pipelineStages = computed(() => {
  return rawPipelineStages.value
})
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
const pipelineStatus = computed(() => {
  const workflowStatus = String(workflow.value.status || '')
  // Once the directory is confirmed, a failure belongs to the generation
  // workflow (stage 3), not to score analysis / directory generation (stage
  // 2).  Do not overwrite the stage-2 timeline with that later failure.
  if (workflowStatus === 'failed' && planningStatus.value !== 'confirmed') return 'failed'
  if (workflowStatus === 'needs_handling') return 'needs_handling'
  if (workflowStatus === 'blocked_human') return 'blocked_human'
  if (workflowStatus === 'running') return 'running'
  const status = String(analysisPipeline.value.status || 'not_started')
  if (pipelineStages.value.some(stage => ['running', 'queued'].includes(stage.status))) {
    return 'running'
  }
  if (pipelineStages.value.some(stage => stage.status === 'failed')) return 'failed'
  if (pipelineStages.value.some(stage => ['paused', 'blocked'].includes(stage.status))) return 'needs_handling'
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
  || !initialMaterialsReady.value
  || !secondStageConfirmed.value
))

// 过程与大模型监控相关
const showLlmModal = ref(false)
const runningDurationSeconds = ref(0)
let runningTimer = null
let assistantClockTimer = null

const allLlmRequests = computed(() => {
  const result = []
  const stages = showGenerationPipeline.value
    ? generationExecutionStages.value
    : pipelineStages.value
  for (const stage of stages) {
    if (Array.isArray(stage.llm_requests)) {
      for (const req of stage.llm_requests) {
        result.push({
          ...req,
          stage_id: stage.stage_id,
          stage_label: stage.label,
        })
      }
    }
  }
  return result
})

const latestLlmRequest = computed(() => (
  allLlmRequests.value.length > 0
    ? allLlmRequests.value[allLlmRequests.value.length - 1]
    : null
))

function requestAttemptKindLabel(req) {
  if (!req) return '初始尝试'
  const kind = req.parameters?.attempt_kind || req.attempt_kind
  const attemptIndex = req.parameters?.candidate_attempt || req.attempt_index || 1
  if (kind === 'controlled_repair') {
    return `第 ${attemptIndex} 次受控修复 (Controlled Repair)`
  }
  return `第 ${attemptIndex} 次初始生成`
}

function getStageLabel(stageId) {
  const stage = [...pipelineStages.value, ...generationStages.value].find(
    item => item.stage_id === stageId,
  )
  return stage ? stage.label : (stageId || '目录生成阶段')
}

function formatLlmParameters(req) {
  if (!req) return ''
  try {
    return JSON.stringify(req.parameters || req, null, 2)
  } catch (e) {
    return String(req.parameters || req)
  }
}
const showGenerationPipeline = computed(() => (
  hasOutline.value
  && planningStatus.value === 'confirmed'
  && (
    generationBusy.value
    || ['failed', 'blocked', 'succeeded'].includes(String(generation.value.status || ''))
  )
))
// Once Step 3 is visible, Step 2 is necessarily complete and its launch CTA
// must never be rendered again. Planning status and chapter data cover both
// current and legacy snapshots before generation starts.
const planningReadyForReview = computed(() => (
  hasOutline.value
  || ['needs_human', 'confirmed'].includes(planningStatus.value)
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
const outlineCompletedStageCount = computed(() => pipelineStages.value.filter(
  stage => ['succeeded', 'reused', 'completed'].includes(stage.status),
).length)
const generationCompletedStageCount = computed(() => generationExecutionStages.value.filter(
  stage => ['succeeded', 'reused', 'completed'].includes(stage.status),
).length)
const outlinePipelineLlmRequestCount = computed(() => pipelineStages.value.reduce(
  (total, stage) => total + Number(stage.llm_request_count || 0),
  0,
))
const generationPipelineLlmRequestCount = computed(() => generationExecutionStages.value.reduce(
  (total, stage) => total + Number(stage.llm_request_count || 0),
  0,
))

function timestampMilliseconds(value) {
  const timestamp = Date.parse(String(value || ''))
  return Number.isFinite(timestamp) ? timestamp : 0
}

function workflowElapsedSeconds(stages, isRunning, fallbackSeconds = 0, operation = null) {
  const startedAt = (stages || [])
    .map(stage => timestampMilliseconds(stage?.started_at))
    .filter(Boolean)
  if (!startedAt.length) {
    if (operation && (operation.started_at || operation.created_at)) {
      const opStart = timestampMilliseconds(operation.started_at || operation.created_at)
      const opEnd = isRunning ? assistantClockNow.value : timestampMilliseconds(operation.completed_at || operation.updated_at || operation.created_at)
      if (opStart && opEnd) {
        return Math.max(0, Math.floor((opEnd - opStart) / 1000))
      }
    }
    return fallbackSeconds
  }
  const completedAt = (stages || [])
    .map(stage => timestampMilliseconds(stage?.completed_at))
    .filter(Boolean)
  const end = isRunning ? assistantClockNow.value : (Math.max(...completedAt, ...startedAt))
  return Math.max(0, Math.floor((end - Math.min(...startedAt)) / 1000))
}

function assistantTurnElapsedSeconds(turn) {
  const startedAt = Number(turn?.startedAt) || assistantClockNow.value
  const finishedAt = Number(turn?.finishedAt) || assistantClockNow.value
  return Math.max(0, Math.floor((finishedAt - startedAt) / 1000))
}

function phaseProcessStatus(status) {
  if (['running', 'in_progress'].includes(status)) return 'processing'
  if (status === 'completed') return 'completed'
  if (['failed', 'blocked', 'outdated'].includes(status)) return 'failed'
  return 'waiting'
}

const outlineProcessStatus = computed(() => {
  return phaseProcessStatus(String(planningPhaseState.value.phase_status || 'not_started'))
})

const generationProcessStatus = computed(() => {
  return phaseProcessStatus(String(writingPhaseState.value.phase_status || 'not_started'))
})

const outlineElapsedSeconds = computed(() => {
  const isRunning = outlineProcessStatus.value === 'processing' || outlineBusy.value
  const stages = pipelineStages.value || []
  const operation = planningPhaseState.value || null
  const fallback = runningDurationSeconds.value || (isRunning ? 1 : null)
  const calc = workflowElapsedSeconds(stages, isRunning, fallback, operation)
  if (isRunning) {
    const _now = assistantClockNow.value
    return Math.max(1, calc || runningDurationSeconds.value || 1)
  }
  return calc ?? planningPhaseState.value.elapsed_seconds ?? null
})

const generationElapsedSeconds = computed(() => {
  const isRunning = generationProcessStatus.value === 'processing' || generationBusy.value
  const stages = generationExecutionStages.value || []
  const operation = writingPhaseState.value || null
  const fallback = runningDurationSeconds.value || (isRunning ? 1 : null)
  const calc = workflowElapsedSeconds(stages, isRunning, fallback, operation)
  if (isRunning) {
    const _now = assistantClockNow.value
    return Math.max(1, calc || runningDurationSeconds.value || 1)
  }
  return calc ?? writingPhaseState.value.elapsed_seconds ?? null
})

watch(
  () => (
    outlineBusy.value
    || generationBusy.value
    || (running.value && ['outline', 'document'].includes(runningAction.value))
    || ['running', 'processing'].includes(pipelineStatus.value)
  ),
  (isBusy, wasBusy) => {
    if (isBusy) {
      if (!wasBusy) runningDurationSeconds.value = 0
      if (!runningTimer) {
        runningTimer = window.setInterval(() => {
          runningDurationSeconds.value += 1
        }, 1000)
      }
    } else {
      if (runningTimer) {
        window.clearInterval(runningTimer)
        runningTimer = null
      }
    }
  },
  { immediate: true },
)

watch(
  () => (
    initialAsking.value
    || outlineBusy.value
    || generationBusy.value
    || outlineProcessStatus.value === 'processing'
    || generationProcessStatus.value === 'processing'
    || (running.value && ['outline', 'document'].includes(runningAction.value))
    || ['running', 'processing'].includes(pipelineStatus.value)
  ),
  isActive => {
    if (isActive && !assistantClockTimer) {
      assistantClockNow.value = Date.now()
      assistantClockTimer = window.setInterval(() => {
        assistantClockNow.value = Date.now()
      }, 1000)
    } else if (!isActive && assistantClockTimer) {
      window.clearInterval(assistantClockTimer)
      assistantClockTimer = null
    }
  },
  { immediate: true },
)
const showOutlineProcessMessage = computed(() => (
  hasOutline.value
  || planningReadyForReview.value
  || outlineBusy.value
  || pipelineStages.value.some(stage => stage.status !== 'pending')
))
const outlineWorkflowTitle = computed(() => {
  const status = String(planningPhaseState.value.phase_status || 'not_started')
  if (['not_started', 'ready'].includes(status)) return '等待进入第二阶段'
  if (outlineProcessStatus.value === 'processing') return '正在解析评分点并生成目录'
  if (outlineProcessStatus.value === 'failed') return '评分点解析与目录生成失败'
  if (status === 'waiting_confirmation') return '编写计划已生成，等待您审核'
  return `编写计划已生成（${planningView.value.summary.chapter_count} 个章节节点）`
})
const outlineWorkflowDescription = computed(() => {
  const status = String(planningPhaseState.value.phase_status || 'not_started')
  if (['not_started', 'ready'].includes(status)) return '材料准备完成后即可开始解析评分点并生成目录。'
  if (outlineProcessStatus.value === 'processing') return '正在解析招标要求、评分点并生成章节目录。'
  if (outlineProcessStatus.value === 'failed') return '请展开处理详情查看失败节点；修正后在对话中回复“继续”即可恢复。'
  return `已识别 ${planningView.value.summary.score_point_count} 个评分点和 ${planningView.value.summary.response_unit_count} 个响应任务。`
})
const outlineStatusBadgeClass = computed(() => {
  if (outlineProcessStatus.value === 'completed') return 'done'
  if (outlineProcessStatus.value === 'failed') return 'failed'
  if (outlineProcessStatus.value === 'processing' || outlineBusy.value) return 'action'
  return 'pending'
})
const outlineStatusBadgeText = computed(() => {
  if (outlineProcessStatus.value === 'completed') return '已完成'
  if (outlineProcessStatus.value === 'failed') return '失败'
  if (outlineProcessStatus.value === 'processing' || outlineBusy.value) return '处理中'
  return '待执行'
})

const generationStatusBadgeClass = computed(() => {
  if (generationProcessStatus.value === 'completed') return 'done'
  if (generationProcessStatus.value === 'failed') return 'failed'
  if (generationProcessStatus.value === 'processing' || generationBusy.value) return 'action'
  return 'pending'
})
const generationStatusBadgeText = computed(() => {
  if (generationProcessStatus.value === 'completed') return '已完成'
  if (generationProcessStatus.value === 'failed') return '失败'
  if (generationProcessStatus.value === 'processing' || generationBusy.value) return '生成中'
  return '待生成'
})
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
    ? '使用已经确认的评分目录，检查材料与证据缺口、锁定文档结构，再执行逐章写作、全文整合、质量审核和 Word 交付。'
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
const activePipelineStage = computed(() => (
  pipelineStages.value.find(stage => ['running', 'queued'].includes(stage.status))
))
const outlineRunningLabel = computed(() => (
  activePipelineStage.value?.label
    ? `正在${activePipelineStage.value.label}…`
    : '正在生成评分目录…'
))
const pipelineStatusLabel = computed(() => {
  const label = {
    not_started: '尚未开始',
    pending: '等待执行',
    queued: '已排队',
    processing: '正在处理',
    running: '正在处理',
    failed: '已失败',
    blocked_human: '等待人工确认',
    succeeded: '已完成',
    completed: '已完成',
  }[pipelineStatus.value] || pipelineStatus.value
  return ['processing', 'running'].includes(pipelineStatus.value)
    ? `${label} · 已运行 ${formatPipelineDuration(runningDurationSeconds.value)}`
    : label
})
const topPipelineStatus = computed(() => (
  showGenerationPipeline.value
    ? (generationBusy.value ? 'running' : String(generation.value.status || 'not_started'))
    : pipelineStatus.value
))
const topPipelineStatusLabel = computed(() => {
  const label = {
    not_started: '尚未开始',
    pending: '等待执行',
    queued: '已排队',
    processing: '正在处理',
    running: '正在处理',
    failed: '已失败',
    blocked_human: '等待人工确认',
    succeeded: '已完成',
    completed: '已完成',
    cancelled: '已取消',
  }[topPipelineStatus.value] || topPipelineStatus.value
  return ['processing', 'running'].includes(topPipelineStatus.value)
    ? `${label} · 已运行 ${formatPipelineDuration(runningDurationSeconds.value)}`
    : label
})
const showPipelineMonitor = computed(() => (
  hasOutline.value
  || outlineBusy.value
  || showGenerationPipeline.value
  || pipelineStatus.value === 'running'
  || Boolean(latestLlmRequest.value)
))
const completedPipelineStageCount = computed(() => (
  topPipelineStages.value.filter(stage => ['succeeded', 'reused', 'completed'].includes(stage.status)).length
))
const remainingPipelineStageCount = computed(() => (
  topPipelineStages.value.filter(
    stage => !['succeeded', 'reused', 'completed'].includes(stage.status),
  ).length
))
function buildPipelineActivityMessages(stages, phaseKey) {
  const messages = []
  let localRequestNumber = 0

  stages.forEach((stage, stageIndex) => {
    const stageRequests = Array.isArray(stage.llm_requests) ? stage.llm_requests : []
    stageRequests.forEach((request) => {
      localRequestNumber += 1
      const requestNumber = localRequestNumber
      const requestStatus = String(request.status || 'completed')
      const isRunning = ['running', 'processing', 'queued'].includes(requestStatus)
      const isFailed = requestStatus === 'failed'
      const batchId = request.parameters?.logical_batch_id
      messages.push({
        id: `${phaseKey}:llm:${request.request_id || `${stage.stage_id}:${requestNumber}`}:${requestStatus}`,
        kind: 'llm',
        status: isFailed ? 'failed' : (isRunning ? 'running' : 'completed'),
        title: isFailed
          ? `第 ${requestNumber} 次连接大模型失败`
          : (isRunning ? `正在进行第 ${requestNumber} 次大模型连接` : `第 ${requestNumber} 次大模型连接已完成`),
        detail: `${stage.label} · ${requestAttemptKindLabel(request)}${batchId ? ` · 批次 ${batchId}` : ''}`,
        meta: isFailed ? '模型返回未通过校验，系统将按规则重试或停止。' : (isRunning ? '正在发送上下文并等待模型响应。' : '模型响应已接收，正在进入后续校验。'),
      })
    })

    const stageStatus = String(stage.status || 'pending')
    const isCompleted = ['succeeded', 'reused', 'completed'].includes(stageStatus)
    const isRunning = ['running', 'processing', 'queued'].includes(stageStatus)
    const isFailed = stageStatus === 'failed'
    if (!isCompleted && !isRunning && !isFailed) return

    const laterIncomplete = stages.slice(stageIndex + 1).filter(
      item => !['succeeded', 'reused', 'completed'].includes(item.status),
    ).length
    messages.push({
      id: `${phaseKey}:stage:${stage.stage_id}:${stageStatus}`,
      kind: 'stage',
      status: isFailed ? 'failed' : (isRunning ? 'running' : 'completed'),
      title: isFailed ? `处理失败：${stage.label}` : (isRunning ? `正在执行：${stage.label}` : `已完成：${stage.label}`),
      detail: stageResultSummary(stage) || (isRunning ? '正在执行本步骤的确定性处理、模型调用与结果校验。' : '本步骤产物已保存，可供后续步骤复用。'),
      meta: isRunning
        ? `当前第 ${stageIndex + 1}/${stages.length} 步，完成当前步骤后还剩 ${laterIncomplete} 步。`
        : `第 ${stageIndex + 1}/${stages.length} 步 · ${pipelineStageStatus(stage)}`,
    })
  })

  return messages.slice(-12)
}
const outlinePipelineActivityMessages = computed(() => (
  buildPipelineActivityMessages(pipelineStages.value, 'phase-2')
))
const generationPipelineActivityMessages = computed(() => (
  buildPipelineActivityMessages(generationExecutionStages.value, 'phase-3')
))
const pipelineLlmRequestCount = computed(() => topPipelineStages.value.reduce(
  (total, stage) => total + Number(stage.llm_request_count || 0),
  0,
))
const planningReviewOperationId = computed(() => String(
  latestOutlineOperation()?.operation_id
  || snapshot.value.analysis?.latest_operation?.operation_id
  || snapshot.value.analysis?.operation_id
  || '',
))
const showPlanningReviewPrompt = computed(() => (
  planningStatus.value === 'needs_human'
  && hasOutline.value
  && planningReviewOperationId.value !== dismissedPlanningReviewOperationId.value
))
const processingSummaryLabel = computed(() => {
  const duration = runningDurationSeconds.value > 0
    ? `已处理 ${formatPipelineDuration(runningDurationSeconds.value)}`
    : '处理摘要'
  return duration
})
function formatPipelineDuration(totalSeconds) {
  const normalized = Math.max(0, Number(totalSeconds) || 0)
  const minutes = Math.floor(normalized / 60)
  const seconds = normalized % 60
  if (minutes > 0) return `${minutes} 分钟 ${seconds} 秒`
  return `${seconds} 秒`
}
const outlinePipelineActivitySummaryLabel = computed(() => (
  `阶段 2 执行过程 · ${outlinePipelineActivityMessages.value.length} 条记录`
))
const generationPipelineActivitySummaryLabel = computed(() => {
  if (runningDurationSeconds.value > 0) {
    return `阶段 3 执行过程 · ${generationBusy.value ? '已运行' : '耗时'} ${formatPipelineDuration(runningDurationSeconds.value)}`
  }
  return `阶段 3 执行过程 · ${generationPipelineActivityMessages.value.length} 条记录`
})
const generationWorkflowStatusClass = computed(() => {
  if (generation.value.status === 'failed' || generation.value.status === 'blocked') return 'failed'
  if (generation.value.status === 'succeeded') return 'done'
  return 'action'
})
const generationWorkflowStatusLabel = computed(() => {
  if (generationBusy.value) {
    return `正在处理 · 已运行 ${formatPipelineDuration(runningDurationSeconds.value)}`
  }
  if (generation.value.status === 'succeeded') return '已完成'
  if (generation.value.status === 'failed') return '已失败'
  if (generation.value.status === 'blocked') return '已暂停'
  return '已启动'
})
const generationWorkflowTitle = computed(() => {
  const status = String(writingPhaseState.value.phase_status || 'not_started')
  if (status === 'in_progress') return '正在编写章节正文'
  if (status === 'running') return '完整标书正在生成'
  if (status === 'completed') return '完整标书生成完成'
  if (status === 'failed') return '完整标书生成失败'
  if (status === 'blocked') return '完整标书生成已暂停'
  if (status === 'outdated') return '写作结果已过期'
  return '等待第二阶段完成并确认目录'
})
watch(
  () => [
    initialChatTurns.value.length,
    message.value,
    error.value,
    outlinePipelineActivityMessages.value.length,
    generationPipelineActivityMessages.value.length,
    topPipelineStages.value.map(stage => `${stage.stage_id}:${stage.status}:${stage.llm_request_count || 0}`).join('|'),
    latestLlmRequest.value?.request_id || '',
    latestLlmRequest.value?.status || '',
  ],
  () => scrollChatToLatest(),
  { flush: 'post' },
)
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
    if (!selectedOutlineChapterId.value || !flatOutline.value.some(c => c.chapter_id === selectedOutlineChapterId.value)) {
      if (flatOutline.value.length > 0 && selectedOutlineChapterId.value !== '__quality_gates__' && selectedOutlineChapterId.value !== '__all_scores__') {
        selectedOutlineChapterId.value = flatOutline.value[0].chapter_id
      }
    }
  },
  { immediate: true },
)

const currentOutlineChapter = computed(() => {
  if (!selectedOutlineChapterId.value) return flatOutline.value[0] || null
  return flatOutline.value.find(c => c.chapter_id === selectedOutlineChapterId.value) || flatOutline.value[0] || null
})

const filteredOutlineTree = computed(() => {
  const query = outlineSearchQuery.value.trim().toLowerCase()
  if (!query) return planningView.value.outline

  function filterNodes(nodes) {
    const matched = []
    for (const node of nodes) {
      const matchSelf = (node.title && String(node.title).toLowerCase().includes(query))
        || (node.number && String(node.number).toLowerCase().includes(query))
        || (node.purpose && String(node.purpose).toLowerCase().includes(query))
      const matchedChildren = node.children?.length ? filterNodes(node.children) : []
      if (matchSelf || matchedChildren.length > 0) {
        matched.push({
          ...node,
          children: matchedChildren,
        })
      }
    }
    return matched
  }
  return filterNodes(planningView.value.outline)
})

const visibleNavOutline = computed(() => {
  const query = outlineSearchQuery.value.trim().toLowerCase()
  const source = query ? filteredOutlineTree.value : planningView.value.outline
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
      if (projected.children?.length && (query || expandedChapterIds.value.has(projected.chapter_id))) {
        appendVisible(projected.children)
      }
    }
  }
  appendVisible(source)
  return result
})

function getBlueprintNodes() {
  if (!snapshot.value) snapshot.value = {}
  if (!snapshot.value.analysis) snapshot.value.analysis = {}
  if (!snapshot.value.analysis.chapter_blueprint) snapshot.value.analysis.chapter_blueprint = {}
  if (!Array.isArray(snapshot.value.analysis.chapter_blueprint.nodes)) {
    snapshot.value.analysis.chapter_blueprint.nodes = (snapshot.value.planning?.blueprint?.nodes || []).map(n => ({ ...n }))
  }
  return snapshot.value.analysis.chapter_blueprint.nodes
}

function ensureBlueprintSync() {
  if (!snapshot.value.planning) snapshot.value.planning = {}
  if (!snapshot.value.planning.snapshot) snapshot.value.planning.snapshot = {}
  if (!snapshot.value.planning.snapshot.chapter_blueprint) snapshot.value.planning.snapshot.chapter_blueprint = {}
  snapshot.value.planning.snapshot.chapter_blueprint.nodes = getBlueprintNodes()
}

function updateOutlineChapter(chapterId, patch) {
  const nodes = getBlueprintNodes()
  let target = nodes.find(n => n.chapter_id === chapterId)
  if (!target) {
    const flat = flatOutline.value.find(c => c.chapter_id === chapterId)
    if (flat) {
      target = {
        chapter_id: flat.chapter_id,
        title: flat.title,
        number: flat.number,
        parent_chapter_id: flat.parent_chapter_id || '',
        order: flat.order || 0,
        purpose: flat.purpose || '',
        writing_objectives: [...(flat.writing_objectives || [])],
        score_point_ids: [...(flat.score_point_ids || [])],
        score_condition_ids: [...(flat.score_condition_ids || [])],
        primary_response_unit_ids: [...(flat.primary_response_unit_ids || [])],
        supporting_response_unit_ids: [...(flat.supporting_response_unit_ids || [])],
        requirement_ids: [...(flat.requirement_ids || [])],
      }
      nodes.push(target)
    }
  }
  if (target) {
    Object.assign(target, patch)
  }
  ensureBlueprintSync()
}

function addRootChapter() {
  const nodes = getBlueprintNodes()
  if (!nodes.length && flatOutline.value.length) {
    for (const f of flatOutline.value) {
      nodes.push({
        chapter_id: f.chapter_id,
        title: f.title,
        number: f.number,
        parent_chapter_id: f.parent_chapter_id || '',
        order: f.order || 0,
        purpose: f.purpose || '',
        writing_objectives: [...(f.writing_objectives || [])],
        score_point_ids: [...(f.score_point_ids || [])],
        score_condition_ids: [...(f.score_condition_ids || [])],
        primary_response_unit_ids: [...(f.primary_response_unit_ids || [])],
        supporting_response_unit_ids: [...(f.supporting_response_unit_ids || [])],
        requirement_ids: [...(f.requirement_ids || [])],
      })
    }
  }
  const rootNodes = nodes.filter(n => !n.parent_chapter_id)
  const nextIndex = rootNodes.length + 1
  const newId = `chapter_custom_${Date.now()}`
  const newNode = {
    chapter_id: newId,
    parent_chapter_id: '',
    order: rootNodes.length ? Math.max(...rootNodes.map(n => Number(n.order || 0))) + 1 : 1,
    number: `${nextIndex}`,
    title: `新章节 ${nextIndex}`,
    purpose: '',
    writing_objectives: [],
    score_point_ids: [],
    score_condition_ids: [],
    primary_response_unit_ids: [],
    supporting_response_unit_ids: [],
    requirement_ids: [],
  }
  nodes.push(newNode)
  ensureBlueprintSync()
  selectedOutlineChapterId.value = newId
}

function addChildChapter(parentChapterId) {
  const nodes = getBlueprintNodes()
  if (!nodes.length && flatOutline.value.length) {
    for (const f of flatOutline.value) {
      nodes.push({
        chapter_id: f.chapter_id,
        title: f.title,
        number: f.number,
        parent_chapter_id: f.parent_chapter_id || '',
        order: f.order || 0,
        purpose: f.purpose || '',
        writing_objectives: [...(f.writing_objectives || [])],
        score_point_ids: [...(f.score_point_ids || [])],
        score_condition_ids: [...(f.score_condition_ids || [])],
        primary_response_unit_ids: [...(f.primary_response_unit_ids || [])],
        supporting_response_unit_ids: [...(f.supporting_response_unit_ids || [])],
        requirement_ids: [...(f.requirement_ids || [])],
      })
    }
  }
  const parent = nodes.find(n => n.chapter_id === parentChapterId) || flatOutline.value.find(c => c.chapter_id === parentChapterId)
  if (!parent) return
  const siblings = nodes.filter(n => n.parent_chapter_id === parentChapterId)
  const nextSubIndex = siblings.length + 1
  const parentNumber = parent.number || '1'
  const newNumber = `${parentNumber}.${nextSubIndex}`
  const newId = `chapter_custom_${Date.now()}`
  const newNode = {
    chapter_id: newId,
    parent_chapter_id: parentChapterId,
    order: siblings.length ? Math.max(...siblings.map(n => Number(n.order || 0))) + 1 : 1,
    number: newNumber,
    title: `新子章节 ${newNumber}`,
    purpose: '',
    writing_objectives: [],
    score_point_ids: [],
    score_condition_ids: [],
    primary_response_unit_ids: [],
    supporting_response_unit_ids: [],
    requirement_ids: [],
  }
  nodes.push(newNode)
  ensureBlueprintSync()
  expandedChapterIds.value.add(parentChapterId)
  selectedOutlineChapterId.value = newId
}

async function deleteOutlineChapter(chapterId) {
  const confirmed = await confirmDialog({
    title: '删除章节',
    message: '确定要删除该章节及其所有子章节吗？',
    confirmText: '删除',
    cancelText: '取消',
    tone: 'danger',
  })
  if (!confirmed) return
  const nodes = getBlueprintNodes()
  if (!nodes.length && flatOutline.value.length) {
    for (const f of flatOutline.value) {
      nodes.push({
        chapter_id: f.chapter_id,
        title: f.title,
        number: f.number,
        parent_chapter_id: f.parent_chapter_id || '',
        order: f.order || 0,
        purpose: f.purpose || '',
        writing_objectives: [...(f.writing_objectives || [])],
        score_point_ids: [...(f.score_point_ids || [])],
        score_condition_ids: [...(f.score_condition_ids || [])],
        primary_response_unit_ids: [...(f.primary_response_unit_ids || [])],
        supporting_response_unit_ids: [...(f.supporting_response_unit_ids || [])],
        requirement_ids: [...(f.requirement_ids || [])],
      })
    }
  }
  const toDelete = new Set([chapterId])
  let changed = true
  while (changed) {
    changed = false
    for (const node of nodes) {
      if (node.parent_chapter_id && toDelete.has(node.parent_chapter_id) && !toDelete.has(node.chapter_id)) {
        toDelete.add(node.chapter_id)
        changed = true
      }
    }
  }
  const remaining = nodes.filter(n => !toDelete.has(n.chapter_id))
  snapshot.value.analysis.chapter_blueprint.nodes = remaining
  ensureBlueprintSync()
  if (selectedOutlineChapterId.value === chapterId || toDelete.has(selectedOutlineChapterId.value)) {
    selectedOutlineChapterId.value = remaining[0]?.chapter_id || ''
  }
}

function moveOutlineChapter(chapterId, direction) {
  const nodes = getBlueprintNodes()
  if (!nodes.length && flatOutline.value.length) {
    for (const f of flatOutline.value) {
      nodes.push({
        chapter_id: f.chapter_id,
        title: f.title,
        number: f.number,
        parent_chapter_id: f.parent_chapter_id || '',
        order: f.order || 0,
        purpose: f.purpose || '',
        writing_objectives: [...(f.writing_objectives || [])],
        score_point_ids: [...(f.score_point_ids || [])],
        score_condition_ids: [...(f.score_condition_ids || [])],
        primary_response_unit_ids: [...(f.primary_response_unit_ids || [])],
        supporting_response_unit_ids: [...(f.supporting_response_unit_ids || [])],
        requirement_ids: [...(f.requirement_ids || [])],
      })
    }
  }
  const target = nodes.find(n => n.chapter_id === chapterId)
  if (!target) return
  const parentId = target.parent_chapter_id || ''
  const siblings = nodes.filter(n => (n.parent_chapter_id || '') === parentId)
  siblings.sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
  const currentIndex = siblings.findIndex(n => n.chapter_id === chapterId)
  if (currentIndex === -1) return
  const targetIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
  if (targetIndex < 0 || targetIndex >= siblings.length) return
  const other = siblings[targetIndex]
  const tempOrder = target.order || currentIndex
  target.order = other.order || targetIndex
  other.order = tempOrder
  ensureBlueprintSync()
}

function startInlineEdit(chapter) {
  inlineEditingChapterId.value = chapter.chapter_id
  inlineEditingTitle.value = chapter.title || ''
}

function saveInlineEdit(chapterId) {
  if (inlineEditingTitle.value.trim()) {
    updateOutlineChapter(chapterId, { title: inlineEditingTitle.value.trim() })
  }
  inlineEditingChapterId.value = ''
}

function cancelInlineEdit() {
  inlineEditingChapterId.value = ''
}

function addChapterObjective(chapterId) {
  const text = newObjectiveText.value.trim()
  if (!text) return
  const chapter = flatOutline.value.find(c => c.chapter_id === chapterId)
  if (!chapter) return
  const objectives = [...(chapter.writing_objectives || []), text]
  updateOutlineChapter(chapterId, { writing_objectives: objectives })
  newObjectiveText.value = ''
}

function removeChapterObjective(chapterId, index) {
  const chapter = flatOutline.value.find(c => c.chapter_id === chapterId)
  if (!chapter || !chapter.writing_objectives) return
  const objectives = chapter.writing_objectives.filter((_, i) => i !== index)
  updateOutlineChapter(chapterId, { writing_objectives: objectives })
}

function updateChapterObjective(chapterId, index, text) {
  const chapter = flatOutline.value.find(c => c.chapter_id === chapterId)
  if (!chapter || !chapter.writing_objectives) return
  const objectives = [...chapter.writing_objectives]
  objectives[index] = text
  updateOutlineChapter(chapterId, { writing_objectives: objectives })
}
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
  ready_with_warnings: '不可交付：存在校验错误',
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
    description: initialMaterialsReady.value ? `${activeInputs.value.length} 个文件已登记` : '请上传招标文件和公司资质/参考资料',
    status: initialMaterialsReady.value ? 'done' : 'active',
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
  return displayInputs.value.filter(item => item.role === role)
}

function displayInputStatusLabel(item) {
  if (item.role === 'legacy_bid') {
    return { parsing: '解析中', ready: '解析完成', failed: '解析失败' }[item.status] || '待解析'
  }
  return sourceStatusLabel(item)
}

async function openLegacyPreview(item) {
  legacyPreviewOpen.value = true
  legacyPreviewLoading.value = true
  legacyPreviewIndex.value = null
  legacyPreviewFilename.value = item.filename
  legacyPreviewError.value = ''
  try {
    const { data } = await fetchLegacyBidIndex(props.runId, item.legacy_bid_id || item.input_id)
    legacyPreviewIndex.value = data?.index || null
  } catch (cause) {
    legacyPreviewError.value = cause?.response?.data?.message || '拆解结果读取失败'
  } finally {
    legacyPreviewLoading.value = false
  }
}

function closeLegacyPreview() {
  legacyPreviewOpen.value = false
  legacyPreviewIndex.value = null
  legacyPreviewError.value = ''
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
    applyWorkspaceSnapshot(data, resetError)
  } catch (cause) {
    reportError(cause, 'V3 工作区状态读取失败。')
  } finally {
    loading.value = false
  }
}

function applyWorkspaceSnapshot(data, resetError = false) {
  snapshot.value = normalizeV3WorkspaceSnapshot(data)
  // Once this workspace has entered planning, a stale outline should expose
  // the re-planning action immediately after refresh.  Do not require the
  // user to repeat the conversational "enter phase 2" acknowledgement just
  // because the old outline was invalidated by a prompt/model update.
  if (
    initialMaterialsReady.value
    && ['outdated', 'blocked', 'needs_human', 'confirmed'].includes(planningStatus.value)
  ) {
    secondStageConfirmed.value = true
  }
  if (activeStageDrawerId.value && !stageDetailLoading.value) {
    void loadStageDetail(activeStageDrawerId.value)
  }
  if (resetError) {
    clearError()
  } else if (!error.value) {
    const latest = latestOutlineOperation()
    if (latest?.status === 'failed') reportOutlineOperationFailure(latest)
  }
}

function connectWorkspaceStream() {
  closeWorkspaceStream?.()
  closeWorkspaceStream = null
  if (!props.runId) return
  closeWorkspaceStream = subscribeV3Workspace(props.runId, {
    onSnapshot: data => applyWorkspaceSnapshot(data),
  })
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

async function prepareOutline(options = {}) {
  if (outlineBusy.value || (running.value && runningAction.value === 'outline')) {
    return
  }
  if (!initialMaterialsReady.value) {
    message.value = '请先上传招标文件和公司资质/参考资料，再进入第二阶段。'
    return
  }
  if (!secondStageConfirmed.value) {
    message.value = '材料已齐全，请先在对话中回复“继续第二阶段”。'
    return
  }
  running.value = true
  runningAction.value = 'outline'
  clearError()
  waitingForOutlineCompletion.value = false
  message.value = ''
  try {
    const { data } = await prepareV3Outline(props.runId, options)
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

async function retryProjectFacts(withFeedback, regenerate = false) {
  if (outlineBusy.value || (running.value && runningAction.value === 'outline')) return
  const feedback = withFeedback ? projectFactFeedback.value.trim() : ''
  if (withFeedback && !feedback) return
  running.value = true
  runningAction.value = 'outline'
  clearError()
  try {
    const { data } = await prepareV3Outline(
      props.runId,
      {
        ...(feedback ? { projectFeedback: feedback } : {}),
        ...(regenerate ? { regenerateCapabilities: ['planning.project_understanding'] } : {}),
      },
    )
    assertCommandAccepted(data, '全局项目事实重新生成失败。')
    projectFactFeedback.value = ''
    await refresh()
    closeStageDrawer()
    message.value = data.message || data.receipt?.message || '已重新生成全局项目事实。'
  } catch (cause) {
    await refresh().catch(() => {})
    reportError(cause, '全局项目事实重新生成失败。')
  } finally {
    running.value = false
    runningAction.value = ''
  }
}

async function submitPlanningFeedback() {
  const feedback = planningReviewFeedback.value.trim()
  if (!feedback) return
  const activeReview = pendingReviews.value.find(item => item.kind === 'planning') || {}
  const baseBlueprintHash = String(activeReview.target_hash || '')
  if (!baseBlueprintHash) {
    error.value = '当前目录版本已变化，请刷新后再提交修改意见。'
    return
  }
  running.value = true
  runningAction.value = 'planning-feedback'
  clearError()
  try {
    const { data } = await prepareV3Outline(props.runId, { reviewFeedback: feedback, baseBlueprintHash })
    assertCommandAccepted(data, '目录修改失败')
    planningReviewFeedback.value = ''
    dismissedPlanningReviewOperationId.value = ''
    message.value = '已提交修改意见，正在生成新的目录版本。'
    await refresh()
  } catch (cause) {
    reportError(cause, '提交目录修改意见失败')
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
    const confirmationStatus = String(
      data?.result?.operation_status
      || data?.receipt?.result?.operation_status
      || '',
    )
    const waitingForFinalOutline = confirmationStatus === 'blocked_human'
    message.value = waitingForFinalOutline
      ? '原始目录已确认；已结合旧投标书生成最终目录，请再次审核确认。'
      : '最终目录已确认；正文生成尚未启动。'
    await refresh()
    activeTab.value = 'upload'
    if (waitingForFinalOutline) activeTab.value = 'planning'
  } catch (cause) {
    reportError(cause, '目录确认失败。')
  } finally {
    running.value = false
    runningAction.value = ''
  }
}

function openWritingWorkbench() {
  const firstChapterId = flatOutline.value[0]?.chapter_id || ''
  if (firstChapterId) {
    router.push({ name: 'ChapterWorkspace', params: { workspaceId: props.runId, chapterId: firstChapterId } })
  } else {
    router.push({ name: 'ProjectHome', params: { workspaceId: props.runId } })
  }
}

async function runDocument(chapterIds = []) {
  const normalizedChapterIds = Array.isArray(chapterIds) ? chapterIds.filter(Boolean) : []
  running.value = true
  runningAction.value = normalizedChapterIds.length ? 'selected-chapter' : 'document'
  activeTab.value = 'upload'
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
    ? `已开始一键生成所选 ${normalizedChapterIds.length} 个章节范围。`
    : '完整标书生成任务已启动，正在等待后端阶段状态。'
  try {
    const { data } = await runV3Pipeline(props.runId, normalizedChapterIds)
    assertCommandAccepted(data, normalizedChapterIds.length ? '本章生成失败。' : '完整标书生成失败。')
    message.value = data.message || data.receipt?.message || (normalizedChapterIds.length ? '本章已生成。' : '完整标书已生成。')
    await router.push({
      name: normalizedChapterIds.length ? 'ChapterWorkspace' : 'ProjectHome',
      params: normalizedChapterIds.length
        ? { workspaceId: props.runId, chapterId: normalizedChapterIds[0] }
        : { workspaceId: props.runId },
    })
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

function isGenerationChapterSelected(chapterId) {
  return selectedGenerationChapterIds.value.includes(chapterId)
}

function descendantChapterIds(chapterId) {
  const selected = new Set([chapterId])
  let changed = true
  while (changed) {
    const size = selected.size
    for (const chapter of flatOutline.value) {
      if (selected.has(chapter.parent_chapter_id)) selected.add(chapter.chapter_id)
    }
    changed = selected.size !== size
  }
  return [...selected]
}

function toggleGenerationChapter(chapter) {
  const chapterId = String(chapter?.chapter_id || '')
  if (!chapterId) return
  const relatedIds = descendantChapterIds(chapterId)
  const next = new Set(selectedGenerationChapterIds.value)
  const selecting = !next.has(chapterId)
  for (const id of relatedIds) {
    if (selecting) next.add(id)
    else next.delete(id)
  }
  selectedGenerationChapterIds.value = flatOutline.value
    .map(item => item.chapter_id)
    .filter(id => next.has(id))
}

function clearGenerationSelection() {
  selectedGenerationChapterIds.value = []
}

async function runSelectedChapters() {
  if (!selectedGenerationChapterIds.value.length) {
    error.value = '请先勾选需要一键生成的章节。'
    return
  }
  // Child chapter IDs are intentionally sent too: this keeps the visual selection
  // and the requested write scope exactly aligned, while the backend de-duplicates it.
  await runDocument(selectedGenerationChapterIds.value)
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

async function research(need) {
  researchingNeedId.value = need.need_id
  clearError()
  try {
    const { data } = await resolveV3Research(props.runId, need.need_id)
    assertCommandAccepted(data, 'Tavily 检索失败。')
    message.value = data.message || data.receipt?.message || 'Tavily 检索完成。'
    await refresh()
  } catch (cause) {
    reportError(cause, 'Tavily 检索失败。')
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
  if (
    stage?.stage_id === 'plan_response'
    && Number(stage?.repair_round || 0) > 0
    && ['succeeded', 'reused'].includes(stage?.status)
  ) {
    const maxRounds = Number(stage?.max_repair_rounds || 2)
    return `已自动修复第 ${stage.repair_round}/${maxRounds} 轮`
  }
  const status = {
    pending: '等待上游阶段',
    queued: '已排队',
    running: '正在执行',
    succeeded: '执行完成',
    reused: '复用已验证产物',
    failed: '执行失败',
    paused: '需要处理',
    blocked: '需要处理',
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
    plan_response: '操作：生成全局项目事实，供全部章节统一引用',
    compile_chapter_blueprint: '操作：生成评分驱动章节目录',
    confirm_planning: '用户操作：审阅并确认目录',
    sync_material_requirements: '操作：匹配公司资料并列出证据缺口',
    compile_document_contract: '操作：固化已确认目录、模板和写入位置',
    plan_document: '操作：拆分章节、依赖和写作任务',
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

function stageResultSummary(stage) {
  const generatedSummary = generationStageSummary(stage)
  if (generatedSummary) return generatedSummary
  const summary = stage?.summary && typeof stage.summary === 'object' ? stage.summary : {}
  const entries = Object.entries(summary)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, 3)
  if (entries.length) {
    return entries
      .map(([key, value]) => `${pipelineSummaryLabels[key] || key} ${formatPoints(value)}`)
      .join(' · ')
  }
  if (['succeeded', 'reused'].includes(stage?.status)) return '步骤已完成，产物已持久化。'
  if (['failed', 'paused', 'blocked'].includes(stage?.status)) return pipelineStageError(stage)
  if (stage?.status === 'blocked_human') return '需要人工处理后才能继续。'
  return ''
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
    researching: '正在调用 Tavily',
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

function shouldExpandLlmRequest(request, requestIndex, requestCount) {
  return request?.status === 'failed' || requestIndex === requestCount - 1
}

function llmRequestPurpose(request) {
  const parameters = request?.parameters || {}
  const explicitPurpose = String(parameters.request_purpose || '').trim()
  if (explicitPurpose) return explicitPurpose

  const capabilityId = String(
    parameters.capability_id || request?.capability_id || '',
  ).trim()
  const initialPurpose = {
    'planning.chapter_outline_split': '根据已提取的招标要求和评分项，生成可用于编写投标文件的章节目录及评分覆盖关系。',
    'planning.rewrite_outline_merge': '将旧投标书章节对齐到新招标目录，并确定每个叶子章节的复用方式。',
    'score.semantic_reconcile': '核对评分项、响应内容和证据之间的对应关系，补全可追溯的评分模型。',
    'planning.project_understanding': '归纳项目背景、建设目标、范围和关键约束，形成项目理解。',
    'planning.topic_duty_plan': '把招标要求拆分为编写主题、责任范围和所需材料。',
  }[capabilityId]

  const attemptKind = parameters.attempt_kind || request?.attempt_kind
  if (attemptKind === 'controlled_repair' || attemptKind === 'repair') {
    return initialPurpose
      ? `修复上一轮不符合格式或校验要求的输出，并重新提交：${initialPurpose}`
      : '修复上一轮不符合格式或校验要求的模型输出；不改变已提供的业务事实。'
  }

  return initialPurpose || '根据本阶段已准备好的业务资料生成结构化结果，供后续流程继续使用。'
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

function getChapterAriaLabel(chapter) {
  if (!chapter) return ''
  const action = isChapterExpanded(chapter.chapter_id) ? '收起' : '展开'
  return `${action} ${chapter.title || ''} 的子章节`
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

function exportOutlineMarkdown() {
  const rootChapters = planningView.value.outline || []
  if (!rootChapters.length) return
  let mdText = '# 章节目录草案\n\n'
  function renderChapter(ch, depth = 0) {
    const indent = '  '.repeat(depth)
    const num = ch.number ? `${ch.number} ` : ''
    const title = ch.title || ch.chapter_id || ch.id
    mdText += `${indent}- ${num}${title}\n`
    if (ch.purpose) {
      mdText += `${indent}  - **编制目的**：${ch.purpose}\n`
    }
    if (ch.writing_objectives?.length) {
      mdText += `${indent}  - **写作目标**：${ch.writing_objectives.join('；')}\n`
    }
    if (ch.children && ch.children.length) {
      ch.children.forEach(child => renderChapter(child, depth + 1))
    }
  }
  rootChapters.forEach(ch => renderChapter(ch, 0))
  const blob = new Blob([mdText], { type: 'text/markdown;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `章节目录_${props.runId || 'outline'}.md`
  a.click()
  URL.revokeObjectURL(url)
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
  return { tender: '招标', legacy_bid: '旧投标书', score: '评分', company: '公司资料', company_fact: '公司资料' }[role] || ''
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
    activeTab.value = 'upload'
    closeContentUnit()
    closeStageDrawer()
    pendingUploads.tender.splice(0)
    pendingUploads.score.splice(0)
    pendingUploads.company.splice(0)
    connectWorkspaceStream()
    refresh()
  },
)

onMounted(() => {
  refresh()
  connectWorkspaceStream()
  window.addEventListener('keydown', handleWorkspaceKeydown)
})
onUnmounted(() => {
  closeWorkspaceStream?.()
  closeWorkspaceStream = null
  if (runningTimer) window.clearInterval(runningTimer)
  if (assistantClockTimer) window.clearInterval(assistantClockTimer)
  window.removeEventListener('keydown', handleWorkspaceKeydown)
  window.removeEventListener('mousemove', onResizingNav)
  window.removeEventListener('mouseup', stopResizingNav)
  window.removeEventListener('touchmove', onResizingNav)
  window.removeEventListener('touchend', stopResizingNav)
  if (document?.body) {
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
  }
})
</script>

<style scoped>
.v3-workspace {
  width: 100%;
  max-width: 100%;
  margin: 0 auto;
  padding: 0;
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

.workspace-tab-view.tab-planning {
  width: 80vw;
  max-width: 80vw;
  min-width: 0;
  margin-inline: auto;
  box-sizing: border-box;
  flex: 1;
  min-height: 0;
  height: 100%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px 20px 20px;
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
  margin-bottom: 12px;
  flex-shrink: 0;
  padding: 12px 18px;
  border: 1px solid rgba(226, 232, 240, 0.8);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(12px);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
  box-sizing: border-box;
  width: 100%;
}

.sticky-bar-info {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  min-width: 0;
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
  flex-shrink: 0;
  margin-left: auto;
}

.btn-back-assistant {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #334155;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.btn-back-assistant:hover {
  background: #f1f5f9;
  border-color: #94a3b8;
  color: #0f172a;
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
.planning-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-sizing: border-box;
}
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
.writer-batch-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 4px; }
.writer-batch-actions .btn { min-height: 36px; font-size: 12px; }
.writer-clear-selection { padding: 5px 2px; font-size: 12px; }
.writer-chapter-list { display: grid; gap: 7px; margin-top: 16px; }
.writer-word-toc { display: grid; gap: 2px; margin-top: 16px; }
.writer-toc-item { display: flex; width: 100%; gap: 7px; padding-top: 7px; padding-bottom: 7px; border: 0; border-radius: 5px; color: #27384d; background: transparent; text-align: left; cursor: pointer; line-height: 1.45; }
.writer-toc-item:hover, .writer-toc-item.active { background: #eaf0ff; color: #315bc4; }
.writer-toc-item > span { flex: 0 0 auto; font-family: 'Times New Roman', serif; }
.writer-chapter-check { width: 16px; height: 16px; flex: 0 0 16px; margin: 2px 0 0; accent-color: #315bc4; cursor: pointer; }
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
  border: 1px solid #dbe3f0;
  border-radius: 8px;
  background: #fff;
}
.pipeline-llm-request > summary {
  min-height: 40px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto 16px;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  color: #334155;
  cursor: pointer;
  list-style: none;
}
.pipeline-llm-request > summary::-webkit-details-marker { display: none; }
.pipeline-llm-request > summary:hover { background: #f8fafc; }
.pipeline-llm-request > summary > strong { font-size: 11px; white-space: nowrap; }
.llm-request-purpose-summary {
  min-width: 0;
  overflow: hidden;
  color: #64748b;
  font-size: 10px;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pipeline-llm-request > summary > span:not(.llm-request-purpose-summary) { font-size: 9px; font-weight: 700; }
.llm-request-chevron {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform .2s ease;
}
.pipeline-llm-request[open] .llm-request-chevron { transform: rotate(90deg); }
.pipeline-llm-request-detail {
  padding: 0 10px 10px;
  border-top: 1px solid #e2e8f0;
}
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
.planning-heading-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
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
.planning-word-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: row;
  gap: 0;
  margin-top: 14px;
  align-items: stretch;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  overflow: hidden;
  padding-bottom: 16px;
  position: relative;
}

/* 左侧：Word 风格目录导航窗格 */
.planning-nav-pane {
  height: 100%;
  max-height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #f8fafc;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  overflow: hidden;
  min-width: 200px;
  max-width: 750px;
  box-sizing: border-box;
  flex-shrink: 0;
}

/* 中间：左右可拖拽分割线 */
.planning-resizer {
  width: 16px;
  margin: 0 -3px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: col-resize;
  user-select: none;
  flex-shrink: 0;
  z-index: 10;
  transition: background-color 0.15s ease;
}

.planning-resizer .resizer-handle {
  width: 2px;
  height: 48px;
  border-radius: 2px;
  background: #cbd5e1;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
}

.planning-resizer .resizer-handle::before,
.planning-resizer .resizer-handle::after {
  content: '';
  position: absolute;
  top: 50%;
  width: 2px;
  height: 12px;
  transform: translateY(-50%);
  background: #cbd5e1;
  border-radius: 2px;
  opacity: 0;
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.planning-resizer .resizer-handle::before {
  left: -4px;
}

.planning-resizer .resizer-handle::after {
  right: -4px;
}

.planning-resizer:hover .resizer-handle,
.planning-resizer.is-active .resizer-handle {
  height: 80px;
  background: #3b82f6;
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.5);
}

.planning-resizer:hover .resizer-handle::before,
.planning-resizer:hover .resizer-handle::after,
.planning-resizer.is-active .resizer-handle::before,
.planning-resizer.is-active .resizer-handle::after {
  opacity: 1;
  background: #3b82f6;
}

.nav-pane-header {
  padding: 12px;
  border-bottom: 1px solid #e2e8f0;
  background: #ffffff;
}

.nav-search-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 4px 8px;
}

.nav-search-icon {
  font-size: 12px;
  color: #64748b;
}

.nav-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  font-size: 12px;
  color: #1e293b;
  outline: none;
}

.nav-search-clear {
  border: none;
  background: transparent;
  font-size: 14px;
  color: #94a3b8;
  cursor: pointer;
  padding: 0 2px;
}

.nav-search-clear:hover {
  color: #475569;
}

.nav-pane-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: 8px;
}

.nav-tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid #3b82f6;
  border-radius: 6px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.nav-tool-btn:hover {
  background: #dbeafe;
}

.nav-tool-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.nav-tool-link {
  border: none;
  background: transparent;
  color: #64748b;
  font-size: 11px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}

.nav-tool-link:hover {
  background: #e2e8f0;
  color: #1e293b;
}

.nav-tree-container {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
  min-height: 380px;
}

.nav-tree-empty {
  padding: 24px 16px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
}

.nav-tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  cursor: pointer;
  position: relative;
  transition: background-color 0.15s ease;
  font-size: 12px;
  line-height: 1.4;
  color: #334155;
  user-select: none;
}

.nav-tree-row:hover {
  background: #f1f5f9;
}

.nav-tree-row.active {
  background: #e0e7ff;
  color: #3730a3;
  font-weight: 600;
  box-shadow: inset 3px 0 #4f46e5;
}

.nav-tree-row.highlighted {
  background: #fef3c7;
  color: #92400e;
}

.nav-tree-row.dimmed {
  opacity: 0.45;
}

.nav-tree-toggle,
.nav-tree-spacer {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
}

.nav-tree-toggle {
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 11px;
  padding: 0;
  border-radius: 3px;
}

.nav-tree-toggle:hover {
  background: #cbd5e1;
  color: #0f172a;
}

.nav-tree-content {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.nav-chapter-num {
  font-weight: 700;
  color: #475569;
  flex: 0 0 auto;
  font-size: 11px;
}

.nav-tree-row.active .nav-chapter-num {
  color: #4338ca;
}

.nav-chapter-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-inline-input {
  width: 100%;
  padding: 2px 4px;
  border: 1px solid #4f46e5;
  border-radius: 4px;
  font-size: 12px;
  outline: none;
  background: #ffffff;
}

.nav-coverage-badge {
  flex: 0 0 auto;
  padding: 1px 6px;
  border-radius: 999px;
  background: #dcfce7;
  color: #15803d;
  font-size: 10px;
  font-weight: 700;
}

.nav-row-actions {
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex: 0 0 auto;
}

.nav-tree-row:hover .nav-row-actions,
.nav-tree-row.active .nav-row-actions {
  display: inline-flex;
}

.nav-action-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 10px;
  border-radius: 3px;
  padding: 0;
  opacity: 0.75;
}

.nav-action-icon:hover {
  opacity: 1;
  background: #cbd5e1;
}

.nav-action-delete:hover {
  background: #fee2e2;
}

.nav-pane-footer {
  border-top: 1px solid #e2e8f0;
  padding: 8px 10px;
  background: #ffffff;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-footer-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}

.nav-footer-item:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.nav-footer-item.active {
  background: #eef2ff;
  border-color: #a5b4fc;
  color: #4338ca;
}

.nav-footer-badge {
  font-size: 10px;
  color: #64748b;
  font-weight: normal;
}

/* 右侧：选中章节详情与编辑面板 (仅在右侧面板内部独立滚动) */
.planning-detail-pane {
  height: 100%;
  max-height: 100%;
  min-height: 0;
  flex: 1;
  min-width: 0;
  width: 100%;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-left: 10px;
  padding-right: 8px;
  padding-bottom: 24px;
  box-sizing: border-box;
}

.quality-gate-view,
.all-scores-view,
.detail-sections-container {
  width: 100%;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-sizing: border-box;
  min-width: 0;
}

.detail-pane-bottom-spacer {
  height: 80px;
  width: 100%;
  flex-shrink: 0;
}

.planning-detail-pane::-webkit-scrollbar,
.nav-tree-container::-webkit-scrollbar {
  width: 6px;
}

.planning-detail-pane::-webkit-scrollbar-track,
.nav-tree-container::-webkit-scrollbar-track {
  background: #f1f5f9;
  border-radius: 4px;
}

.planning-detail-pane::-webkit-scrollbar-thumb,
.nav-tree-container::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.planning-detail-pane::-webkit-scrollbar-thumb:hover,
.nav-tree-container::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.detail-header-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.detail-header-main {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.detail-chapter-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
}

.detail-depth-pill {
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-weight: 700;
}

.detail-parent-hint {
  color: #64748b;
}

.detail-title-display {
  margin: 0;
  font-size: 18px;
  color: #0f172a;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.35;
}

.detail-num-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 14px;
}

.detail-header-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  flex: 0 0 auto;
}

.btn-danger-outline {
  border: 1px solid #fca5a5;
  color: #b91c1c;
  background: #fff;
}

.btn-danger-outline:hover {
  background: #fef2f2;
}

.detail-sections-container {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.detail-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
}

.detail-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f1f5f9;
}

.detail-card-header h4 {
  margin: 0;
  font-size: 14px;
  color: #1e293b;
  font-weight: 700;
}

.detail-hint-text {
  font-size: 11px;
  color: #94a3b8;
}

.detail-badge-count {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-weight: 600;
}

.detail-form-grid {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 10px;
}

.form-field-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-field-group.full-width {
  grid-column: 1 / -1;
}

.field-label {
  font-size: 11px;
  font-weight: 600;
  color: #475569;
}

.form-input,
.form-textarea {
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 7px 10px;
  font-size: 13px;
  color: #1e293b;
  background: #ffffff;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  box-sizing: border-box;
}

.form-input:focus,
.form-textarea:focus {
  border-color: #4f46e5;
  box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.12);
}

.form-textarea {
  resize: vertical;
  line-height: 1.5;
}

.objectives-edit-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.objective-edit-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.objective-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex: 0 0 22px;
  border-radius: 50%;
  background: #eef2ff;
  color: #4338ca;
  font-size: 11px;
  font-weight: 700;
}

.objective-input {
  flex: 1;
}

.objective-remove-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 16px;
  cursor: pointer;
  border-radius: 4px;
}

.objective-remove-btn:hover {
  background: #fee2e2;
  color: #b91c1c;
}

.objective-add-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.objective-new-input {
  flex: 1;
}

.detail-empty-text {
  margin: 8px 0;
  color: #94a3b8;
  font-size: 12px;
  font-style: italic;
}

.detail-score-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}

.detail-score-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  background: #f8fafc;
}

.detail-score-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.detail-score-head strong {
  font-size: 13px;
  color: #0f172a;
}

.detail-score-val {
  font-size: 12px;
  font-weight: 700;
  color: #4338ca;
  flex: 0 0 auto;
}

.detail-score-crit {
  margin: 6px 0 8px;
  color: #64748b;
  font-size: 11px;
  line-height: 1.45;
}

.detail-score-footer {
  display: flex;
  align-items: center;
  gap: 6px;
}

.score-depth-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #e2e8f0;
  color: #475569;
}

.review-tag {
  font-size: 10px;
  color: #b45309;
  font-style: normal;
  font-weight: 700;
}

.detail-conditions-list,
.detail-requirements-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.requirement-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafbfc;
}

.requirement-card header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.requirement-card p {
  margin: 0;
  font-size: 12px;
  color: #334155;
  line-height: 1.5;
}

.detail-subchapters-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
}

.subchapter-item-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}

.subchapter-item-btn:hover {
  border-color: #a5b4fc;
  background: #eff6ff;
}

.subchapter-num {
  font-size: 11px;
  font-weight: 700;
  color: #4338ca;
}

.subchapter-title {
  font-size: 12px;
  color: #1e293b;
  line-height: 1.35;
}

.subchapter-score-pill {
  font-size: 10px;
  color: #15803d;
  background: #dcfce7;
  padding: 1px 6px;
  border-radius: 999px;
  margin-top: 2px;
}

.all-scores-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
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
.msg-bubble {
  background: #ffffff;
  border: none;
  border-radius: 14px;
  padding: 16px 18px;
  font-size: 14px;
  color: #1e293b;
  line-height: 1.6;
  box-shadow: none;
  max-width: 100%;
}

.user-msg .msg-bubble {
  background: #1e293b;
  color: #ffffff;
  border: none;
  border-radius: 16px;
  padding: 12px 18px;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
}

.user-msg .msg-bubble p {
  color: #ffffff;
  margin: 0;
}

.timeline-step-msg {
  width: 100%;
}

.timeline-step-msg > .msg-bubble {
  width: 100%;
  max-width: 100%;
}

.workflow-step-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f1f5f9;
}

.workflow-step-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.workflow-step-heading h4 {
  margin: 0;
  color: #0f172a;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.35;
}

.workflow-step-heading p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.workflow-step-intro {
  margin-top: 12px;
  color: #475569;
  font-size: 13px;
  line-height: 1.65;
}

.step-tag {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  white-space: nowrap;
}

.step-tag.ready {
  background: #dbeafe;
  color: #1d4ed8;
}

.workflow-step-status {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}

.workflow-step-status.done {
  background: #dcfce7;
  color: #15803d;
}

.workflow-step-status.pending {
  background: #f1f5f9;
  color: #64748b;
}

.workflow-step-status.action {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}

.pulse-indicator {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #3b82f6;
  margin-right: 6px;
  animation: pulse-dot 1.5s infinite;
}

@keyframes pulse-dot {
  0% { transform: scale(0.95); opacity: 0.7; box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.6); }
  70% { transform: scale(1.1); opacity: 1; box-shadow: 0 0 0 5px rgba(59, 130, 246, 0); }
  100% { transform: scale(0.95); opacity: 0.7; box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
}

/* 必传材料卡片 */
.upload-start-bubble {
  width: 100%;
  max-width: 720px;
  background: #ffffff;
  border: 1px solid #dbeafe;
  border-radius: 14px;
  padding: 16px 18px;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.04);
}

.upload-start-header h4 {
  margin: 2px 0 4px;
  color: #0f172a;
  font-size: 16px;
  font-weight: 700;
}

.upload-start-header p {
  margin: 0 0 14px;
  color: #64748b;
  font-size: 13px;
  line-height: 1.5;
}

.workflow-result-kicker {
  display: block;
  margin-bottom: 3px;
  color: #64748b;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .04em;
  text-transform: uppercase;
}

.required-upload-zones {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.required-upload-zone {
  min-width: 0;
  border: 1.5px dashed #cbd5e1;
  border-radius: 12px;
  background: #f8fafc;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.required-upload-zone:hover {
  border-color: #3b82f6;
  background: #eff6ff;
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(37, 99, 235, 0.08);
}

.required-upload-zone.complete {
  border-style: solid;
  border-color: #86efac;
  background: #f0fdf4;
}

.required-upload-zone.complete:hover {
  border-color: #22c55e;
  background: #dcfce7;
}

.required-upload-zone-label {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  cursor: pointer;
  height: 100%;
  box-sizing: border-box;
}

.zone-icon-box {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: #eff6ff;
  color: #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
}

.required-upload-zone.complete .zone-icon-box {
  background: #dcfce7;
  color: #16a34a;
}

.zone-info-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.zone-title-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.zone-title-line strong {
  font-size: 14px;
  color: #0f172a;
  font-weight: 700;
}

.zone-req-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #fee2e2;
  color: #dc2626;
  font-weight: 700;
}

.zone-info-box small {
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.zone-status-box {
  margin-top: auto;
  padding-top: 6px;
}

.zone-uploaded-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 700;
  color: #16a34a;
}

.zone-upload-btn-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #2563eb;
}

@media (max-width: 640px) {
  .required-upload-zones { grid-template-columns: 1fr; }
}

/* 步骤1中的材料卡片与上传区 */
.step-upload-section {
  margin-top: 14px;
}

.step-materials-card {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #f1f5f9;
}

.step-materials-card .file-changes-header {
  padding-bottom: 8px;
  margin-bottom: 6px;
  border-bottom: none;
}

.file-list-msg .file-changes-card {
  width: 100%;
  max-width: 100%;
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  box-shadow: none;
}

.file-changes-header {
  padding-bottom: 6px;
  margin-bottom: 6px;
  border-bottom: none;
}

.file-changes-title {
  font-size: 12px;
  font-weight: 700;
  color: #475569;
}

.file-changes-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-change-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  transition: all 0.15s ease;
}

.file-change-item:hover {
  background: #eff6ff;
  border-color: #dbeafe;
}

.file-item-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.file-role-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}

.file-role-badge.role-tender {
  background: #dbeafe;
  color: #1e40af;
}

.file-role-badge.role-company {
  background: #e0e7ff;
  color: #3730a3;
}

.file-role-badge.role-legacy_bid {
  background: #f3e8ff;
  color: #6b21a8;
}

.file-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #1e293b;
  font-weight: 600;
}

.diff-tag.add {
  font-size: 11px;
  color: #15803d;
  background: #dcfce7;
  padding: 3px 8px;
  border-radius: 6px;
  font-weight: 700;
  white-space: nowrap;
}

.legacy-preview-trigger {
  font-size: 11px;
  font-weight: 700;
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  padding: 3px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.legacy-preview-trigger:hover {
  background: #2563eb;
  color: #ffffff;
}

/* 阶段 2 确认卡片 */
.action-launch-bubble {
  width: 100%;
  max-width: 720px;
  background: linear-gradient(135deg, #ffffff 0%, #f0fdf4 100%);
  border: 1px solid #86efac;
  border-radius: 14px;
  padding: 16px 18px;
  box-shadow: 0 4px 14px rgba(22, 163, 74, 0.06);
}

.action-launch-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(134, 239, 172, 0.4);
  flex-wrap: wrap;
}

.btn-primary-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: linear-gradient(135deg, #16a34a 0%, #15803d 100%);
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(22, 163, 74, 0.25);
  transition: all 0.15s ease;
}

.btn-primary-action:hover:not(:disabled) {
  background: linear-gradient(135deg, #15803d 0%, #166534 100%);
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(22, 163, 74, 0.35);
}

.btn-primary-action:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-launch-subtext {
  font-size: 12px;
  color: #64748b;
}

/* 阶段 2 与阶段 3 结果卡片 */
.outline-card-bubble,
.generation-stage-bubble {
  width: 100%;
  max-width: 720px;
  border-radius: 14px;
  padding: 16px 18px;
}

.outline-card-bubble {
  background: #ffffff;
  border: 1px solid #bfdbfe;
}

.generation-stage-bubble {
  background: #ffffff;
  border: 1px solid #c7d2fe;
}

.outline-card-summary {
  margin: 0 0 10px;
  color: #475569;
  font-size: 13px;
}

.status-chat-msg {
  width: 100%;
  max-width: 720px;
}

.status-avatar {
  border-color: #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}

.status-bubble {
  width: 100%;
  box-shadow: none;
}

.success-chat-msg .status-bubble {
  border-color: #a7f3d0;
  background: #f0fdf4;
  color: #065f46;
}

.error-chat-msg .status-avatar {
  border-color: #fecaca;
  background: #fef2f2;
  color: #dc2626;
}

.error-chat-msg .status-bubble {
  border-color: #fecaca;
  background: #fff5f5;
  color: #991b1b;
  border-left: 4px solid #ef4444;
}

.error-detail-list {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #fecaca;
}

.error-detail-list ol {
  display: grid;
  gap: 6px;
  margin: 7px 0 0;
  padding-left: 20px;
}

.error-detail-list li span {
  display: block;
  font-size: 12px;
  color: #b91c1c;
}

/* 底部输入控制台 */
.studio-input-footer {
  position: relative;
  flex-shrink: 0;
  padding: 14px clamp(16px, 3vw, 32px) 18px;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 -2px 10px rgba(15, 23, 42, 0.02);
}

.workbench-entry-card {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  width: 100%;
  padding: 12px 16px;
  border: 1px solid #bfdbfe;
  border-radius: 12px;
  background: #f8fbff;
}

.workbench-entry-icon {
  width: 40px;
  height: 40px;
  display: inline-grid;
  place-items: center;
  border-radius: 10px;
  color: #1d4ed8;
  background: #dbeafe;
}

.workbench-entry-icon svg {
  width: 20px;
  height: 20px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.workbench-entry-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.workbench-entry-copy strong {
  color: #0f172a;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.4;
}

.workbench-entry-copy small {
  margin-top: 2px;
  color: #64748b;
  font-size: 11px;
  line-height: 1.4;
}

.workbench-entry-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 16px;
  background: #2563eb;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}

.workbench-entry-action:hover {
  background: #1d4ed8;
}

.workbench-entry-action svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.modern-input-card {
  width: 100%;
  background: #f8fafc;
  border: 1.5px solid #cbd5e1;
  border-radius: 14px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all 0.2s ease;
}

.modern-input-card:focus-within {
  background: #ffffff;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15), 0 4px 14px rgba(15, 23, 42, 0.04);
}

.modern-textarea {
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: #0f172a;
  resize: none;
  width: 100%;
  min-height: 48px;
  line-height: 1.5;
  font-family: inherit;
}

.modern-textarea::placeholder {
  color: #94a3b8;
}

.modern-card-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid #f1f5f9;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.quick-chip-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar-chip-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  cursor: pointer;
  transition: all 0.15s ease;
}

.toolbar-chip-btn:hover {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #1d4ed8;
}

.chip-icon {
  font-weight: 700;
  font-size: 14px;
  color: #2563eb;
}

.chip-req {
  font-size: 10px;
  font-style: normal;
  padding: 1px 4px;
  border-radius: 3px;
  background: #fee2e2;
  color: #dc2626;
  margin-left: 2px;
}

.attachment-trigger {
  display: inline-grid;
  width: 32px;
  height: 32px;
  place-items: center;
  padding: 0;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  background: #ffffff;
  color: #475569;
  cursor: pointer;
  transition: all 0.15s ease;
}

.attachment-trigger:hover,
.attachment-trigger[aria-expanded="true"] {
  border-color: #93c5fd;
  background: #eff6ff;
  color: #1d4ed8;
}

.attachment-trigger svg {
  width: 16px;
  height: 16px;
}

.quick-upload-menu {
  position: absolute;
  z-index: 20;
  bottom: calc(100% + 8px);
  left: 0;
  display: grid;
  min-width: 184px;
  padding: 6px;
  border: 1px solid #dbe3ef;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 12px 24px rgba(15, 23, 42, .12);
}

.quick-upload-option {
  display: flex;
  min-height: 40px;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 10px;
  border-radius: 7px;
  color: #334155;
  cursor: pointer;
  font-size: 13px;
}

.quick-upload-option:hover {
  background: #eff6ff;
  color: #1d4ed8;
}

.modern-send-circle-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #2563eb;
  color: #ffffff;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}

.modern-send-circle-btn:hover:not(:disabled) {
  background: #1d4ed8;
  transform: scale(1.05);
}

.modern-send-circle-btn:disabled {
  background: #e2e8f0;
  color: #94a3b8;
  cursor: not-allowed;
}

.studio-compliance-note {
  margin: 0;
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.5;
  text-align: center;
}

/* 目录预览树与确认按钮 */
.outline-card-details {
  margin-top: 12px;
}

.outline-card-details > summary {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.outline-card-details[open] > summary {
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
  background: #dbeafe;
}

.preview-tree-box {
  background: #ffffff;
  border: 1px solid #bfdbfe;
  border-top: none;
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.full-outline-preview {
  max-height: 320px;
  overflow-y: auto;
}

.tree-preview-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px calc(6px) calc(8px + var(--outline-depth, 0) * 16px);
  border-radius: 6px;
  font-size: 13px;
  color: #334155;
  transition: background 0.15s ease;
}

.tree-preview-item:hover {
  background: #f8fafc;
}

.tree-preview-item .node-num {
  font-weight: 800;
  color: #2563eb;
  flex-shrink: 0;
}

.tree-preview-item .node-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tree-preview-item .node-title {
  color: #0f172a;
  font-weight: 600;
}

.tree-preview-item .node-content small {
  color: #64748b;
  font-size: 11px;
}

.tree-preview-item .node-coverage {
  flex-shrink: 0;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
  font-weight: 600;
}

.workflow-result-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 40px;
  margin-top: 12px;
  padding: 8px 16px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.workflow-result-link:hover {
  background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%);
  transform: translateY(-1px);
  box-shadow: 0 3px 8px rgba(37, 99, 235, 0.15);
}

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
  .workspace-tab-view.tab-planning { width: 92vw; max-width: 92vw; min-width: 0; }
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
  .planning-word-layout { flex-direction: column; height: auto; min-height: 0; }
  .planning-resizer { display: none; }
  .planning-nav-pane { width: 100% !important; max-width: 100%; position: static; height: 380px; max-height: 380px; }
  .planning-detail-pane { height: auto; max-height: none; overflow-y: visible; padding-left: 0; padding-bottom: 24px; }
  .detail-header-card { flex-direction: column; }
  .detail-form-grid { grid-template-columns: 1fr; }
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

.v3-workspace {
  flex: 1;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.workspace-tab-view.tab-upload {
  flex: 1;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 现代 AI 工作台样式 */
.initial-chat-studio {
  width: 50vw;
  max-width: 50vw;
  min-width: 560px;
  height: 100%;
  max-height: 100%;
  min-height: 0;
  margin-inline: auto;
  background: #ffffff;
  box-shadow: 0 0 30px rgba(15, 23, 42, 0.04);
  border-left: 1px solid #e2e8f0;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.studio-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 68px;
  padding: 12px 24px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.02);
  flex-shrink: 0;
  gap: 16px;
  flex-wrap: wrap;
}

.bot-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.bot-icon {
  display: grid;
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
  padding: 0;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  color: #2563eb;
  box-shadow: 0 2px 6px rgba(37, 99, 235, 0.1);
}

.bot-icon svg {
  width: 22px;
  height: 22px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.bot-title-wrap {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.bot-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bot-title-row h3 {
  margin: 0;
  font-size: 16px;
  color: #0f172a;
  font-weight: 700;
}

.bot-title-wrap p {
  margin: 0;
  font-size: 12px;
  color: #64748b;
}

.mode-pill {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  letter-spacing: 0.5px;
}

.mode-pill-full {
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}

.mode-pill-rewrite {
  background: linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%);
  color: #6d28d9;
  border: 1px solid #ddd6fe;
}

.studio-header-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stat-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 5px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  color: #475569;
  font-weight: 500;
  transition: all 0.15s ease;
}

.stat-tag:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.stat-tag svg {
  width: 15px;
  height: 15px;
  color: #64748b;
}

.studio-chat-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px clamp(16px, 3vw, 32px) 28px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  background: #f8fafc;
}

.legacy-chat-stream {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 20px;
}

.chat-msg {
  display: flex;
  gap: 14px;
  width: 100%;
  max-width: 100%;
  align-items: flex-start;
}

.chat-msg.bot-msg { align-self: flex-start; }
.chat-msg.user-msg {
  display: flex;
  flex-direction: row;
  justify-content: flex-end;
  align-self: flex-end;
  width: 100%;
  max-width: 100%;
}

.msg-avatar {
  display: grid;
  flex: 0 0 38px;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  color: #475569;
  font-size: 13px;
  font-weight: 800;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
}

.step-avatar {
  border-color: #c7d2fe;
  background: linear-gradient(135deg, #eef2ff 0%, #e0e7ff 100%);
  color: #4338ca;
}

.material-avatar {
  border-color: #bfdbfe;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  color: #1d4ed8;
}

.material-avatar svg,
.file-summary-avatar svg,
.step-action-avatar svg {
  width: 18px;
  height: 18px;
}

.file-summary-avatar {
  border-color: #cbd5e1;
  background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
  color: #475569;
}

.step-action-avatar {
  border-color: #93c5fd;
  background: linear-gradient(135deg, #eff6ff 0%, #bfdbfe 100%);
  color: #1d4ed8;
}

.outline-avatar {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
}

.outline-avatar svg {
  width: 18px;
  height: 18px;
}

.conversation-avatar {
  letter-spacing: -.02em;
}

.user-msg .conversation-avatar {
  border-color: #cbd5e1;
  background: #f1f5f9;
  color: #334155;
}

.msg-bubble {
  max-width: 100%;
  border: none;
  border-radius: 4px 16px 16px;
  padding: 14px 16px;
  box-shadow: none;
  overflow-wrap: anywhere;
}

.msg-bubble p {
  margin: 0;
}

.user-msg .msg-bubble {
  border-radius: 16px;
  background: #27272a;
  color: #ffffff;
  border: none;
}

.materials-card-bubble,
.highlight-msg .msg-bubble {
  width: 100%;
  max-width: 100%;
}

.chat-material-card {
  min-height: 52px;
}

.file-icon {
  display: grid;
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 8px;
  background: #f1f5f9;
  color: #475569;
}

.file-icon svg {
  width: 16px;
  height: 16px;
}

.file-info strong,
.node-title,
.llm-card-meta code,
.llm-batch-code code {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.process-bubble {
  width: 100%;
  padding: 18px;
  border-color: #c7d2fe;
  background: linear-gradient(180deg, #f8faff 0%, #f3f6ff 100%);
}

.process-card-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
  align-items: start;
  padding-bottom: 14px;
  border-bottom: 1px solid #dbe4ff;
}

.process-card-title h4 {
  margin: 8px 0 0;
  color: #172554;
  font-size: 17px;
  line-height: 1.35;
}

.process-card-title p {
  max-width: 680px;
  margin-top: 5px;
  color: #52627a;
  font-size: 12px;
  line-height: 1.55;
}

.process-card-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-end;
}

.process-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

.process-metrics span {
  padding: 3px 8px;
  border-radius: 999px;
  background: #e0e7ff;
  color: #3730a3;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.process-detail-button {
  min-height: 40px;
  padding-inline: 13px;
  box-shadow: none;
}

.process-bubble .chat-stepper-container {
  margin: 14px 0 0;
  padding: 12px;
  border-color: #dbe4f0;
  border-radius: 12px;
}

.mini-stepper-title {
  gap: 12px;
  min-height: 28px;
  margin-bottom: 10px;
}

.chat-stepper-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(152px, 1fr));
  gap: 8px;
}

.chat-stepper-list > li {
  min-width: 0;
}

.chat-stepper-node {
  width: 100%;
  min-height: 56px;
  gap: 9px;
  padding: 8px 10px;
  border-radius: 9px;
  font: inherit;
  text-align: left;
}

.chat-stepper-node:focus-visible,
.chat-llm-status button:focus-visible,
.process-detail-button:focus-visible,
.toolbar-chip-btn:focus-within,
.modern-send-circle-btn:focus-visible,
.modal-close-btn:focus-visible {
  outline: 3px solid rgba(79, 70, 229, .24);
  outline-offset: 2px;
}

.chat-stepper-node.stage-reused,
.chat-stepper-node.stage-succeeded {
  border-color: #a7f3d0;
  background: #f0fdf4;
}

.chat-stepper-node.stage-queued,
.chat-stepper-node.stage-running,
.chat-stepper-node.stage-processing {
  border-color: #a5b4fc;
  background: #eef2ff;
}

.chat-stepper-node.stage-failed {
  border-color: #fecaca;
  background: #fef2f2;
}

.chat-stepper-node.has-warning {
  border-color: #fcd34d;
  background: #fffbeb;
}

.stage-reused .mini-node-badge,
.stage-succeeded .mini-node-badge {
  background: #15803d;
  color: #fff;
}

.stage-failed .mini-node-badge {
  background: #dc2626;
  color: #fff;
}

.has-warning .mini-node-badge {
  background: #d97706;
  color: #fff;
}

.mini-node-badge {
  flex: 0 0 20px;
  width: 20px;
  height: 20px;
}

.mini-node-info {
  flex: 1;
}

.mini-node-info strong,
.mini-node-info small {
  display: block;
  white-space: normal;
}

.mini-node-info strong {
  font-size: 12px;
  line-height: 1.35;
}

.chat-llm-status {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  min-height: 52px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: #fff;
}

.llm-status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 4px #d1fae5;
}

.llm-status-running .llm-status-dot {
  background: #4f46e5;
  box-shadow: 0 0 0 4px #e0e7ff;
  animation: pulse 1.4s ease-in-out infinite;
}

.llm-status-failed .llm-status-dot {
  background: #dc2626;
  box-shadow: 0 0 0 4px #fee2e2;
}

.chat-llm-status strong,
.chat-llm-status small {
  display: block;
}

.chat-llm-status strong {
  color: #1e293b;
  font-size: 12px;
}

.chat-llm-status small {
  margin-top: 2px;
  color: #64748b;
  font-size: 10px;
  overflow-wrap: anywhere;
}

.chat-llm-status button {
  min-height: 40px;
  padding: 7px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  background: #eef2ff;
  color: #3730a3;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  font-weight: 700;
}

.plan-result-details {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #dbe4ff;
}

/* 统一步骤卡片：每一步都按“步骤头部 → 操作/进度 → 步骤结果”组织。 */
.timeline-step-msg {
  width: 100%;
}

.timeline-step-msg > .msg-bubble {
  width: 100%;
  max-width: none;
}

.workflow-step-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e5eaf2;
}

.workflow-step-heading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 10px;
  min-width: 0;
}

.workflow-step-heading h4 {
  margin: 0;
  color: #172033;
  font-size: 17px;
  line-height: 1.35;
}

.workflow-step-heading p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.workflow-step-intro {
  margin-top: 12px !important;
  color: #334155;
  line-height: 1.65;
}

.workflow-step-status,
.workflow-result-status {
  display: inline-flex;
  min-height: 28px;
  align-items: center;
  padding: 4px 9px;
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  background: #f8fafc;
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
  white-space: nowrap;
}

.workflow-step-status.done,
.workflow-result-status.done {
  border-color: #bbf7d0;
  background: #ecfdf5;
  color: #047857;
}

.workflow-step-status.action {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
}

.workflow-result-status.action {
  border-color: #c7d2fe;
  background: #eef2ff;
  color: #4338ca;
}

.workflow-step-status.failed,
.workflow-result-status.failed {
  border-color: #fecaca;
  background: #fef2f2;
  color: #b91c1c;
}

.workflow-result-kicker {
  display: block;
  margin-bottom: 3px;
  color: #64748b;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .04em;
  text-transform: uppercase;
}

.materials-card-bubble .card-title-row {
  align-items: flex-start;
}

.materials-card-bubble .card-title-row h4 {
  margin: 0;
  font-size: 15px;
}

.outline-card-bubble {
  width: 100%;
  max-width: 560px;
  background: #f8fbff;
  border-color: #bfdbfe;
}

.outline-card-summary {
  margin: 0 0 10px;
  color: #475569;
  font-size: 12px;
}

.outline-card-details {
  margin-bottom: 0;
}

.outline-card-details > summary {
  display: flex;
  min-height: 38px;
  align-items: center;
  padding: 7px 10px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.outline-card-details[open] > summary {
  margin-bottom: 8px;
  background: #dbeafe;
}

.action-launch-bubble {
  background: linear-gradient(180deg, #f5fbff 0%, #f0fdf4 100%);
  border-color: #bae6fd;
}

.generation-stage-bubble {
  background: linear-gradient(180deg, #f8faff 0%, #f5f3ff 100%);
  border-color: #c7d2fe;
}

.generation-avatar {
  border-color: #c7d2fe;
  background: #eef2ff;
  color: #4338ca;
  font-weight: 800;
}

.generation-stage-headline {
  margin-bottom: 0 !important;
}

.ai-process-overview {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.ai-process-overview span {
  padding: 3px 7px;
  border-radius: 999px;
  background: #eef4fb;
  color: #52627a;
  font-size: 11px;
  font-weight: 700;
}

.ai-process-stage-list {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ai-process-stage-list button {
  width: 100%;
  min-height: 44px;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) 18px;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border: 0;
  border-radius: 8px;
  color: #45566f;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.ai-process-stage-list button:hover { background: #f1f6fc; }
.ai-process-stage-list button:focus-visible { outline: 3px solid rgba(59, 130, 246, .22); outline-offset: 1px; }
.ai-process-stage-list button > svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.8; }
.ai-stage-marker {
  width: 20px;
  height: 20px;
  display: inline-grid;
  place-items: center;
  border: 1px solid #cbd8e8;
  border-radius: 50%;
  color: #60738e;
  background: #fff;
  font-size: 11px;
  font-weight: 800;
}
.ai-stage-marker.stage-succeeded,
.ai-stage-marker.stage-reused { border-color: #6fcf97; color: #fff; background: #229b5f; }
.ai-stage-marker.stage-failed { border-color: #f1a9a9; color: #fff; background: #cf4545; }
.ai-stage-marker.stage-running,
.ai-stage-marker.stage-processing,
.ai-stage-marker.stage-queued { border-color: #a8c6f5; color: #2f6fed; background: #edf5ff; }
.ai-stage-copy { min-width: 0; }
.ai-stage-copy strong,
.ai-stage-copy small { display: block; }
.ai-stage-copy strong { color: #33465f; font-size: 12px; }
.ai-stage-copy small { overflow: hidden; color: #73839a; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

.generation-queue-note {
  margin: 12px 0 0;
  color: #64748b;
  font-size: 12px;
}

.launch-card-content {
  margin-top: 14px;
}

.workflow-result-panel {
  margin-top: 14px;
  padding: 13px;
  border: 1px solid #dbe4f0;
  border-radius: 11px;
  background: rgba(255, 255, 255, .86);
}

.workflow-result-header,
.workflow-result-item-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.workflow-result-header strong {
  color: #1e293b;
  font-size: 13px;
}

.workflow-result-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 11px;
}

.workflow-result-metrics span {
  padding: 8px 9px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  color: #64748b;
  font-size: 11px;
  text-align: center;
}

.workflow-result-metrics b {
  display: block;
  color: #1d4ed8;
  font-size: 17px;
  line-height: 1.2;
}

.workflow-result-link {
  min-height: 38px;
  margin-top: 10px;
  padding: 7px 10px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
  color: #1d4ed8;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  font-weight: 800;
}

.workflow-result-link:hover,
.workflow-result-item button:hover {
  background: #dbeafe;
}

.pipeline-result-panel {
  background: rgba(255, 255, 255, .72);
}

.workflow-result-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.workflow-result-item {
  min-width: 0;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  background: #fff;
}

.workflow-result-item.result-succeeded,
.workflow-result-item.result-reused {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.workflow-result-item.result-failed {
  border-color: #fecaca;
  background: #fff7f7;
}

.workflow-result-item-heading strong {
  min-width: 0;
  color: #1e293b;
  font-size: 12px;
  line-height: 1.4;
}

.workflow-result-item-heading span {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 10px;
  white-space: nowrap;
}

.workflow-result-item p {
  display: -webkit-box;
  margin: 6px 0 8px;
  overflow: hidden;
  color: #64748b;
  font-size: 10px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.workflow-result-item button {
  min-height: 32px;
  padding: 5px 8px;
  border: 1px solid #dbe4f0;
  border-radius: 7px;
  background: #f8fafc;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-size: 10px;
  font-weight: 700;
}

.process-card-title {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  column-gap: 10px;
  align-items: start;
}

.process-card-title .step-tag {
  grid-row: 1 / span 2;
  align-self: start;
}

.process-card-title h4,
.process-card-title p {
  grid-column: 2;
}

.mini-node-result {
  display: -webkit-box !important;
  margin-top: 3px !important;
  overflow: hidden;
  color: #334155 !important;
  line-height: 1.35 !important;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.plan-summary-line {
  margin-top: 0;
}

.status-chat-msg {
  width: min(100%, 760px);
}

.status-avatar {
  border-color: #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}

.status-bubble {
  width: 100%;
  box-shadow: none;
}

.success-chat-msg .status-bubble {
  border-color: #a7f3d0;
  background: #f0fdf4;
  color: #065f46;
}

.error-chat-msg .status-avatar,
.error-chat-msg .status-bubble {
  border-color: #fecaca;
  background: #fef2f2;
  color: #991b1b;
}

.error-detail-list {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #fecaca;
}

.error-detail-list ol {
  display: grid;
  gap: 6px;
  margin: 7px 0 0;
  padding-left: 20px;
}

.error-detail-list li span {
  display: block;
}

.studio-input-footer {
  position: relative;
  bottom: auto;
  z-index: auto;
  flex: 0 0 auto;
  padding: 14px clamp(14px, 3vw, 36px) 16px;
  background: #fafafa;
}

.modern-input-card {
  width: 100%;
  padding: 12px 14px;
  border-color: #cbd5e1;
}

.modern-textarea {
  min-height: 48px;
}

.toolbar-chip-btn {
  min-height: 40px;
  padding: 7px 11px;
}

.chip-icon {
  flex: 0 0 17px;
  width: 17px;
  height: 17px;
}

.modern-send-circle-btn {
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
}

.modern-send-circle-btn svg {
  width: 20px;
  height: 20px;
}

.studio-compliance-note {
  margin: 0;
  color: #64748b;
  font-size: 11px;
  line-height: 1.5;
}

.view-heading-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
}

/* 诊断弹窗由聊天内按钮触发，浮层不参与工作区高度计算。 */
.llm-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: grid;
  padding: 20px;
  place-items: center;
  background: rgba(15, 23, 42, .55);
  backdrop-filter: blur(4px);
}

.llm-modal-content {
  display: flex;
  flex-direction: column;
  width: min(920px, 100%);
  max-height: calc(100dvh - 40px);
  overflow: hidden;
  border: 1px solid #dbe4f0;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 24px 70px rgba(15, 23, 42, .28);
}

.llm-modal-header {
  display: flex;
  flex: 0 0 auto;
  gap: 20px;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.llm-modal-header h3 {
  margin: 0;
  color: #0f172a;
  font-size: 18px;
}

.modal-close-btn {
  display: grid;
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  padding: 0;
  place-items: center;
  border: 1px solid #e2e8f0;
  border-radius: 50%;
  background: #fff;
  color: #475569;
  cursor: pointer;
  font-size: 16px;
}

.modal-close-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
}

.llm-modal-body {
  display: grid;
  gap: 12px;
  min-height: 0;
  padding: 18px 20px 22px;
  overflow: auto;
}

.modal-empty-hint {
  margin: 0;
  padding: 28px;
  color: #64748b;
  text-align: center;
}

.llm-request-cards {
  display: grid;
  gap: 12px;
}

.llm-detail-card {
  padding: 14px;
  border: 1px solid #dbe4f0;
  border-radius: 12px;
  background: #f8fafc;
}

.llm-detail-card.status-failed {
  border-color: #fecaca;
  background: #fff7f7;
}

.llm-card-header,
.llm-card-title,
.llm-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.llm-card-header {
  justify-content: space-between;
}

.llm-card-title {
  min-width: 0;
}

.llm-card-time {
  color: #64748b;
  font-variant-numeric: tabular-nums;
}

.llm-card-meta {
  margin-top: 10px;
  color: #475569;
  font-size: 11px;
}

.llm-card-meta span {
  min-width: 0;
}

.llm-request-purpose-card {
  margin-top: 12px;
  padding: 10px 12px;
  border-left: 3px solid #4f46e5;
  border-radius: 8px;
  background: #eef2ff;
  color: #312e81;
  font-size: 12px;
  line-height: 1.55;
}

.llm-request-purpose-card strong {
  display: block;
  font-weight: 800;
}

.llm-request-purpose-card p,
.llm-request-purpose {
  margin: 5px 0 0;
}

.pipeline-llm-request .llm-request-purpose,
.drawer-llm-list .llm-request-purpose {
  color: #334155;
  line-height: 1.55;
}

.llm-card-meta code,
.llm-snapshot-view pre,
.repair-feedback-box {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.llm-repair-alert {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #fed7aa;
  border-radius: 9px;
  background: #fff7ed;
  color: #9a3412;
  font-size: 12px;
}

.llm-repair-alert p {
  margin: 5px 0 0;
}

.llm-card-details {
  margin-top: 12px;
}

.llm-card-details > summary {
  min-height: 40px;
  padding: 9px 11px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #334155;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.llm-snapshot-view,
.repair-feedback-box {
  max-width: 100%;
  overflow: auto;
}

.llm-snapshot-view pre,
.repair-feedback-box {
  margin: 8px 0 0;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
  color: #334155;
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.planning-review-overlay {
  position: fixed;
  inset: 0;
  z-index: 1250;
  display: grid;
  padding: 20px;
  place-items: center;
  background: rgba(148, 163, 184, .42);
  backdrop-filter: blur(3px);
}
.planning-review-dialog {
  position: relative;
  width: min(520px, 100%);
  padding: 30px;
  border: 1px solid #dbeafe;
  border-radius: 20px;
  background: #fff;
  box-shadow: 0 24px 70px rgba(30, 64, 175, .18);
  color: #172554;
}
.planning-review-dialog h2 { margin: 8px 0; color: #172554; font-size: 24px; }
.planning-review-dialog > p:not(.section-kicker) { margin: 0; color: #475569; line-height: 1.7; }
.planning-review-close {
  position: absolute;
  top: 13px;
  right: 14px;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  color: #64748b;
  background: #f1f5f9;
  font-size: 22px;
  cursor: pointer;
}
.planning-review-icon {
  display: inline-grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #2563eb;
  font-weight: 800;
}
.planning-review-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 22px 0;
}
.planning-review-metrics div { padding: 12px; border: 1px solid #e0e7ff; border-radius: 11px; background: #f8faff; }
.planning-review-metrics dt { color: #64748b; font-size: 12px; }
.planning-review-metrics dd { margin: 4px 0 0; color: #1d4ed8; font-size: 20px; font-weight: 800; }
.planning-review-feedback { display: grid; gap: 6px; margin: 0 0 18px; color: #334155; font-size: 13px; font-weight: 700; }
.planning-review-feedback textarea { width: 100%; resize: vertical; padding: 9px 10px; border: 1px solid #bfdbfe; border-radius: 9px; color: #172554; font: inherit; font-weight: 400; line-height: 1.5; }
.planning-review-actions { display: flex; justify-content: flex-end; gap: 10px; }

.project-fact-drawer-panel {
  display: grid;
  gap: 10px;
  margin: 12px 0;
  padding: 14px;
  border: 1px solid #dbe4f0;
  border-radius: 12px;
  background: #f8fbff;
}
.project-fact-drawer-heading {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  justify-content: space-between;
}
.project-fact-drawer-heading h4 { margin: 2px 0 0; color: #172554; }
.project-fact-reused-pill {
  padding: 4px 8px;
  border-radius: 999px;
  color: #166534;
  background: #dcfce7;
  font-size: 11px;
  font-weight: 750;
}
.project-fact-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}
.project-fact-metrics div { padding: 8px 10px; border: 1px solid #e0e7ff; border-radius: 9px; background: #fff; }
.project-fact-metrics dt { color: #64748b; font-size: 11px; }
.project-fact-metrics dd { margin: 3px 0 0; color: #1d4ed8; font-size: 17px; font-weight: 800; }
.project-fact-reuse-note,
.project-fact-summary p { margin: 0; color: #475569; font-size: 12px; line-height: 1.55; }
.project-fact-summary,
.project-fact-validation-list { display: grid; gap: 5px; color: #334155; font-size: 12px; line-height: 1.5; }
.project-fact-summary strong,
.project-fact-validation-list > strong { color: #172554; }
.project-fact-validation-list ol { display: grid; gap: 7px; margin: 0; padding-left: 20px; }
.project-fact-validation-list li span { color: #b45309; font-weight: 750; }
.project-fact-validation-list li p { margin: 2px 0 0; overflow-wrap: anywhere; }
.project-fact-recovery-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.project-fact-recovery-actions textarea {
  flex: 1 0 100%;
  width: 100%;
  resize: vertical;
  padding: 9px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 9px;
  background: #fff;
  color: #172554;
  font: inherit;
  line-height: 1.45;
}

/* 执行计划固定在输入框上方；实时动作作为普通聊天消息进入消息流。 */
.legacy-chat-stream .bot-msg > .msg-bubble {
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.pipeline-activity-group {
  width: calc(100% - 54px);
  margin-left: 54px;
  color: #64748b;
  background: transparent;
}
.pipeline-plan-chat-msg .pipeline-activity-group {
  width: 100%;
  margin: 10px 0 0;
}
.pipeline-activity-group > summary {
  width: 100%;
  min-height: 44px;
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 4px 2px;
  border-bottom: 1px solid #0f172a;
  color: #64748b;
  background: transparent;
  cursor: pointer;
  list-style: none;
  font-size: 13px;
  font-weight: 650;
}
.pipeline-activity-group > summary::-webkit-details-marker { display: none; }
.pipeline-activity-group > summary:hover { color: #334155; }
.pipeline-group-chevron {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform .2s ease;
}
.pipeline-activity-group[open] .pipeline-group-chevron { transform: rotate(90deg); }
.generation-stage-bubble .generation-activity-group {
  width: 100%;
  margin: 8px 0 0;
  padding-top: 4px;
  border-top: 1px solid #e0e7ff;
}
.pipeline-activity-list { padding: 2px 0 8px; }
.pipeline-activity-msg {
  width: 100%;
  max-width: 100%;
  display: block;
}
.pipeline-log-entry {
  width: 100%;
  color: #64748b;
  background: transparent;
}
.pipeline-log-entry > summary {
  min-height: 44px;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) 18px;
  gap: 10px;
  align-items: center;
  padding: 5px 2px;
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  list-style: none;
}
.pipeline-log-entry > summary::-webkit-details-marker { display: none; }
.pipeline-log-entry > summary:hover { color: #334155; }
.pipeline-log-icon {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.pipeline-log-icon svg,
.pipeline-log-chevron {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.pipeline-log-line {
  min-width: 0;
  display: flex;
  gap: 7px;
  align-items: baseline;
  white-space: nowrap;
}
.pipeline-log-line strong {
  flex: 0 0 auto;
  color: #475569;
  font-size: 13px;
  font-weight: 650;
}
.pipeline-log-line > span {
  min-width: 0;
  overflow: hidden;
  color: #94a3b8;
  font-size: 12px;
  text-overflow: ellipsis;
}
.activity-running .pipeline-log-line strong { color: #334155; }
.activity-failed .pipeline-log-line strong,
.activity-failed .pipeline-log-icon { color: #b91c1c; }
.pipeline-log-chevron { transition: transform .2s ease; }
.pipeline-log-entry[open] .pipeline-log-chevron { transform: rotate(90deg); }
.pipeline-log-detail {
  margin: 0 0 6px 10px;
  padding: 2px 0 8px 31px;
  border-left: 1px solid #e2e8f0;
}
.pipeline-log-detail p {
  margin: 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.55;
}
.pipeline-log-detail button {
  min-height: 40px;
  margin-top: 5px;
  padding: 6px 0;
  border: 0;
  color: #4f46e5;
  background: transparent;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.pipeline-activity-group > summary:focus-visible,
.pipeline-log-entry > summary:focus-visible,
.pipeline-log-detail button:focus-visible,
.pipeline-plan-actions button:focus-visible,
.vertical-plan-node:focus-visible {
  outline: 3px solid rgba(79, 70, 229, .24);
  outline-offset: 2px;
}
.pipeline-plan-actions button {
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid #c7d2fe;
  border-radius: 9px;
  color: #3730a3;
  background: #eef2ff;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: border-color .2s ease, background-color .2s ease;
}
.pipeline-plan-actions button:hover { border-color: #818cf8; background: #e0e7ff; }

.pipeline-plan-dock {
  width: 100%;
  padding: 14px;
  border: 1px solid #dbe4f0;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(15, 23, 42, .06);
}
.pipeline-processing-summary {
  margin-top: 10px;
  padding: 9px 10px;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
  background: #f8faff;
  color: #475569;
}
.pipeline-processing-summary summary { cursor: pointer; color: #334155; font-size: 12px; font-weight: 750; }
.pipeline-processing-summary ul { display: grid; gap: 5px; margin: 9px 0 0; padding-left: 19px; font-size: 12px; line-height: 1.5; }
.pipeline-plan-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: start;
  padding-bottom: 11px;
  border-bottom: 1px solid #e7edf5;
}
.pipeline-plan-header > div:first-child { min-width: 0; }
.pipeline-plan-kicker,
.pipeline-plan-header strong,
.pipeline-plan-header small { display: block; }
.pipeline-plan-kicker {
  margin-bottom: 3px;
  color: #4f46e5;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .08em;
}
.pipeline-plan-header strong { color: #172033; font-size: 14px; line-height: 1.45; }
.pipeline-plan-header small { margin-top: 3px; color: #64748b; font-size: 11px; line-height: 1.45; }
.pipeline-plan-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  color: #475569;
  font-size: 11px;
  font-weight: 700;
}
.pipeline-plan-summary > span:not(.pipeline-state-pill) {
  padding: 4px 8px;
  border-radius: 999px;
  background: #f1f5f9;
}
.vertical-pipeline-plan {
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}
.vertical-pipeline-plan > li { position: relative; min-width: 0; }
.vertical-pipeline-plan > li:not(:last-child)::after {
  content: '';
  position: absolute;
  z-index: 0;
  top: 31px;
  bottom: -9px;
  left: 15px;
  width: 2px;
  background: #dbe4f0;
}
.vertical-plan-node {
  position: relative;
  z-index: 1;
  width: 100%;
  min-height: 42px;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) minmax(0, auto);
  gap: 9px;
  align-items: center;
  padding: 5px 7px 5px 0;
  border: 0;
  border-radius: 9px;
  color: #334155;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color .2s ease;
}
.vertical-plan-node:hover { background: #f8fafc; }
.vertical-plan-node.stage-running,
.vertical-plan-node.stage-processing,
.vertical-plan-node.stage-queued { background: #f5f7ff; }
.vertical-plan-marker {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 2px solid #dbe4f0;
  border-radius: 50%;
  color: #64748b;
  background: #fff;
  font-size: 11px;
  font-weight: 800;
}
.stage-succeeded .vertical-plan-marker,
.stage-reused .vertical-plan-marker,
.stage-completed .vertical-plan-marker { border-color: #16a34a; color: #fff; background: #16a34a; }
.stage-running .vertical-plan-marker,
.stage-processing .vertical-plan-marker,
.stage-queued .vertical-plan-marker { border-color: #6366f1; color: #4338ca; background: #eef2ff; }
.stage-failed .vertical-plan-marker,
.has-warning .vertical-plan-marker { border-color: #dc2626; color: #fff; background: #dc2626; }
.vertical-plan-copy { min-width: 0; }
.vertical-plan-copy strong,
.vertical-plan-copy small { display: block; }
.vertical-plan-copy strong { color: #273449; font-size: 12px; line-height: 1.4; }
.vertical-plan-copy small,
.vertical-plan-result { color: #64748b; font-size: 10px; line-height: 1.4; }
.vertical-plan-result { max-width: 320px; overflow-wrap: anywhere; text-align: right; }
.pipeline-plan-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #e7edf5;
  color: #64748b;
  font-size: 11px;
}
.pipeline-plan-actions button { margin-left: 4px; }

/* 对话统一为浅色消息流，用户与 AI 的内容在时间线上清晰区分。 */
.legacy-chat-stream > .chat-msg.user-msg {
  display: flex;
  flex-direction: row;
  justify-content: flex-end;
  align-items: flex-end;
  width: 100%;
}
.legacy-chat-stream > .chat-msg.user-msg .msg-avatar { display: none; }
.legacy-chat-stream > .chat-msg.user-msg .msg-bubble {
  margin-left: auto;
  margin-right: 0;
  max-width: min(760px, 80%);
  border: none;
  border-radius: 14px 14px 2px 14px;
  background: #2563eb;
  color: #ffffff;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.15);
  padding: 10px 16px;
  font-size: 14px;
  line-height: 1.5;
  position: relative;
  align-self: flex-end;
}
.legacy-chat-stream > .chat-msg.user-msg .msg-bubble p {
  color: #ffffff;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-delete-btn {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 18px;
  height: 18px;
  display: none;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #94a3b8;
  color: #ffffff;
  border: none;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  box-shadow: 0 1px 3px rgba(0,0,0,0.15);
  transition: all 0.15s ease;
}
.legacy-chat-stream > .chat-msg.user-msg:hover .chat-delete-btn {
  display: flex;
}
.chat-delete-btn:hover {
  background: #ef4444;
  color: #ffffff;
}
.legacy-chat-stream > .chat-msg.bot-msg:not(.timeline-step-msg):not(.pipeline-plan-chat-msg) .msg-bubble {
  border: none;
  border-radius: 14px;
  background: #ffffff;
  color: #334155;
  box-shadow: none;
}

@media (max-width: 1100px) {
  .initial-chat-studio {
    width: 75vw;
    max-width: 75vw;
  }
}

@media (max-width: 760px) {
  .initial-chat-studio {
    width: 100%;
    max-width: 100%;
    min-width: 0;
  }

  .studio-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }

  .studio-header-stats {
    width: 100%;
    justify-content: flex-start;
  }

  .studio-chat-body {
    padding: 16px 12px 20px;
  }

  .workspace-cockpit {
    gap: 12px;
  }

  .cockpit-steps {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .cockpit-steps li {
    min-height: 40px;
  }

  .cockpit-current-card {
    grid-template-columns: 1fr;
    gap: 16px;
    padding: 18px;
  }

  .cockpit-current-copy h4 {
    font-size: 18px;
  }

  .cockpit-current-action {
    width: 100%;
  }

  .cockpit-primary-action {
    width: 100%;
  }

  .cockpit-generation-history li {
    align-items: flex-start;
    flex-direction: column;
  }

  .cockpit-generation-history button {
    width: 100%;
  }

  .chat-msg,
  .chat-msg.process-msg,
  .status-chat-msg {
    width: 100%;
    max-width: 100%;
    gap: 9px;
  }

  .chat-msg.user-msg {
    width: auto;
    max-width: 92%;
  }

  .msg-bubble {
    padding: 12px 13px;
  }

  .workflow-step-header {
    grid-template-columns: 1fr;
  }

  .workflow-result-header {
    flex-direction: column;
  }

  .workflow-step-status,
  .workflow-result-status {
    justify-self: start;
  }

  .workflow-step-heading {
    grid-template-columns: 1fr;
    gap: 7px;
  }

  .workflow-step-heading h4 {
    font-size: 16px;
  }

  .workflow-result-metrics {
    grid-template-columns: 1fr;
  }

  .process-card-header {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .process-card-actions {
    align-items: stretch;
  }

  .process-metrics {
    justify-content: flex-start;
  }

  .process-detail-button {
    width: 100%;
  }

  .chat-stepper-list {
    grid-template-columns: 1fr;
  }

  .chat-llm-status {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .chat-llm-status button {
    grid-column: 1 / -1;
    width: 100%;
  }

  .plan-actions-row {
    align-items: stretch;
    flex-direction: column;
  }

  .plan-actions-row .btn {
    width: 100%;
    min-height: 44px;
  }

  .studio-input-footer {
    padding: 12px;
  }

  .workbench-entry-card {
    grid-template-columns: 40px minmax(0, 1fr);
  }
  .workbench-entry-action {
    grid-column: 1 / -1;
    justify-content: center;
    width: 100%;
  }

  .pipeline-plan-header { grid-template-columns: 1fr; gap: 9px; }
  .pipeline-plan-summary { justify-content: flex-start; }
  .vertical-plan-node { grid-template-columns: 30px minmax(0, 1fr); }
  .vertical-plan-result { grid-column: 2; text-align: left; }
  .pipeline-plan-actions { justify-content: flex-start; }
  .pipeline-plan-actions button { width: 100%; margin-left: 0; }
  .pipeline-log-line { display: grid; gap: 2px; white-space: normal; }
  .pipeline-log-line > span { white-space: nowrap; }
  .pipeline-activity-group { width: calc(100% - 42px); margin-left: 42px; }

  .modern-card-toolbar {
    gap: 10px;
    align-items: flex-end;
  }

  .toolbar-left {
    flex: 1;
  }

  .toolbar-chip-btn {
    min-height: 44px;
  }

  .modern-textarea {
    font-size: 16px;
  }

  .llm-modal-overlay {
    padding: 10px;
  }

  .llm-modal-content {
    max-height: calc(100dvh - 20px);
    border-radius: 12px;
  }

  .llm-modal-header,
  .llm-modal-body {
    padding-inline: 14px;
  }

  .planning-review-overlay { padding: 12px; }
  .planning-review-dialog { padding: 25px 18px 18px; border-radius: 16px; }
  .planning-review-metrics { grid-template-columns: 1fr 1fr; }
  .planning-review-actions { flex-direction: column-reverse; }
  .planning-review-actions .btn { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .studio-chat-body {
    scroll-behavior: auto;
  }

  .llm-status-running .llm-status-dot,
  .modern-send-circle-btn,
  .toolbar-chip-btn,
  .chat-stepper-node,
  .pipeline-group-chevron,
  .pipeline-log-chevron {
    animation: none;
    transition: none;
  }
}

.legacy-preview-overlay {
  position: fixed;
  z-index: 1000;
  inset: 0;
  padding: 24px;
  background: rgb(15 23 42 / 52%);
}
.legacy-preview-dialog {
  display: flex;
  width: min(1440px, 100%);
  height: 100%;
  min-height: 0;
  margin: 0 auto;
  flex-direction: column;
  overflow: hidden;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 24px 70px rgb(15 23 42 / 28%);
}
.legacy-preview-dialog > header {
  display: flex;
  flex: 0 0 auto;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #dbe4f0;
}
.legacy-preview-dialog > header h3 { margin: 0; color: #172554; font-size: 16px; }
.legacy-preview-dialog > header button,
.legacy-preview-trigger {
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  color: #3730a3;
  background: #eef2ff;
  cursor: pointer;
  font-weight: 700;
}
.legacy-preview-dialog > header button { min-height: 44px; }
.legacy-preview-body { flex: 1; min-height: 0; overflow-y: auto; padding: 18px 20px 28px; }
.legacy-preview-error { color: #b91c1c; }
.file-diff-stats { align-items: center; gap: 8px; }

@media (max-width: 720px) {
  .legacy-preview-overlay { padding: 0; }
  .legacy-preview-dialog { border-radius: 0; }
}
</style>
