<template>
  <div class="issues-panel">
    <div class="ip-tabs">
      <button class="ip-tab" :class="{ on: tab === 'office' }" @click="tab = 'office'; goalPanelRef?.refresh?.()">办公室</button>
      <button class="ip-tab" :class="{ on: tab === 'issues' }" @click="tab = 'issues'">问题</button>
      <button
        class="ip-tab"
        :class="{ on: tab === 'materials', alert: deferredCount > 0 }"
        @click="tab = 'materials'; materialsRef?.refresh?.()"
      >
        材料
        <span v-if="deferredCount > 0" class="ip-badge-alert" :title="`待补 ${deferredCount} 条材料`">{{ deferredCount }}</span>
      </button>
      <button class="ip-tab" :class="{ on: tab === 'logs' }" @click="tab = 'logs'">日志</button>
      <button class="ip-tab" :class="{ on: tab === 'files' }" @click="tab = 'files'">文件</button>
    </div>

    <div v-if="deferredCount > 0 && tab !== 'materials'" class="ip-materials-alert" @click="tab = 'materials'; materialsRef?.refresh?.()">
      <span class="ip-alert-dot"></span>
      <strong>待补材料 {{ deferredCount }} 条</strong>
      <span>请补充公司资料或在材料清单中处理</span>
    </div>

    <div v-show="tab === 'office'" class="ip-office">
      <AgentGoalPanel ref="goalPanelRef" :run-id="runId" />
    </div>

    <div v-if="tab === 'files'" class="ip-files">
      <FileExplorer :run-id="runId" @preview-file="$emit('preview-file', $event)" />
    </div>

    <div v-else-if="tab === 'materials'" class="ip-materials">
      <MaterialsChecklistPanel ref="materialsRef" :run-id="runId" @status="onMaterialsPanelStatus" />
    </div>

    <div v-else-if="tab === 'logs'" class="ip-logs">
      <div class="ip-header">
        <div class="ip-title-row">
          <strong>流水线日志</strong>
          <button class="btn btn-sm" @click="clearLogs">清空</button>
        </div>
        <div class="ip-empty-soft">阶段执行细节在此查看，聊天里只保留关键节点。</div>
      </div>
      <div class="ip-log-list" v-if="displayLogs.length">
        <div v-for="(row, i) in displayLogs" :key="i" class="ip-log-line">
          <span v-if="row.stage" class="ip-log-stage">{{ row.stage }}</span>
          <span>{{ row.line }}</span>
        </div>
      </div>
      <div v-else class="ip-empty-soft" style="padding:12px">暂无日志。启动流水线后会实时汇入。</div>
    </div>

    <div v-else-if="tab === 'issues'" class="ip-issues">
      <div class="ip-header">
        <div class="ip-title-row">
          <strong>问题与合规</strong>
          <button class="btn btn-sm" @click="refresh" :disabled="loading">刷新</button>
        </div>
        <div v-if="report.exists" class="ip-banner" :class="{ blocking: report.blocking, warn: !report.blocking && report.need_manual_review }">
          <div class="ip-banner-kicker">
            {{ report.source === 'issues'
              ? '最小修复失败 · 问题仍未关闭'
              : (report.blocking ? '合规阻断 · 暂不可出正式稿' : (report.need_manual_review ? '合规待人工复核' : '合规状态正常')) }}
          </div>
          <div class="ip-banner-stats">
            <template v-if="report.source === 'issues'">失败 {{ counts.fail || 0 }} · 警告 {{ counts.warn || 0 }}</template>
            <template v-else>失败 {{ counts.fail || 0 }} · 警告 {{ counts.warn || 0 }} · 通过 {{ counts.pass || 0 }} · 最高 {{ severityLabel(report.max_severity) }}</template>
          </div>
        </div>
        <div v-else class="ip-empty-soft">{{ emptyMsg }}</div>
        <div class="ip-empty-soft" aria-live="polite">
          V2 门禁证据：
          <span v-for="(gate, index) in qualityGateRows" :key="gate.command" :title="gate.createdAt || '尚未重验'">
            <span v-if="index"> · </span>{{ gate.label }} {{ gate.verdictLabel }}
          </span>
        </div>
      </div>

      <div class="ip-filters" v-if="report.exists">
        <button
          v-for="f in filters"
          :key="f.key"
          class="ip-filter"
          :class="{ on: filter === f.key }"
          @click="filter = f.key"
        >{{ f.label }}</button>
      </div>

      <div class="ip-list" v-if="filteredItems.length">
        <div
          v-for="item in filteredItems"
          :key="item.check_id || item.check_name"
          class="ip-item"
          :class="['sev-' + (item.severity || 'info'), 'st-' + (item.status || '')]"
          @click="selected = selected === item.check_id ? null : item.check_id"
        >
          <div class="ip-item-head">
            <span class="ip-id">{{ item.check_id || '—' }}</span>
            <span class="ip-sev">{{ severityLabel(item.severity) }}</span>
            <span class="ip-st">{{ item.status === 'fail' ? '失败' : (item.status === 'warn' ? '警告' : item.status) }}</span>
          </div>
            <div class="ip-name">{{ item.check_name || item.check_type || '检查项' }}</div>
            <div class="ip-req" v-if="item.requirement">{{ item.requirement }}</div>
            <div class="ip-detail-summary" v-if="item.failure_reason"><b>最近修复失败：</b>{{ item.failure_reason }}</div>
            <div class="ip-detail" v-if="selected === item.check_id">
              <div v-if="item.failure_reason"><b>失败原因：</b>{{ item.failure_reason }}</div>
              <div v-if="item.suggestion"><b>建议：</b>{{ item.suggestion }}</div>
              <div v-if="item.check_type"><b>类型：</b>{{ item.check_type }}</div>
              <div><b>处理：</b>{{ item.auto_fixable ? '可尝试系统定向改稿' : '需人工补充材料/正文响应' }}</div>
              <button v-if="item.failure_reason && item.issue_id" class="btn btn-sm" :disabled="acceptingIssue === item.issue_id" @click.stop="acceptRisk(item)">
                {{ acceptingIssue === item.issue_id ? '提交中...' : '接受风险并解除该项阻断' }}
              </button>
              <div v-if="item.need_manual_review" class="ip-man">需人工复核</div>
          </div>
        </div>
      </div>
      <div v-else-if="report.exists" class="ip-empty-soft">当前筛选下没有条目</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import FileExplorer from './FileExplorer.vue'
