<template>
  <button type="button" class="legacy-card" @click="$emit('preview', match)">
    <span class="legacy-title">{{ match.section_title || '未命名旧章节' }}</span>
    <span class="legacy-path">{{ (match.heading_path || []).join(' / ') }}</span>
    <span class="legacy-content">{{ match.content }}</span>
    <span class="legacy-meta">{{ match.section_id }} · {{ match.block_id }}</span>
    <span class="legacy-hash">hash {{ match.content_hash }}</span>
    <span class="legacy-reason">{{ match.reason }}</span>
    <span v-if="match.risk" class="legacy-risk">{{ match.risk }}</span>
  </button>
</template>

<script setup>
defineProps({ match: { type: Object, required: true } })
defineEmits(['preview'])
</script>

<style scoped>
.legacy-card { width: 100%; display: flex; flex-direction: column; gap: 5px; padding: 12px; border: 1px solid #dbe3ee; border-radius: 10px; background: #fff; text-align: left; cursor: pointer; color: #334155; }
.legacy-card:hover { border-color: #60a5fa; background: #f8fbff; }
.legacy-title { color: #0f172a; font-weight: 700; }
.legacy-path,.legacy-meta,.legacy-hash { color: #64748b; font-size: 11px; overflow-wrap: anywhere; }
.legacy-content { max-height: 6.6em; overflow: hidden; white-space: pre-wrap; line-height: 1.65; font-size: 13px; }
.legacy-reason { font-size: 12px; color: #1d4ed8; }
.legacy-risk { font-size: 12px; color: #9a3412; }
</style>
