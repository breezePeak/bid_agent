<template>
  <div class="plan-bar">
    <div class="plan-bar-header">
      <span class="plan-bar-title">执行计划</span>
      <span class="plan-bar-progress">{{ doneCount }}/{{ steps.length }}</span>
      <span class="plan-bar-track">
        <span class="plan-bar-fill" :style="{ width: percent + '%' }"></span>
      </span>
    </div>
    <div class="plan-bar-steps" ref="stepsRef">
      <button
        v-for="(step, idx) in steps"
        :key="step.command"
        class="plan-badge"
        :class="{ done: step.status === 'done', running: step.status === 'running', error: step.status === 'error' }"
        :title="step.label + (step.status === 'done' ? ' - 已完成' : step.status === 'running' ? ' - 执行中' : ' - 等待')"
        @click="step.status === 'done' && $emit('preview', step.command)"
      >
        <span class="plan-badge-dot">
          <span v-if="step.status === 'done'">✓</span>
          <span v-else-if="step.status === 'running'" class="spin">●</span>
          <span v-else class="plan-badge-num">{{ idx + 1 }}</span>
        </span>
        <span class="plan-badge-label">{{ step.label }}</span>
      </button>
    </div>
    <div class="plan-bar-actions">
      <button v-if="!running && doneCount < steps.length" class="btn btn-primary btn-sm" :disabled="executing" @click="$emit('start')">
        {{ executing ? '执行中...' : '开始执行' }}
      </button>
      <button v-if="running" class="btn btn-sm" @click="$emit('pause')">暂停</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
  executing: { type: Boolean, default: false },
})

defineEmits(['start', 'pause', 'preview'])

const stepsRef = ref(null)
const doneCount = computed(() => props.steps.filter(s => s.status === 'done').length)
const percent = computed(() => props.steps.length ? Math.round((doneCount.value / props.steps.length) * 100) : 0)

watch(() => props.steps.find(s => s.status === 'running'), (s) => {
  if (s && stepsRef.value) {
    nextTick(() => {
      const el = stepsRef.value.querySelector('.plan-badge.running')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
    })
  }
})
</script>