import MaterialsChecklistPanel from './MaterialsChecklistPanel.vue'
import AgentGoalPanel from './AgentGoalPanel.vue'
import { acceptIssueRisk, confirmWorkspaceAction, fetchComplianceReport, fetchIssues, fetchMaterialsChecklist } from '../api'
import { useWorkspaceRuntime } from '../composables/useWorkspaceRuntime'

const props = defineProps({
  runId: { type: String, required: true },
  focus: { type: String, default: '' },
  pipelineLogs: { type: Array, default: () => [] },
})
const emit = defineEmits(['preview-file', 'open-chapter', 'materials-status'])

const tab = ref('office')
const loading = ref(false)
const filter = ref('fail')
const selected = ref(null)
const materialsRef = ref(null)
const goalPanelRef = ref(null)
const localLogs = ref([])
const deferredCountLocal = ref(0)
const materialsExists = ref(false)

// Single status bus: materials deferred badge follows the V2 Snapshot.
const { status: runtimeStatus, materialsDeferred, quality } = useWorkspaceRuntime({
  runId: computed(() => props.runId),
})
const deferredCount = computed(() => {
  const fromBus = Number(materialsDeferred.value || 0) || 0
  return runtimeStatus.value ? fromBus : (deferredCountLocal.value || 0)
})
const report = ref({ exists: false, blocking: false, items: [], counts: {}, max_severity: '' })
const emptyMsg = ref('暂无合规报告。跑完 compliance-check 后会显示失败/警告明细。')
const acceptingIssue = ref('')
let materialsBadgeRefresh = null

const counts = computed(() => report.value.counts || {})
const qualityEvaluations = computed(() => Array.isArray(quality.value?.latest_gate_evaluations)
  ? quality.value.latest_gate_evaluations
  : [])
