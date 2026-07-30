<template>
  <aside class="context-panel">
    <header>
      <h3>章节上下文</h3>
      <span v-if="headRevision">rev {{ headRevision }}</span>
    </header>
    <div v-if="!items.length" class="empty">暂无上下文项</div>
    <div v-for="item in items" :key="item.item_id" class="item">
      <div class="kind">{{ item.kind }}</div>
      <div class="title">{{ item.title }}</div>
      <div class="body">{{ item.body }}</div>
      <div class="src">{{ item.source }}</div>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  headRevision: { type: [Number, String], default: 0 },
})
</script>

<style scoped>
.context-panel {
  border-left: 1px solid #e2e8f0;
  padding: 12px;
  overflow: auto;
  background: #f8fafc;
  min-width: 240px;
}
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
h3 { margin: 0; font-size: 14px; }
.item { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px; margin-bottom: 8px; }
.kind { font-size: 11px; color: #2563eb; font-weight: 600; }
.title { font-size: 13px; font-weight: 600; margin: 4px 0; }
.body { font-size: 12px; color: #334155; white-space: pre-wrap; }
.src { font-size: 11px; color: #94a3b8; margin-top: 4px; }
.empty { color: #94a3b8; font-size: 12px; }
</style>
