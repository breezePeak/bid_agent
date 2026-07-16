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

    <div class="sdv-body" v-if="loading">
      <div class="sdv-loading">加载中…</div>
    </div>
    <div class="sdv-body" v-else-if="error">
      <div class="sdv-error">{{ error }}</div>
    </div>
    <div class="sdv-body" v-else>
      <!-- Compliance focused layout -->
      <template v-if="isCompliance && compliance">
        <div class="sdv-banner" :class="{ blocking: compliance.blocking }">
          <div class="sdv-banner-title">
            {{ compliance.blocking ? '合规阻断 · 暂不可出正式稿' : (compliance.need_manual_review ? '合规待人工复核' : '合规检查结果') }}
          </div>
          <div class="sdv-banner-stats">
            失败 {{ counts.fail || 0 }} · 警告 {{ counts.warn || 0 }} · 通过 {{ counts.pass || 0 }}
            · 最高级别 {{ severityLabel(compliance.max_severity) }}
          </div>
          <div class="sdv-banner-help">
            fail=未通过；warn=需关注；fatal/critical=阻断交付。点击条目查看要求与建议。
          </div>
        </div>

        <div class="sdv-filters">
          <button v-for="f in filters" :key="f.key" class="sdv-filter" :class="{ on: filter === f.key }" @click="filter = f.key">
            {{ f.label }}
          </button>
        </div>

        <div class="sdv-list">
          <div
            v-for="item in filteredItems"
            :key="item.check_id || item.check_name"
            class="sdv-item"
            :class="['sev-' + (item.severity || 'info'), 'st-' + (item.status || '')]"
            @click="toggle(item.check_id)"
          >
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
              <div><b>处理：</b>{{ item.auto_fixable ? '可尝试系统定向改稿' : '需人工补充材料/正文响应' }}</div>
            </div>
          </div>
          <div v-if="!filteredItems.length" class="sdv-empty">当前筛选下没有条目</div>
        </div>
      </template>

      <!-- Generic step detail -->
      <template v-else>
        <div class="sdv-section" v-if="summaryRows.length">
          <h4>概要</h4>
          <div class="sdv-kv" v-for="row in summaryRows" :key="row.k">
            <span class="k">{{ row.k }}</span>
            <span class="v">{{ row.v }}</span>
          </div>
        </div>
        <div class="sdv-section" v-if="detail.timing && detail.timing.duration_label">
          <h4>耗时</h4>
          <div>{{ detail.timing.duration_label }}</div>
        </div>
        <div class="sdv-section" v-if="detail.produces && detail.produces.length">
          <h4>产物</h4>
          <div v-for="a in detail.produces" :key="a.path" class="sdv-art">
            <span>{{ a.path }}</span>
            <span>{{ a.exists ? '✓' : '✗' }}</span>
          </div>
        </div>
        <div class="sdv-section" v-if="detail.requires && detail.requires.length">
          <h4>依赖</h4>
          <div v-for="a in detail.requires" :key="a.path" class="sdv-art">
            <span>{{ a.path }}</span>
            <span>{{ a.exists ? '✓' : '✗' }}</span>
          </div>
        </div>
        <div class="sdv-section" v-if="rawExtra">
          <h4>详情</h4>
          <pre class="sdv-pre">{{ rawExtra }}</pre>
        </div>
        <div v-if="!summaryRows.length && !detail.produces?.length" class="sdv-empty">暂无更多详情</div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { fetchComplianceReport, fetchWorkflowStepDetail } from '../api'

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
const rawExtra = computed(() => {
  const d = detail.value?.details
  if (!d || typeof d !== 'object') return ''
  try { return JSON.stringify(d, null, 2).slice(0, 8000) } catch { return '' }
})

function severityLabel(sev) {
  const m = { fatal: '致命', critical: '严重', major: '重要', minor: '次要', info: '提示' }
  return m[sev] || sev || '—'
}
function formatValue(v) {
  if (v == null) return '—'
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch { return String(v) }
  }
  return String(v)
}
function toggle(id) {
  openId.value = openId.value === id ? null : id
}

async function refresh() {
  loading.value = true
  error.value = ''
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
  }
}

watch(() => [props.runId, props.command], () => refresh(), { immediate: true })
onMounted(refresh)
</script>