const qualityGateRows = computed(() => {
  const byCommand = new Map(qualityEvaluations.value
    .filter(item => item && typeof item === 'object')
    .map(item => [String(item.command || ''), item]))
  return [
    ['global-review', '全文审核'],
    ['compliance-check', '专项合规'],
  ].map(([command, label]) => {
    const evaluation = byCommand.get(command)
    const verdict = String(evaluation?.verdict || '')
    return {
      command,
      label,
      createdAt: String(evaluation?.created_at || ''),
      verdictLabel: ({ pass: '已通过', block: '被阻断', error: '异常' })[verdict] || '待重验',
    }
  })
})
const filters = [
  { key: 'fail', label: '失败' },
  { key: 'warn', label: '警告' },
  { key: 'fatal', label: '致命' },
  { key: 'critical', label: '严重' },
  { key: 'all', label: '全部' },
]

const filteredItems = computed(() => {
  const items = Array.isArray(report.value.items) ? report.value.items : []
  if (filter.value === 'all') return items.filter(i => i.status === 'fail' || i.status === 'warn')
  if (filter.value === 'fail' || filter.value === 'warn') return items.filter(i => i.status === filter.value)
  return items.filter(i => i.severity === filter.value)
})

const displayLogs = computed(() => {
  const fromParent = Array.isArray(props.pipelineLogs) ? props.pipelineLogs : []
  return fromParent.length ? fromParent.slice().reverse() : localLogs.value.slice().reverse()
})

function severityLabel(sev) {
  const m = { fatal: '致命', critical: '严重', major: '重要', minor: '次要', info: '提示' }
  return m[sev] || sev || '—'
}

function clearLogs() {
  localLogs.value = []
}

function publishMaterialsStatus(payload) {
  const n = Number(payload?.deferred || 0) || 0
  deferredCountLocal.value = n
  materialsExists.value = !!payload?.exists
  emit('materials-status', {
    exists: !!payload?.exists,
    deferred: n,
    total: Number(payload?.total || 0) || 0,
    ready: Number(payload?.ready || 0) || 0,
    waived: Number(payload?.waived || 0) || 0,
    items: Array.isArray(payload?.items) ? payload.items : [],
  })
}

function onMaterialsPanelStatus(payload) {
  publishMaterialsStatus(payload || {})
}

async function refreshMaterialsBadge() {
  if (materialsBadgeRefresh) return materialsBadgeRefresh
  materialsBadgeRefresh = (async () => {
    try {
      const { data } = await fetchMaterialsChecklist(props.runId)
      if (!data?.ok) return
      const summary = data.summary || data.checklist?.summary || {}
      publishMaterialsStatus({
        exists: !!data.exists,
        deferred: Number(summary.deferred || 0) || 0,
        total: Number(summary.total || 0) || 0,
        ready: Number(summary.ready || 0) || 0,
        waived: Number(summary.waived || 0) || 0,
        items: Array.isArray(data.items) ? data.items : [],
      })
    } catch (e) { /* status refresh is best-effort */ }
  })().finally(() => { materialsBadgeRefresh = null })
  return materialsBadgeRefresh
}

