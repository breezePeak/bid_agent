<template>
  <div class="step-detail-view">
    <div class="sdv-toolbar">
      <div class="sdv-title">
        <strong>{{ title }}</strong>
        <span v-if="subtitle" class="sdv-sub">{{ subtitle }}</span>
      </div>
      <div class="sdv-actions">
        <button class="btn btn-sm" @click="refresh" :disabled="loading">刷新</button>
        <button class="btn btn-sm" @click="$emit('close')">返回聊天</button>
      </div>
    </div>

    <div class="sdv-body" v-if="loading && !hasLoadedOnce"><div class="sdv-loading">加载中…</div></div>
    <div class="sdv-body" v-else>
      <div v-if="error" class="sdv-error" style="padding:12px 0">{{ error }}</div>
      <template v-if="!error || hasLoadedOnce">
      <!-- Materials checklist step -->
      <template v-if="isMaterials">
        <div class="sdv-section">
          <div class="sdv-banner warn">
            <div class="sdv-banner-title">材料 / 资格清单</div>
            <div class="sdv-banner-help">缺材料可标「待补」写作留白；补齐后标「已齐」并点补料回填。</div>
          </div>
          <MaterialsChecklistPanel :run-id="runId" />
        </div>
      </template>

      <div class="sdv-section" v-else-if="!isManualReview && stageIssues.length">
        <h4>阻断/待处理问题（{{ stageIssues.length }}）</h4>
        <div class="sdv-actions-row" style="margin-bottom:8px">
          <button class="btn btn-sm" :disabled="!!repairBusy" @click="batchPreview">批量预览修复</button>
          <button class="btn btn-sm btn-primary" :disabled="!!repairBusy" @click="batchRepair">批量最小修复</button>
        </div>
        <div v-if="repairMsg" class="sdv-repair-msg">{{ repairMsg }}</div>
        <div v-for="iss in stageIssues" :key="iss.id" class="sdv-item" :class="'sev-' + (iss.severity === 'block' ? 'fatal' : 'info')">
          <div class="sdv-item-head">
            <span class="sdv-id">{{ iss.code }}</span>
            <span class="sdv-badge">{{ iss.severity }}</span>
            <span class="sdv-name">{{ iss.title }}</span>
          </div>
          <div class="sdv-req">{{ iss.detail }}</div>
          <div class="sdv-req" v-if="iss.likely_cause_stage">可能根因阶段：{{ iss.likely_cause_stage }}</div>
          <div class="sdv-actions-row">
            <button class="btn btn-sm" :disabled="!!repairBusy" @click="previewRepair(iss)">预览修复计划</button>
            <button class="btn btn-sm btn-primary" :disabled="!!repairBusy" @click="runRepair(iss)">
              {{ repairBusy === iss.id ? '修复中…' : '确认最小修复' }}
            </button>
            <button class="btn btn-sm" :disabled="!!repairBusy" @click="explainCause(iss)">智能归因</button>
            <button class="btn btn-sm" :disabled="!!repairBusy || iss.severity !== 'block'" @click="acceptRisk(iss)">接受风险</button>
          </div>
          <div class="sdv-req" v-if="iss.cause_reason">归因：{{ iss.likely_cause_stage }} — {{ iss.cause_reason }}</div>
          <div class="sdv-detail" v-if="iss._plan">
            <div><b>计划：</b>{{ iss._plan.summary }}</div>
            <div v-for="(st, si) in (iss._plan.steps || [])" :key="si">• {{ st.label || st.type }}</div>
            <div v-if="(iss._plan.revalidate || []).length"><b>重验：</b>{{ (iss._plan.revalidate || []).join(' → ') }}</div>
          </div>
        </div>
      </div>
      <!-- Manual review -->
      <template v-else-if="isManualReview">
        <div class="sdv-banner" :class="{ blocking: (summary?.total_pending || 0) > 0 }">
          <div class="sdv-banner-title">人工复核</div>
          <div class="sdv-banner-stats">
            待处理 {{ summary?.total_pending ?? 0 }}
            · 弱证据 {{ summary?.template_evidence_pending ?? 0 }}
            · 评分覆盖 {{ summary?.score_coverage_pending ?? 0 }}
            · 章节 {{ summary?.chapter_review_pending ?? 0 }}
            · 全文 {{ summary?.global_review_pending ?? 0 }}
            · 合规 {{ summary?.compliance_pending ?? 0 }}
          </div>
        </div>
        <div class="sdv-filters">
          <button
            v-for="f in mrCategories"
            :key="f.key"
            class="sdv-filter"
            :class="{ on: mrCategory === f.key }"
            @click="switchMrCategory(f.key)"
          >{{ f.label }}</button>
        </div>
        <div v-if="actionMsg" class="sdv-repair-msg">{{ actionMsg }}</div>
        <div class="sdv-list">
          <div v-for="item in mrItems" :key="item.item_id" class="sdv-item soft">
            <div class="sdv-item-head">
              <span class="sdv-id">{{ item.item_id }}</span>
              <span class="sdv-badge">{{ itemStatus(item) }}</span>
              <span class="sdv-name">{{ itemTitle(item) }}</span>
            </div>
            <div class="sdv-req" v-if="itemMeta(item)">{{ itemMeta(item) }}</div>
            <div class="sdv-req" v-if="item.description || item.suggestion">
              {{ item.description || item.suggestion }}
            </div>
            <textarea
              class="sdv-note"
              :value="notes[item.item_id] ?? noteOf(item)"
              @input="notes[item.item_id] = $event.target.value"
              placeholder="填写人工说明或修订指令"
              rows="2"
            ></textarea>
            <div class="sdv-actions-row">
              <button class="btn btn-sm btn-primary" :disabled="!!busyId" @click="submitMr(item, 'accepted')">接受/确认</button>
              <button class="btn btn-sm" :disabled="!!busyId" @click="submitMr(item, 'resolved')">已处理</button>
              <button class="btn btn-sm" :disabled="!!busyId" @click="submitMr(item, 'dismissed')">忽略</button>
              <button
                v-if="chapterIdOf(item)"
                class="btn btn-sm"
                @click="emit('open-chapter', chapterIdOf(item))"
              >查看章节</button>
            </div>
          </div>
          <div v-if="!mrItems.length" class="sdv-empty">当前分类暂无待处理项</div>
        </div>
        <div class="sdv-section" v-if="(summary?.latest_replay_requests || []).length">
          <h4>最近重跑建议</h4>
          <div v-for="(r, i) in summary.latest_replay_requests" :key="i" class="sdv-replay-row">
            <span class="sdv-line">{{ r.category }} / {{ r.item_id }} → {{ r.recommended_stage }}</span>
            <button
              v-if="stageToCommand(r.recommended_stage)"
              class="btn btn-sm btn-primary"
              @click="emit('rerun-stage', stageToCommand(r.recommended_stage))"
            >从该阶段重跑</button>
          </div>
        </div>
      </template>

      <!-- Compliance -->
      <template v-else-if="isCompliance && compliance">
        <div class="sdv-banner" :class="{ blocking: compliance.blocking }">
          <div class="sdv-banner-title">
            {{ compliance.blocking ? '合规阻断 · 暂不可出正式稿' : (compliance.need_manual_review ? '合规待人工复核' : '合规检查结果') }}
          </div>
          <div class="sdv-banner-stats">
            失败 {{ counts.fail || 0 }} · 警告 {{ counts.warn || 0 }} · 通过 {{ counts.pass || 0 }}
            · 最高 {{ severityLabel(compliance.max_severity) }}
          </div>
        </div>
        <div class="sdv-filters">
          <button v-for="f in filters" :key="f.key" class="sdv-filter" :class="{ on: filter === f.key }" @click="filter = f.key">{{ f.label }}</button>
        </div>
        <div class="sdv-list">
          <div v-for="item in filteredItems" :key="item.check_id || item.check_name" class="sdv-item" :class="['sev-' + (item.severity || 'info'), 'st-' + (item.status || '')]" @click="toggle(item.check_id)">
            <div class="sdv-item-head">
              <span class="sdv-id">{{ item.check_id || '—' }}</span>
              <span class="sdv-badge">{{ severityLabel(item.severity) }}</span>
              <span class="sdv-badge">{{ item.status === 'fail' ? '失败' : (item.status === 'warn' ? '警告' : item.status) }}</span>
              <span class="sdv-name">{{ item.check_name || item.check_type }}</span>
            </div>
            <div class="sdv-req" v-if="item.requirement">{{ item.requirement }}</div>
            <div class="sdv-detail" v-if="openId === item.check_id">
              <div v-if="item.suggestion"><b>建议：</b>{{ item.suggestion }}</div>
              <div v-if="item.check_type"><b>类型：</b>{{ item.check_type }}</div>
            </div>
          </div>
          <div v-if="!filteredItems.length" class="sdv-empty">当前筛选下没有条目</div>
        </div>
      </template>

      <template v-else>
        <!-- Summary -->
        <div class="sdv-section" v-if="summaryRows.length">
          <h4>概要</h4>
          <div class="sdv-kv" v-for="row in summaryRows" :key="row.k"><span class="k">{{ row.k }}</span><span class="v">{{ row.v }}</span></div>
        </div>
        <div class="sdv-section" v-if="detail.timing && detail.timing.duration_label">
          <h4>耗时</h4>
          <div>{{ detail.timing.duration_label }}</div>
        </div>

        <!-- Global review -->
        <div class="sdv-section" v-if="globalReview">
          <div class="sdv-banner" :class="{ blocking: globalReview.blocking || (globalReview.blocking_reasons || []).length }" style="margin-bottom:12px">
            <div class="sdv-banner-title">
              {{ (globalReview.blocking || (globalReview.blocking_reasons || []).length) ? '全文审核阻断 · 请先处理下列问题再继续' : '全文审核已通过门禁' }}
            </div>
            <div class="sdv-banner-stats" v-if="(globalReview.blocking_reasons || []).length">
              <div v-for="(r, i) in globalReview.blocking_reasons" :key="i">• {{ r }}</div>
            </div>
          </div>
          <h4>全文审核结论</h4>
          <div class="sdv-flags">
            <span class="sdv-flag" :class="{ bad: globalReview.project_name_consistent === false }">项目名一致: {{ yn(globalReview.project_name_consistent) }}</span>
            <span class="sdv-flag" :class="{ bad: globalReview.bidder_name_consistent === false }">投标人一致: {{ yn(globalReview.bidder_name_consistent) }}</span>
            <span class="sdv-flag" :class="{ bad: globalReview.need_manual_review }">需人工复核: {{ yn(globalReview.need_manual_review) }}</span>
          </div>
          <div v-if="(globalReview.uncovered_score_points || []).length" class="sdv-block">
            <b>未覆盖评分点（{{ globalReview.uncovered_score_points.length }}）</b>
            <div class="sdv-tags">
              <span v-for="id in globalReview.uncovered_score_points.slice(0, 40)" :key="id" class="sdv-tag">{{ id }}</span>
            </div>
          </div>
          <div v-if="(globalReview.chapter_conflicts || []).length" class="sdv-block">
            <b>章节冲突</b>
            <div v-for="(c, i) in globalReview.chapter_conflicts.slice(0, 20)" :key="i" class="sdv-line">{{ formatValue(c) }}</div>
          </div>
          <div v-if="(globalReview.fabrication_risks || []).length" class="sdv-block">
            <b>编造风险</b>
            <div v-for="(c, i) in globalReview.fabrication_risks.slice(0, 20)" :key="i" class="sdv-line">{{ formatValue(c) }}</div>
          </div>
          <div v-if="(globalReview.suggestions || []).length" class="sdv-block">
            <b>建议</b>
            <div v-for="(c, i) in globalReview.suggestions" :key="i" class="sdv-line">• {{ c }}</div>
          </div>
        </div>

        <!-- Score points -->
        <div class="sdv-section" v-if="scorePoints.length">
          <h4>评分点（{{ scorePoints.length }}）</h4>
          <div v-for="row in scorePoints.slice(0, 50)" :key="row.id || row.title" class="sdv-item soft">
            <div class="sdv-item-head">
              <span class="sdv-id">{{ row.id }}</span>
              <span class="sdv-badge" v-if="row.score != null">{{ row.score }} 分</span>
              <span class="sdv-name">{{ row.title }}</span>
            </div>
            <div class="sdv-req" v-if="row.requirement">{{ row.requirement }}</div>
          </div>
        </div>

        <!-- Review rows -->
        <div class="sdv-section" v-if="reviewRows.length">
          <h4>章节审核（{{ reviewRows.length }}）</h4>
          <div v-for="row in reviewRows" :key="row.chapter_id" class="sdv-item soft" @click="toggle(row.chapter_id)">
            <div class="sdv-item-head">
              <span class="sdv-id">{{ row.chapter_id }}</span>
              <span class="sdv-badge" v-if="row.problem_count != null">问题 {{ row.problem_count }}</span>
              <span class="sdv-name">{{ row.chapter_title || row.name || '' }}</span>
            </div>
            <div class="sdv-detail" v-if="openId === row.chapter_id && (row.problems || []).length">
              <div v-for="(p, i) in row.problems.slice(0, 12)" :key="i" class="sdv-line">• {{ p }}</div>
            </div>
          </div>
        </div>

        <!-- Score coverage -->
        <div class="sdv-section" v-if="scoreCoverage">
          <h4>评分覆盖</h4>
          <div class="sdv-kv" v-for="(v, k) in (scoreCoverage.summary || {})" :key="k">
            <span class="k">{{ k }}</span><span class="v">{{ formatValue(v) }}</span>
          </div>
          <div v-if="(scoreCoverage.uncovered_score_points || []).length" class="sdv-block">
            <b>未覆盖（{{ scoreCoverage.uncovered_score_points.length }}）</b>
            <div class="sdv-tags">
              <span v-for="id in scoreCoverage.uncovered_score_points.slice(0, 50)" :key="id" class="sdv-tag bad">{{ id }}</span>
            </div>
          </div>
          <div v-if="(scoreCoverage.weak_score_points || []).length" class="sdv-block">
            <b>弱覆盖（{{ scoreCoverage.weak_score_points.length }}）</b>
            <div class="sdv-tags">
              <span v-for="id in scoreCoverage.weak_score_points.slice(0, 50)" :key="id" class="sdv-tag warn">{{ id }}</span>
            </div>
          </div>
        </div>

        <!-- Score estimate -->
        <div class="sdv-section" v-if="scoreEstimate && Object.keys(scoreEstimate).length">
          <h4>估分结果</h4>
          <div class="sdv-kv" v-for="(v, k) in scoreEstimate" :key="k" v-show="typeof v !== 'object'">
            <span class="k">{{ k }}</span><span class="v">{{ formatValue(v) }}</span>
          </div>
          <pre class="sdv-pre" v-if="scoreEstimateRaw">{{ scoreEstimateRaw }}</pre>
        </div>

        <!-- Outline -->
        <div class="sdv-section" v-if="outlineChapters.length">
          <h4>大纲章节（{{ outlineChapters.length }}）</h4>
          <div v-for="c in outlineChapters" :key="c.id" class="sdv-item soft">
            <div class="sdv-item-head">
              <span class="sdv-id">{{ c.id }}</span>
              <span class="sdv-name">{{ c.title }}</span>
            </div>
            <div class="sdv-req" v-if="c.description">{{ c.description }}</div>
            <div class="sdv-tags" v-if="(c.score_point_ids || []).length">
              <span v-for="id in c.score_point_ids.slice(0, 12)" :key="id" class="sdv-tag">{{ id }}</span>
            </div>
          </div>
        </div>

        <!-- Chapter files -->
        <div class="sdv-section" v-if="chapterFiles.length">
          <h4>已生成章节（{{ chapterFiles.length }}）</h4>
          <div v-for="f in chapterFiles" :key="f.chapter_id" class="sdv-art">
            <span>{{ f.chapter_id }} · {{ f.path }}</span>
            <span>{{ formatSize(f.size) }}</span>
          </div>
        </div>

        <!-- Summaries -->
        <div class="sdv-section" v-if="chapterSummaries.length">
          <h4>章节摘要（{{ chapterSummaries.length }}）</h4>
          <div v-for="s in chapterSummaries" :key="s.chapter_id" class="sdv-item soft">
            <div class="sdv-item-head">
              <span class="sdv-id">{{ s.chapter_id }}</span>
              <span class="sdv-name">{{ s.title || '摘要' }}</span>
            </div>
            <div class="sdv-req" v-if="s.summary">{{ s.summary }}</div>
          </div>
        </div>

        <!-- Facts -->
        <div class="sdv-section" v-if="globalFacts && Object.keys(globalFacts).length">
          <h4>全局事实</h4>
          <pre class="sdv-pre">{{ factsRaw }}</pre>
        </div>

        <!-- Format check -->
        <div class="sdv-section" v-if="formatCheck && Object.keys(formatCheck).length">
          <h4>格式检查</h4>
          <pre class="sdv-pre">{{ formatRaw }}</pre>
        </div>

        <!-- Artifacts -->
        <div class="sdv-section" v-if="(detail.produces || []).length">
          <h4>产物文件</h4>
          <div v-for="a in detail.produces" :key="a.path" class="sdv-art">
            <span>{{ a.path }}</span>
            <span>{{ a.exists ? '✓' : '✗' }}{{ a.size ? ' · ' + formatSize(a.size) : '' }}</span>
          </div>
        </div>
        <div class="sdv-section" v-if="(detail.requires || []).length">
          <h4>依赖文件</h4>
          <div v-for="a in detail.requires" :key="a.path" class="sdv-art">
            <span>{{ a.path }}</span>
            <span>{{ a.exists ? '✓' : '✗' }}</span>
          </div>
        </div>

        <div v-if="!hasAnyContent && !error" class="sdv-empty">该节点暂无可展示成果，可能尚未执行完成。</div>
      </template>
      </template>
    </div>
    <Teleport to="body">
      <div v-if="repairDialog" class="dialog-overlay sdv-repair-overlay" @click.self="closeRepairDialog">
        <section class="dialog sdv-repair-dialog" role="dialog" aria-modal="true" aria-labelledby="repair-dialog-title">
          <div class="dialog-header">
            <h2 id="repair-dialog-title">{{ repairDialog.title }}</h2>
            <button class="btn btn-icon" aria-label="关闭" @click="closeRepairDialog">&times;</button>
          </div>
          <div class="dialog-body">
            <p v-if="repairDialog.description" class="sdv-dialog-description">{{ repairDialog.description }}</p>
            <template v-if="repairDialog.kind === 'risk'">
              <label class="sdv-dialog-label" for="repair-risk-reason">备注（可选）</label>
              <textarea id="repair-risk-reason" v-model="riskReason" class="sdv-note" rows="3" placeholder="可选填写说明，不填也可直接确认"></textarea>
              <p class="sdv-dialog-hint">该操作会保留审计记录；管理员未开启该功能时将无法提交。</p>
            </template>
            <template v-else-if="repairDialog.kind === 'result'">
              <div class="sdv-repair-result" :class="{ bad: !repairDialog.result?.ok }">
                {{ repairDialog.result?.message || '操作已结束' }}
              </div>
              <div v-if="repairDialog.result?.results?.length" class="sdv-plan-list">
                <div v-for="(result, index) in repairDialog.result.results" :key="index" class="sdv-plan-row">
                  <b>{{ result.issue_id || result.plan?.issue?.code || `问题 ${index + 1}` }}</b>：{{ result.message || (result.ok ? '已执行' : '执行失败') }}
                </div>
              </div>
            </template>
            <template v-else>
              <div v-for="plan in repairDialog.plans" :key="plan.issue_id" class="sdv-plan-card">
                <div class="sdv-plan-title">{{ plan.issue?.code || plan.issue_id }} · {{ plan.issue?.title || '修复计划' }}</div>
                <div class="sdv-dialog-hint">{{ plan.summary }}</div>
                <ol v-if="plan.steps?.length" class="sdv-plan-list">
                  <li v-for="(step, index) in plan.steps" :key="index" class="sdv-plan-row" :class="{ manual: isManualStep(step) }">
                    <b>{{ step.label || actionLabel(step.type) }}</b><span v-if="isManualStep(step)">（需人工处理，不会自动修改文件）</span>
                  </li>
                </ol>
                <p v-else class="sdv-dialog-hint">此问题没有可自动执行的修复动作，仅会重跑检查。</p>
                <div v-if="plan.revalidate?.length" class="sdv-dialog-hint">完成后将重验：{{ plan.revalidate.join(' → ') }}</div>
              </div>
            </template>
            <div class="dialog-footer">
              <button class="btn" @click="closeRepairDialog">{{ repairDialog.kind === 'result' ? '关闭' : '取消' }}</button>
              <button v-if="repairDialog.kind === 'execute' || repairDialog.kind === 'batch-execute'" class="btn btn-primary" :disabled="!!repairBusy" @click="confirmRepairDialog">
                {{ repairBusy ? '执行中…' : '确认执行' }}
              </button>
              <button v-else-if="repairDialog.kind === 'risk'" class="btn btn-danger" :disabled="!!repairBusy" @click="confirmRepairDialog">
                {{ repairBusy ? '提交中…' : '确认接受风险' }}
              </button>
            </div>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import {
  fetchComplianceReport,
  fetchWorkflowStepDetail,
  fetchIssues,
  previewIssueRepair,
  executeIssueRepair,
  acceptIssueRisk,
  explainIssueCause,
  batchPreviewRepairs,
  batchExecuteRepairs,
  fetchManualReviewSummary,
  fetchManualReviewItems,
  updateManualReview,
  confirmWorkspaceAction,
} from '../api'
import MaterialsChecklistPanel from './MaterialsChecklistPanel.vue'

