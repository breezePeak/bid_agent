<template>
  <div class="issues-panel">
    <div class="ip-tabs">
      <button class="ip-tab" :class="{ on: tab === 'issues' }" @click="tab = 'issues'">问题</button>
      <button class="ip-tab" :class="{ on: tab === 'files' }" @click="tab = 'files'">文件</button>
    </div>

    <div v-if="tab === 'files'" class="ip-files">
      <FileExplorer :run-id="runId" @preview-file="$emit('preview-file', $event)" />
    </div>

    <div v-else class="ip-issues">
      <div class="ip-header">
        <div class="ip-title-row">
          <strong>问题与合规</strong>
          <button class="btn btn-sm" @click="refresh" :disabled="loading">刷新</button>
        </div>
        <div v-if="report.exists" class="ip-banner" :class="{ blocking: report.blocking, warn: !report.blocking && report.need_manual_review }">
          <div class="ip-banner-kicker">
            {{ report.blocking ? '合规阻断 · 暂不可出正式稿' : (report.need_manual_review ? '合规待人工复核' : '合规状态正常') }}
          </div>
          <div class="ip-banner-stats">
            失败 {{ counts.fail || 0 }} · 警告 {{ counts.warn || 0 }} · 通过 {{ counts.pass || 0 }}
            · 最高 {{ severityLabel(report.max_severity) }}
          </div>
        </div>
        <div v-else class="ip-empty-soft">{{ emptyMsg }}</div>
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
          <div class="ip-detail" v-if="selected === item.check_id">
            <div v-if="item.suggestion"><b>建议：</b>{{ item.suggestion }}</div>
            <div v-if="item.check_type"><b>类型：</b>{{ item.check_type }}</div>
            <div><b>处理：</b>{{ item.auto_fixable ? '可尝试系统定向改稿' : '需人工补充材料/正文响应' }}</div>
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
import { fetchComplianceReport } from '../api'

const props = defineProps({
  runId: { type: String, required: true },
  /** when parent asks to focus compliance */
  focus: { type: String, default: '' },
})
defineEmits(['preview-file'])

const tab = ref('issues')
const loading = ref(false)
const filter = ref('fail')
const selected = ref(null)
const report = ref({ exists: false, blocking: false, items: [], counts: {}, max_severity: '' })
const emptyMsg = ref('暂无合规报告。跑完 compliance-check 后会显示失败/警告明细。')

const counts = computed(() => report.value.counts || {})
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

function severityLabel(sev) {
  const m = { fatal: '致命', critical: '严重', major: '重要', minor: '次要', info: '提示' }
  return m[sev] || sev || '—'
}

async function refresh() {
  loading.value = true
  try {
    const { data } = await fetchComplianceReport()
    if (data && data.ok) {
      report.value = {
        exists: !!data.exists,
        blocking: !!data.blocking,
        need_manual_review: !!data.need_manual_review,
        max_severity: data.max_severity || '',
        counts: data.counts || {},
        items: data.items || [],
      }
      if (!data.exists) emptyMsg.value = data.message || emptyMsg.value
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

watch(() => props.runId, () => refresh())
watch(() => props.focus, (v) => {
  if (v === 'compliance' || v === 'compliance-check' || v === 'issues') {
    tab.value = 'issues'
    filter.value = 'fail'
    refresh()
  } else if (v === 'files') {
    tab.value = 'files'
  }
})

onMounted(refresh)

defineExpose({ refresh, showIssues: () => { tab.value = 'issues'; refresh() } })
</script>
