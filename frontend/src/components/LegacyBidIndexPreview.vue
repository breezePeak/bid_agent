<template>
  <section v-if="index" class="legacy-preview">
    <dl class="legacy-metrics">
      <div><dt>章节</dt><dd>{{ index.sections?.length || 0 }}</dd></div>
      <div><dt>段落/块</dt><dd>{{ index.blocks?.length || 0 }}</dd></div>
      <div><dt>结构缺口</dt><dd>{{ index.structure_gaps?.length || 0 }}</dd></div>
    </dl>
    <p v-for="item in index.needs_review || []" :key="item" class="review-note">{{ item }}</p>
    <div class="legacy-browser">
      <nav aria-label="旧投标书目录">
        <button
          v-for="section in index.sections || []"
          :key="section.section_id"
          type="button"
          :class="{ active: selectedSectionId === section.section_id }"
          :style="{ paddingLeft: `${10 + (section.level - 1) * 14}px` }"
          @click="selectedSectionId = section.section_id"
        >{{ section.title }}</button>
      </nav>
      <article class="legacy-blocks">
        <template v-if="selectedSection">
          <h4>{{ selectedSection.title }}</h4>
          <button
            v-for="block in selectedBlocks"
            :key="block.block_id"
            type="button"
            class="legacy-block"
            @click="selectedBlockId = block.block_id"
          >
            <span>{{ block.content }}</span>
            <small>
              {{ block.page ? `第 ${block.page} 页 · ` : '' }}{{ block.block_id }} · {{ block.content_hash?.slice(0, 10) }}
            </small>
          </button>
        </template>
        <p v-else>选择左侧目录查看原文。</p>
      </article>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ index: { type: Object, default: null } })
const selectedSectionId = ref('')
const selectedBlockId = ref('')
watch(() => props.index, value => {
  selectedSectionId.value = value?.sections?.[0]?.section_id || ''
  selectedBlockId.value = ''
}, { immediate: true })
const selectedSection = computed(() => props.index?.sections?.find(item => item.section_id === selectedSectionId.value) || null)
const selectedBlocks = computed(() => {
  const ids = new Set(selectedSection.value?.content_block_ids || [])
  return (props.index?.blocks || []).filter(block => ids.has(block.block_id))
})
</script>

<style scoped>
.legacy-preview { display: grid; gap: 12px; }
.legacy-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 0; }
.legacy-metrics div { padding: 10px; border-radius: 9px; background: #f1f5f9; }
.legacy-metrics dt { color: #64748b; font-size: 11px; }
.legacy-metrics dd { margin: 3px 0 0; color: #1e293b; font-size: 18px; font-weight: 800; }
.review-note { margin: 0; color: #b45309; font-size: 12px; }
.legacy-browser { display: grid; grid-template-columns: minmax(180px, 30%) 1fr; min-height: 240px; border: 1px solid #dbe4f0; border-radius: 10px; overflow: hidden; }
.legacy-browser nav { display: grid; align-content: start; border-right: 1px solid #dbe4f0; background: #f8fafc; }
.legacy-browser nav button { min-height: 38px; border: 0; background: transparent; text-align: left; cursor: pointer; }
.legacy-browser nav button.active { color: #3730a3; background: #e0e7ff; font-weight: 700; }
.legacy-blocks { padding: 13px; overflow: auto; }
.legacy-blocks h4 { margin: 0 0 10px; }
.legacy-block { display: grid; width: 100%; gap: 5px; padding: 10px 0; border: 0; border-bottom: 1px solid #e2e8f0; background: transparent; text-align: left; cursor: pointer; }
.legacy-block small { color: #64748b; font-family: ui-monospace, Consolas, monospace; }
@media (max-width: 720px) { .legacy-browser { grid-template-columns: 1fr; } .legacy-browser nav { border-right: 0; border-bottom: 1px solid #dbe4f0; } }
</style>