const props = defineProps({
  runId: { type: String, required: true },
  command: { type: String, required: true },
})
const emit = defineEmits(['close', 'open-chapter', 'rerun-stage'])

const loading = ref(false)
const error = ref('')
const hasLoadedOnce = ref(false)
const title = ref('')
const subtitle = ref('')
const detail = ref({})
const compliance = ref(null)
const filter = ref('fail')
const openId = ref(null)
const issueList = ref([])
const repairBusy = ref('')
const repairMsg = ref('')
const repairDialog = ref(null)
const riskReason = ref('')
const summary = ref(null)
const mrItems = ref([])
const mrCategory = ref('score_coverage')
const notes = ref({})
const busyId = ref('')
const actionMsg = ref('')

const mrCategories = [
  { key: 'template_evidence', label: '弱证据/模板缺口' },
  { key: 'score_coverage', label: '未覆盖评分点' },
  { key: 'chapter_review', label: '章节审核问题' },
  { key: 'global_review', label: '全文风险' },
]

const isManualReview = computed(() => String(props.command || '').startsWith('manual-review'))
const isCompliance = computed(() => props.command === 'compliance-check' || props.command === 'compliance')
const isMaterials = computed(() => {
  const c = String(props.command || '')
  return c === 'build-materials-checklist' || c === 'materials-checklist' || c === 'materials'
})
const workflowCommand = computed(() => {
  const c = String(props.command || '')
  if (c.startsWith('manual-review')) return ''
  return c
})

