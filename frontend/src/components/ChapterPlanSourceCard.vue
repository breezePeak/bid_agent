<template>
  <button
    type="button"
    class="plan-source-card"
    :aria-label="`查看来源详情：${source.title}`"
    @click="$emit('inspect', source)"
  >
    <span class="source-card-topline">
      <span class="source-type">{{ sourceTypeLabel }}</span>
      <span class="source-status">只读</span>
    </span>
    <strong>{{ source.title }}</strong>
    <span v-if="source.preview" class="source-preview">{{ source.preview }}</span>
    <span class="source-location">{{ source.snapshot_ref }}</span>
    <span class="source-hash"># {{ source.content_hash.slice(0, 10) }}</span>
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  source: { type: Object, required: true },
})

defineEmits(['inspect'])

const labels = {
  TENDER_REQUIREMENT: '招标要求',
  SCORE_OBLIGATION: '评分义务',
  GLOBAL_PROJECT_FACT: '项目事实',
  CHAPTER_CONTEXT_ITEM: '章节资料',
  USER_MATERIAL_BLOCK: '用户材料',
  SIBLING_REFERENCE: '他章引用',
  WEB_EVIDENCE: '网页证据',
}

const sourceTypeLabel = computed(() => labels[props.source.source_type] || props.source.source_type)
</script>

<style scoped>
.plan-source-card {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-height: 116px;
  padding: 12px;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: #fff;
  color: #0f172a;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%);
}
.plan-source-card:hover { border-color: #60a5fa; background: #f8fbff; }
.plan-source-card:focus-visible { outline: 3px solid rgb(37 99 235 / 28%); outline-offset: 2px; }
.source-card-topline { display: flex; justify-content: space-between; gap: 8px; }
.source-type, .source-status { font-size: 11px; font-weight: 700; }
.source-type { color: #1d4ed8; }
.source-status { color: #64748b; }
.plan-source-card strong { font-size: 13px; line-height: 1.45; }
.source-preview { color: #475569; font-size: 12px; line-height: 1.5; }
.source-location, .source-hash { color: #64748b; font: 11px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; }
</style>
