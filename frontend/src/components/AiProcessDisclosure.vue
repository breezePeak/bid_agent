<template>
  <details class="ai-process-disclosure" :class="`is-${normalizedStatus}`">
    <summary>
      <span class="ai-process-indicator" aria-hidden="true">
        <svg v-if="normalizedStatus === 'processing'" class="ai-process-spinner" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="7" />
        </svg>
        <svg v-else-if="normalizedStatus === 'failed'" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="7" />
          <path d="M10 6.5v4.25M10 13.5h.01" />
        </svg>
        <svg v-else-if="normalizedStatus === 'waiting'" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="7" />
          <path d="M10 6.5v4l2.5 1.5" />
        </svg>
        <svg v-else viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="7" />
          <path d="m6.8 10 2 2 4.4-4.5" />
        </svg>
      </span>
      <span class="ai-process-label" aria-live="polite">{{ summaryLabel }}</span>
      <svg class="ai-process-chevron" aria-hidden="true" viewBox="0 0 20 20">
        <path d="m7.5 5 5 5-5 5" />
      </svg>
    </summary>
    <div class="ai-process-detail">
      <slot>
        <p>{{ detailText || defaultDetailText }}</p>
      </slot>
    </div>
  </details>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, default: 'completed' },
  seconds: { type: Number, default: 0 },
  detailText: { type: String, default: '' },
})

const normalizedStatus = computed(() => (
  ['processing', 'completed', 'failed', 'waiting'].includes(props.status)
    ? props.status
    : 'completed'
))

const durationLabel = computed(() => {
  const total = Math.max(0, Math.floor(Number(props.seconds) || 0))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  if (minutes) return `${minutes} 分 ${seconds} 秒`
  return `${seconds} 秒`
})

const summaryLabel = computed(() => ({
  processing: `正在处理 · 已用 ${durationLabel.value}`,
  completed: `已处理 · 耗时 ${durationLabel.value}`,
  failed: `处理失败 · 耗时 ${durationLabel.value}`,
  waiting: `等待确认 · 已处理 ${durationLabel.value}`,
}[normalizedStatus.value]))

const defaultDetailText = computed(() => ({
  processing: '请求已提交，正在等待当前处理步骤返回。',
  completed: '处理已完成，结果已返回到当前对话。',
  failed: '处理未完成，请根据错误提示重试或补充材料。',
  waiting: '自动处理已暂停，正在等待您的确认。',
}[normalizedStatus.value]))
</script>

<style scoped>
.ai-process-disclosure {
  width: 100%;
  margin-top: 10px;
  color: #52627a;
}

.ai-process-disclosure > summary {
  min-height: 44px;
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) 18px;
  gap: 8px;
  align-items: center;
  padding: 7px 10px;
  border: 1px solid #dbe5f2;
  border-radius: 10px;
  background: #f7faff;
  color: #40516a;
  cursor: pointer;
  list-style: none;
  font-size: 12px;
  font-weight: 700;
  transition: border-color .2s ease, background-color .2s ease, color .2s ease;
}

.ai-process-disclosure > summary::-webkit-details-marker { display: none; }
.ai-process-disclosure > summary:hover { border-color: #b8c9df; background: #f1f6fc; color: #263a56; }
.ai-process-disclosure > summary:focus-visible { outline: 3px solid rgba(59, 130, 246, .22); outline-offset: 2px; }

.ai-process-indicator,
.ai-process-indicator svg,
.ai-process-chevron {
  width: 18px;
  height: 18px;
}

.ai-process-indicator { display: inline-grid; place-items: center; color: #2f6fed; }
.ai-process-indicator svg,
.ai-process-chevron {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.is-completed .ai-process-indicator { color: #16834b; }
.is-failed .ai-process-indicator { color: #c43d3d; }
.is-waiting .ai-process-indicator { color: #9a6700; }
.ai-process-chevron { color: #8291a7; transition: transform .2s ease; }
.ai-process-disclosure[open] .ai-process-chevron { transform: rotate(90deg); }

.ai-process-spinner circle {
  stroke-dasharray: 28 16;
  transform-origin: center;
  animation: ai-process-spin .9s linear infinite;
}

.ai-process-detail {
  margin: 7px 4px 0;
  padding: 10px 12px;
  border-left: 2px solid #dbe5f2;
  color: #5f6f86;
  background: #fbfdff;
  font-size: 12px;
  line-height: 1.6;
}

.ai-process-detail :deep(p) { margin: 0; }

@keyframes ai-process-spin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .ai-process-disclosure > summary,
  .ai-process-chevron,
  .ai-process-spinner circle {
    animation: none;
    transition: none;
  }
}
</style>