function chapterIdOf(item) {
  if (!item || typeof item !== 'object') return ''
  return String(item.chapter_id || item.target_chapter_id || item.heading_id || '').trim()
}

function stageToCommand(stageId) {
  const s = String(stageId || '').trim()
  if (!s) return ''
  // stage_id uses underscore; workflow commands use hyphen
  const map = {
    select_contexts: 'select-context-all',
    plan_chapter_jobs: 'plan-jobs',
    write_chapters: 'write-all',
    review_fix_chapters: 'review-fix-all',
    global_review: 'global-review',
    compliance_check: 'compliance-check',
    build_materials_checklist: 'build-materials-checklist',
    generate_outline: 'generate-outline',
    extract_facts: 'extract-facts',
  }
  if (map[s]) return map[s]
  if (s.includes('-')) return s
  return s.replace(/_/g, '-')
}
const counts = computed(() => compliance.value?.counts || {})
const filters = [
  { key: 'fail', label: '失败' },
  { key: 'warn', label: '警告' },
  { key: 'fatal', label: '致命' },
  { key: 'critical', label: '严重' },
  { key: 'all', label: '全部问题' },
]
const filteredItems = computed(() => {
  const items = compliance.value?.items || []
  if (filter.value === 'all') return items.filter(i => i.status === 'fail' || i.status === 'warn')
  if (filter.value === 'fail' || filter.value === 'warn') return items.filter(i => i.status === filter.value)
  return items.filter(i => i.severity === filter.value)
})
const summaryRows = computed(() => {
  const s = detail.value?.summary
  if (!s || typeof s !== 'object') return []
  return Object.keys(s).map(k => ({ k, v: formatValue(s[k]) }))
})
const d = computed(() => detail.value?.details || {})
const globalReview = computed(() => d.value.global_review || null)
const scorePoints = computed(() => d.value.score_point_rows || [])
const reviewRows = computed(() => d.value.review_rows || [])
const scoreCoverage = computed(() => d.value.score_coverage || null)
const scoreEstimate = computed(() => d.value.score_estimate || null)
const scoreEstimateRaw = computed(() => {
  const s = scoreEstimate.value
  if (!s) return ''
  try { return JSON.stringify(s, null, 2).slice(0, 6000) } catch { return '' }
})
const outlineChapters = computed(() => d.value.outline_chapters || [])
const chapterFiles = computed(() => d.value.chapter_files || [])
const chapterSummaries = computed(() => d.value.chapter_summaries || [])
const globalFacts = computed(() => d.value.global_facts || null)
const factsRaw = computed(() => {
  try { return JSON.stringify(globalFacts.value || {}, null, 2).slice(0, 6000) } catch { return '' }
})
const formatCheck = computed(() => d.value.format_check || null)
const formatRaw = computed(() => {
  try { return JSON.stringify(formatCheck.value || {}, null, 2).slice(0, 6000) } catch { return '' }
})
const hasAnyContent = computed(() =>
  summaryRows.value.length
  || globalReview.value
  || scorePoints.value.length
  || reviewRows.value.length
  || scoreCoverage.value
  || (scoreEstimate.value && Object.keys(scoreEstimate.value).length)
  || outlineChapters.value.length
  || chapterFiles.value.length
  || chapterSummaries.value.length
  || (globalFacts.value && Object.keys(globalFacts.value).length)
  || (formatCheck.value && Object.keys(formatCheck.value).length)
  || (detail.value.produces || []).length
)