async function refresh() {
  loading.value = true
  try {
    const [{ data }, { data: issuesData }] = await Promise.all([
      fetchComplianceReport(props.runId),
      fetchIssues(props.runId, 'open'),
    ])
    const issueRows = (Array.isArray(issuesData?.issues) ? issuesData.issues : []).map((issue) => {
      const evidence = issue?.evidence && typeof issue.evidence === 'object' ? issue.evidence : {}
      const lastFailure = evidence.last_repair_failure && typeof evidence.last_repair_failure === 'object'
        ? evidence.last_repair_failure : {}
      const failureReason = String(issue?.failure_reason || lastFailure.reason || '').trim()
      return {
        check_id: String(issue?.id || ''),
        issue_id: String(issue?.id || ''),
        check_name: issue?.title || issue?.code || '质量问题',
        check_type: issue?.stage_id || issue?.code || '',
        requirement: issue?.detail || '',
        suggestion: issue?.suggestion || issue?.recommended_action || '',
        failure_reason: failureReason,
        severity: issue?.severity === 'block' ? 'critical' : (issue?.severity || 'major'),
        status: failureReason || issue?.severity === 'block' ? 'fail' : 'warn',
        auto_fixable: issue?.auto_fixable !== false,
        need_manual_review: !!issue?.need_manual_review,
      }
    })
    if (data && data.ok) {
      report.value = {
        exists: !!data.exists || issueRows.length > 0,
        source: data.exists ? 'compliance' : (issueRows.length ? 'issues' : ''),
        blocking: !!data.blocking || issueRows.some(item => item.status === 'fail'),
        need_manual_review: !!data.need_manual_review,
        max_severity: data.max_severity || '',
        counts: data.exists
          ? (data.counts || {})
          : {
              fail: issueRows.filter(item => item.status === 'fail').length,
              warn: issueRows.filter(item => item.status === 'warn').length,
            },
        items: data.exists ? (data.items || []) : issueRows,
      }
      if (!data.exists) emptyMsg.value = issueRows.length
        ? '以下为 V2 问题单中的未关闭问题；展开可查看最近一次修复失败原因。'
        : (data.message || emptyMsg.value)
      if (data.blocking) {
        tab.value = 'issues'
        filter.value = 'fail'
      }
    }
  } catch (e) {
    emptyMsg.value = '加载合规报告失败'
  } finally {
    loading.value = false
  }
}

async function acceptRisk(item) {
  const reason = window.prompt('接受风险会解除该问题的流程阻断，但失败原因、原始证据和你的理由会永久保留在风险登记中。\n\n请填写接受原因（至少 8 个字符）：', '')
  if (reason === null) return
  if (reason.trim().replace(/\s/g, '').length < 8) {
    emptyMsg.value = '接受风险原因至少需要 8 个有效字符。'
    return
  }
  if (!window.confirm('确认接受此风险并允许流程继续？该决定会被审计保留。')) return
  acceptingIssue.value = item.issue_id
  try {
    let { data } = await acceptIssueRisk(props.runId, item.issue_id, reason.trim())
    const actionId = data?.action?.action_id || data?.action?.confirmation_id
    if (actionId) ({ data } = await confirmWorkspaceAction(props.runId, actionId))
    if (!data?.ok && !data?.accepted) throw new Error(data?.message || '接受风险失败')
    emptyMsg.value = '风险已接受并记录：失败原因、原始证据和接受理由均已保留。现在可继续下一步流程。'
    await refresh()
  } catch (e) {
    emptyMsg.value = e.response?.data?.message || e.message || '接受风险失败'
  } finally { acceptingIssue.value = '' }
}

watch(() => props.runId, () => {
  localLogs.value = []
  deferredCountLocal.value = 0
  materialsExists.value = false
  tab.value = 'office'
  refresh()
})
watch(() => props.focus, (v) => {
  if (v === 'goal' || v === 'office' || v === 'agent') {
    tab.value = 'office'
    goalPanelRef.value?.refresh?.()
  } else if (v === 'compliance' || v === 'compliance-check' || v === 'issues') {
    tab.value = 'issues'
    filter.value = 'fail'
    refresh()
  } else if (v === 'materials' || v === 'materials-checklist' || v === 'build-materials-checklist') {
    tab.value = 'materials'
    materialsRef.value?.refresh?.()
    refreshMaterialsBadge()
  } else if (v === 'logs' || v === 'pipeline-logs') {
    tab.value = 'logs'
  } else if (v === 'files') {
    tab.value = 'files'
  }
})

onMounted(() => {
  refresh()
})

defineExpose({
  refresh,
  refreshMaterialsBadge,
  showOffice: () => { tab.value = 'office'; goalPanelRef.value?.refresh?.() },
  showIssues: () => { tab.value = 'issues'; refresh() },
  showMaterials: () => { tab.value = 'materials'; materialsRef.value?.refresh?.(); refreshMaterialsBadge() },
  showLogs: () => { tab.value = 'logs' },
  refreshGoal: () => goalPanelRef.value?.refresh?.(),
  pushLog: (line, stage = '') => {
    localLogs.value = [...localLogs.value.slice(-400), { line, stage, at: new Date().toISOString() }]
  },
})
</script>
