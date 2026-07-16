<template>
  <div class="plan-list" :class="{ collapsed: planCollapsed }">
    <div class="plan-list-header" @click="planCollapsed = !planCollapsed">
      <span class="plan-list-arrow">{{ planCollapsed ? '▸' : '▾' }}</span>
      <span class="plan-list-title">执行计划</span>
      <span class="plan-list-progress">{{ doneCount }}/{{ steps.length }}</span>
      <span class="plan-list-track">
        <span class="plan-list-fill" :style="{ width: percent + '%' }"></span>
      </span>
      <div class="plan-list-actions" @click.stop>
        <button v-if="running" class="btn btn-sm" @click="$emit('pause')">暂停</button>
      </div>
    </div>
    <div class="plan-list-summary">
      <div class="plan-summary-item active" v-if="activeStep">
        <span class="plan-summary-kicker">{{ activeLabel }}</span>
        <strong>{{ activeStep.label }}</strong>
        <small>{{ activeStep.message || activeMeta }}</small>
      </div>
      <div class="plan-summary-item" v-if="nextStep">
        <span class="plan-summary-kicker">下一步</span>
        <strong>{{ nextStep.label }}</strong>
        <small>{{ nextStep.message || '等待执行' }}</small>
      </div>
      <div class="plan-summary-recovery" v-if="recovery">
        正在尝试修复：{{ recovery.reason || '分析失败原因' }} · {{ recovery.action || '自动重试' }}（{{ recovery.attempt || 0 }}/{{ recovery.max_attempts || 2 }}）
      </div>
      <div
        v-if="compliance && compliance.exists"
        class="plan-summary-compliance"
        :class="{ blocking: compliance.blocking, warn: !compliance.blocking && compliance.need_manual_review }"
        @click="$emit('preview-compliance')"
      >
        <span class="plan-summary-kicker">{{ compliance.blocking ? '合规阻断' : (compliance.need_manual_review ? '合规待核' : '合规') }}</span>
        <strong>
          fail {{ (compliance.counts && compliance.counts.fail) || 0 }}
          · warn {{ (compliance.counts && compliance.counts.warn) || 0 }}
          · {{ compliance.max_severity || 'info' }}
        </strong>
        <small>{{ complianceTopHint }}</small>
      </div>
    </div>
    <div class="plan-list-body" v-show="!planCollapsed" ref="bodyRef">
      <div
        v-for="(step, idx) in steps"
        :key="step.command"
        class="plan-row"
        :class="{ done: step.status === 'done', running: step.status === 'running', recovering: step.status === 'recovering' || step.status === 'retrying', error: step.status === 'error' }"
      >
        <span class="plan-row-check">
          <span v-if="step.status === 'done'">&#x2713;</span>
          <span v-else-if="step.status === 'running' || step.status === 'recovering' || step.status === 'retrying'">
            <svg class="spin" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" style="display: block;">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-opacity="0.25" />
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-linecap="round" />
            </svg>
          </span>
          <span v-else class="plan-row-num">{{ idx + 1 }}</span>
        </span>
        <span class="plan-row-label" :class="{ strike: step.status === 'done' }">{{ step.label }}</span>
        <span class="plan-row-meta">
          <span v-if="step.status === 'done' && step.durationLabel" class="plan-row-duration">{{ step.durationLabel }}</span>
          <span v-else-if="step.status === 'running'" class="plan-row-running"><span class="plan-row-pulse"></span>执行中{{ step.durationLabel ? ' · ' + step.durationLabel : '' }}</span>
          <span v-else-if="step.status === 'recovering'" class="plan-row-running">修复中</span>
          <span v-else-if="step.status === 'retrying'" class="plan-row-running">重试中</span>
          <span v-else-if="step.status === 'error'" class="plan-row-err">失败</span>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
  executing: { type: Boolean, default: false },
  recovery: { type: Object, default: null },
  compliance: { type: Object, default: null },
  forceExpand: { type: Boolean, default: false },
})

defineEmits(['pause', 'preview-compliance'])

const complianceTopHint = computed(() => {
  const items = props.compliance?.failed_items
  if (Array.isArray(items) && items.length) {
    const first = items[0]
    return `${first.check_id || ''}${first.check_name ? ' ' + first.check_name : ''}`.trim() || '点击查看详情'
  }
  if (props.compliance?.blocking) return '存在 fatal/critical 失败，交付已阻断'
  if (props.compliance?.need_manual_review) return '需人工复核签章/资格/废标条款等'
  return '专项合规检查已完成'
})

const planCollapsed = ref(true)
const bodyRef = ref(null)
const doneCount = computed(() => props.steps.filter(s => s.status === 'done').length)
const percent = computed(() => props.steps.length ? Math.round((doneCount.value / props.steps.length) * 100) : 0)
const activeStep = computed(() => props.steps.find(s => ['running', 'recovering', 'retrying', 'error'].includes(s.status)) || props.steps.find(s => s.status !== 'done') || props.steps[props.steps.length - 1])
const nextStep = computed(() => props.steps.find(s => s.status !== 'done' && s !== activeStep.value))
const activeLabel = computed(() => {
  if (!activeStep.value) return '当前'
  if (activeStep.value.status === 'error') return '失败'
  if (activeStep.value.status === 'recovering') return '修复中'
  if (activeStep.value.status === 'retrying') return '重试中'
  if (activeStep.value.status === 'done') return '已完成'
  return '当前'
})
const activeMeta = computed(() => {
  if (!activeStep.value) return ''
  if (activeStep.value.status === 'running') return '执行中'
  if (activeStep.value.status === 'recovering') return '正在自主修复'
  if (activeStep.value.status === 'retrying') return '正在自动重试'
  if (activeStep.value.status === 'error') return '等待处理'
  return '等待执行'
})

watch(() => props.forceExpand, (v) => {
  if (v) planCollapsed.value = false
})
watch(() => props.running || props.executing, (v) => {
  if (v) planCollapsed.value = false
})
watch(() => props.steps.find(s => s.status === 'running'), (s) => {

  if (s && bodyRef.value) {
    nextTick(() => {
      const el = bodyRef.value.querySelector('.plan-row.running')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  }
})
</script>