function severityLabel(sev) {
  const m = { fatal: '致命', critical: '严重', major: '重要', minor: '次要', info: '提示' }
  return m[sev] || sev || '—'
}
function yn(v) {
  if (v === true) return '是'
  if (v === false) return '否'
  return '—'
}
function formatValue(v) {
  if (v == null) return '—'
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch { return String(v) }
  }
  return String(v)
}
function formatSize(n) {
  const x = Number(n) || 0
  if (x < 1024) return x + ' B'
  if (x < 1024 * 1024) return (x / 1024).toFixed(1) + ' KB'
  return (x / 1024 / 1024).toFixed(1) + ' MB'
}
function toggle(id) {
  openId.value = openId.value === id ? null : id
}


const stageIssues = computed(() => {
  if (isManualReview.value) return []
  const cmd = workflowCommand.value || props.command
  const stageMap = {
    'global-review': 'global_review',
    'compliance-check': 'compliance_check',
  }
  const sid = stageMap[cmd] || String(cmd || '').replace(/-/g, '_')
  return (issueList.value || []).filter((i) => {
    const st = String(i.status || '')
    if (!['open', 'in_progress'].includes(st)) return false
    return i.command === cmd || i.stage_id === sid
  })
})

async function loadIssues() {
  try {
    const { data } = await fetchIssues('open')
    if (data && data.ok) issueList.value = data.issues || []
    else issueList.value = []
  } catch (e) {
    issueList.value = []
  }
}

