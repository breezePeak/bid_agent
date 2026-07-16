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

    <div class="sdv-body" v-if="loading"><div class="sdv-loading">加载中…</div></div>
    <div class="sdv-body" v-else-if="error"><div class="sdv-error">{{ error }}</div></div>
    <div class="sdv-body" v-else>
      <div class="sdv-section" v-if="stageIssues.length">
        <h4>阻断/待处理问题（{{ stageIssues.length }}）</h4>
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
          </div>
          <div class="sdv-detail" v-if="iss._plan">
            <div><b>计划：</b>{{ iss._plan.summary }}</div>
            <div v-for="(st, si) in (iss._plan.steps || [])" :key="si">• {{ st.label || st.type }}</div>
            <div v-if="(iss._plan.revalidate || []).length"><b>重验：</b>{{ (iss._plan.revalidate || []).join(' → ') }}</div>
          </div>
        </div>
      </div>
      <!-- Compliance -->
      <template v-if="isCompliance && compliance">
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

        <div v-if="!hasAnyContent" class="sdv-empty">该节点暂无可展示成果，可能尚未执行完成。</div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { fetchComplianceReport, fetchWorkflowStepDetail, fetchIssues, previewIssueRepair, executeIssueRepair } from '../api'

const props = defineProps({
  runId: { type: String, required: true },
  command: { type: String, required: true },
})
defineEmits(['close'])

const loading = ref(false)
const error = ref('')
const title = ref('')
const subtitle = ref('')
const detail = ref({})
const compliance = ref(null)
const filter = ref('fail')
const openId = ref(null)
const issueList = ref([])
const repairBusy = ref('')
const repairMsg = ref('')


const isCompliance = computed(() => props.command === 'compliance-check' || props.command === 'compliance')
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
  const cmd = props.command
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
      repairMsg.value = data.summary || '已生成修复计划'
    } else {
      repairMsg.value = (data && data.message) || '预览失败'
    }
  } catch (e) {
    repairMsg.value = e.message || '预览失败'
  }
}

async function runRepair(iss) {
  if (!confirm('确认按最小修复计划执行？可能重写相关章节并重验门禁。')) return
  repairBusy.value = iss.id
  repairMsg.value = '正在修复…'
  try {
    if (!iss._plan) await previewRepair(iss)
    const { data } = await executeIssueRepair(iss.id, { confirm: true })
    repairMsg.value = (data && data.message) || (data && data.ok ? '完成' : '失败')
    await refresh()
  } catch (e) {
    repairMsg.value = e.message || '修复失败'
  } finally {
    repairBusy.value = ''
  }
}

async function refresh() {
  loading.value = true
  error.value = ''
  openId.value = null
  try {
    if (isCompliance.value) {
      const { data } = await fetchComplianceReport()
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
    } else {
      compliance.value = null
      const { data } = await fetchWorkflowStepDetail(props.command)
      if (!data?.ok) throw new Error(data?.message || '加载失败')
      detail.value = data
      title.value = data.step?.label || props.command
      subtitle.value = props.command
    }
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
    await loadIssues()
  }
}

watch(() => [props.runId, props.command], () => refresh(), { immediate: true })
</script>