async function previewRepair(iss) {
  repairMsg.value = ''
  try {
    const { data } = await previewIssueRepair(iss.id)
    if (data && data.ok) {
      iss._plan = data
      repairDialog.value = { kind: 'preview', title: '最小修复计划', description: '以下仅为预览，不会修改文件。', plans: [data] }
    } else {
      repairMsg.value = (data && data.message) || '预览失败'
    }
  } catch (e) {
    repairMsg.value = e.message || '预览失败'
  }
}

async function runRepair(iss) {
  try {
    if (!iss._plan) {
      const { data } = await previewIssueRepair(iss.id)
      if (!data?.ok) throw new Error(data?.message || '无法生成修复计划')
      iss._plan = data
    }
    repairDialog.value = { kind: 'execute', title: '确认执行最小修复', description: '系统只会执行下列可自动化动作，并在完成后重验合规门禁。', plans: [iss._plan], issueIds: [iss.id] }
  } catch (e) {
    repairMsg.value = e.message || '修复失败'
  }
}


async function explainCause(iss) {
  repairMsg.value = '正在归因…'
  try {
    const { data } = await explainIssueCause(iss.id)
    if (data && data.ok) {
      iss.likely_cause_stage = data.likely_cause_stage
      iss.cause_reason = data.reason
      iss.cause_confidence = data.confidence
      repairMsg.value = `归因：${data.likely_cause_stage}（${data.source}，置信 ${data.confidence ?? '-'}） ${data.reason || ''}`
      await loadIssues()
    } else {
      repairMsg.value = (data && data.message) || '归因失败'
    }
  } catch (e) {
    repairMsg.value = e.message || '归因失败'
  }
}

async function acceptRisk(iss) {
  riskReason.value = ''
  repairDialog.value = { kind: 'risk', title: '接受阻断风险', description: `问题：${iss.code} · ${iss.title}`, issueIds: [iss.id] }
}

async function batchPreview() {
  const ids = stageIssues.value.map(i => i.id).filter(Boolean)
  if (!ids.length) return
  repairMsg.value = '批量预览中…'
  try {
    const { data } = await batchPreviewRepairs(ids)
    if (!data?.ok) throw new Error(data?.message || '批量预览失败')
    const plannedIds = data.issue_ids || ids
    repairDialog.value = {
      kind: 'preview',
      title: `批量修复计划（${plannedIds.length} 条）`,
      description: data.message || '以下仅为预览，不会修改文件。',
      plans: data.plans || [],
    }
  } catch (e) {
    repairMsg.value = e.message || '批量预览失败'
  }
}

async function batchRepair() {
  const ids = stageIssues.value.map(i => i.id).filter(Boolean)
  if (!ids.length) return
  try {
    const { data } = await batchPreviewRepairs(ids)
    if (!data?.ok) throw new Error(data?.message || '无法生成批量修复计划')
    const plannedIds = data.issue_ids || ids
    repairDialog.value = {
      kind: 'batch-execute',
      title: `确认批量最小修复（${plannedIds.length} 条）`,
      description: `${data.message || ''} 请确认下列动作。需要人工处理的动作不会自动修改文件。`.trim(),
      plans: data.plans || [],
      issueIds: plannedIds,
    }
  } catch (e) {
    repairMsg.value = e.message || '批量修复失败'
  }
}

function actionLabel(type) {
  return ({ rewrite_chapters: '定向改写章节', fix_coverage: '补齐评分覆盖', fix_compliance: '合规定向修复', rerun_stage: '重跑处理阶段', revalidate_gate: '重验门禁', upload_evidence: '补充证明材料', open_detail: '查看关联详情', accept_risk: '人工接受风险' })[type] || type || '未命名动作'
}

function isManualStep(step) {
  return ['upload_evidence', 'open_detail', 'accept_risk'].includes(step?.type)
}

function closeRepairDialog() {
  if (!repairBusy.value) repairDialog.value = null
}

async function confirmRepairDialog() {
  const dialog = repairDialog.value
  if (!dialog) return
  repairBusy.value = dialog.kind === 'risk' ? dialog.issueIds[0] : (dialog.kind === 'batch-execute' ? 'batch' : dialog.issueIds[0])
  repairMsg.value = dialog.kind === 'risk' ? '正在提交…' : '正在执行修复…'
  try {
    let response
    if (dialog.kind === 'risk') {
      response = await acceptIssueRisk(props.runId, dialog.issueIds[0], riskReason.value.trim())
    } else if (dialog.kind === 'batch-execute') {
      response = await batchExecuteRepairs(props.runId, dialog.issueIds)
    } else {
      response = await executeIssueRepair(props.runId, dialog.issueIds[0])
    }
    let data = response?.data || {}
    if (data.action) {
      const actionId = data.action.action_id || data.action.confirmation_id
      const confirmed = await confirmWorkspaceAction(props.runId, actionId)
      data = confirmed?.data || {}
    }
    if (!data?.ok && !data?.executed) throw new Error(data?.message || '操作失败')
    repairMsg.value = data.message || '操作完成'
    repairDialog.value = { kind: 'result', title: data.ok ? '操作完成' : '操作完成，但仍有待处理项', result: data }
    await refresh()
  } catch (e) {
    repairMsg.value = e.response?.data?.message || e.message || '操作失败'
    repairDialog.value = { kind: 'result', title: '操作未完成', result: { ok: false, message: repairMsg.value } }
  } finally {
    repairBusy.value = ''
  }
}

function parseMrCategory() {
  const c = String(props.command || '')
  if (c.startsWith('manual-review:')) {
    const cat = c.slice('manual-review:'.length).trim()
    if (mrCategories.some(x => x.key === cat)) return cat
  }
  return mrCategory.value || 'score_coverage'
}

function itemTitle(item) {
  return item.title || item.description || item.score_point_id || item.target_scope || item.item_id || '—'
}
function itemStatus(item) {
  return (item.override && item.override.status) || item.status || item.risk_level || 'pending'
}
function itemMeta(item) {
  const parts = []
  if (item.chapter_id) parts.push(`章节 ${item.chapter_id}`)
  if (item.problem_type) parts.push(item.problem_type)
  if (item.severity) parts.push(item.severity)
  if (item.risk_type) parts.push(item.risk_type)
  if (item.risk_level) parts.push(`风险 ${item.risk_level}`)
  return parts.join(' · ')
}
function noteOf(item) {
  const o = item.override || {}
  return o.operator_instruction || o.operator_note || ''
}

async function loadManualReview() {
  const cat = parseMrCategory()
  mrCategory.value = cat
  const [sumRes, itemsRes] = await Promise.all([
    fetchManualReviewSummary(),
    fetchManualReviewItems(cat),
  ])
  if (!sumRes.data?.ok) throw new Error(sumRes.data?.message || '加载人工复核摘要失败')
  if (!itemsRes.data?.ok) throw new Error(itemsRes.data?.message || '加载人工复核项失败')
  summary.value = sumRes.data.summary || {}
  mrItems.value = itemsRes.data.items || []
  notes.value = {}
  title.value = '人工复核'
  subtitle.value = mrCategories.find(x => x.key === cat)?.label || cat
  compliance.value = null
  detail.value = {}
}

async function switchMrCategory(cat, { keepMsg = false } = {}) {
  mrCategory.value = cat
  loading.value = true
  error.value = ''
  if (!keepMsg) actionMsg.value = ''
  try {
    const { data } = await fetchManualReviewItems(cat)
    if (!data?.ok) throw new Error(data?.message || '加载失败')
    mrItems.value = data.items || []
    notes.value = {}
    subtitle.value = mrCategories.find(x => x.key === cat)?.label || cat
    const sum = await fetchManualReviewSummary()
    if (sum.data?.ok) summary.value = sum.data.summary || {}
    hasLoadedOnce.value = true
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function submitMr(item, status) {
  busyId.value = item.item_id
  actionMsg.value = ''
  try {
    const note = (notes.value[item.item_id] ?? noteOf(item) ?? '').trim()
    const { data } = await updateManualReview(props.runId, mrCategory.value, {
      item_id: item.item_id,
      status,
      operator_note: note,
      operator_instruction: note,
      target_chapter_id: item.chapter_id || item.target_chapter_id || '',
    })
    if (!data?.ok || !data?.action?.confirmation_id) throw new Error(data?.message || '未生成确认操作')
    const confirmed = await confirmWorkspaceAction(props.runId, data.action.confirmation_id)
    if (!confirmed?.data?.ok) throw new Error(confirmed?.data?.message || '更新确认失败')
    actionMsg.value = `已更新 ${item.item_id} → ${status}`
    await switchMrCategory(mrCategory.value, { keepMsg: true })
  } catch (e) {
    actionMsg.value = e.message || '更新失败'
  } finally {
    busyId.value = ''
  }
}

async function refresh() {
  loading.value = true
  error.value = ''
  openId.value = null
  actionMsg.value = ''
  try {
    if (isMaterials.value) {
      title.value = '材料资格清单'
      subtitle.value = 'build-materials-checklist'
      detail.value = {}
      compliance.value = null
      summary.value = null
      mrItems.value = []
      hasLoadedOnce.value = true
    } else if (isManualReview.value) {
      await loadManualReview()
      hasLoadedOnce.value = true
    } else if (isCompliance.value) {
      const { data } = await fetchComplianceReport(props.runId)
      if (!data?.ok) throw new Error(data?.message || '加载失败')
      compliance.value = {
        exists: data.exists,
        blocking: data.blocking,
        need_manual_review: data.need_manual_review,
        max_severity: data.max_severity,
        counts: data.counts || {},
        items: data.items || [],
      }
      title.value = '专项合规检查'
      subtitle.value = data.exists ? (data.blocking ? '阻断中' : '已完成') : '尚未生成报告'
      if (!data.exists) error.value = data.message || '尚未生成合规报告'
      detail.value = {}
      summary.value = null
      mrItems.value = []
      hasLoadedOnce.value = true
    } else {
      compliance.value = null
      summary.value = null
      mrItems.value = []
      const cmd = workflowCommand.value || props.command
      const { data } = await fetchWorkflowStepDetail(cmd)
      if (!data?.ok) throw new Error(data?.message || '加载失败')
      detail.value = data
      title.value = data.step?.label || cmd
      subtitle.value = cmd
      hasLoadedOnce.value = true
    }
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
    if (!isManualReview.value) await loadIssues()
  }
}

watch(() => [props.runId, props.command], () => refresh(), { immediate: true })
</script>
