<template>
  <div v-if="open" class="drawer">
    <header>
      <h3>正文版本</h3>
      <button type="button" class="link" @click="$emit('close')">关闭</button>
    </header>
    <div class="meta">
      head {{ headRevision }} / formal {{ formalRevision }}
    </div>
    <div v-for="item in revisions" :key="item.content_revision" class="rev">
      <div>
        <strong>r{{ item.content_revision }}</strong>
        <span>{{ item.source }}</span>
        <span class="hash">{{ String(item.content_hash || '').slice(0, 10) }}</span>
      </div>
      <div class="actions">
        <button type="button" class="link" @click="$emit('restore', item)">恢复为新 head</button>
        <button
          v-if="item.content_revision === headRevision"
          type="button"
          class="link"
          @click="$emit('approve', item)"
        >H2 确认正式版</button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  revisions: { type: Array, default: () => [] },
  headRevision: { type: [Number, String], default: 0 },
  formalRevision: { type: [Number, String], default: 0 },
})
defineEmits(['close', 'restore', 'approve'])
</script>

<style scoped>
.drawer {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 320px;
  background: #fff;
  border-left: 1px solid #e2e8f0;
  padding: 12px;
  overflow: auto;
  z-index: 5;
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.08);
}
header { display: flex; justify-content: space-between; align-items: center; }
.meta { font-size: 12px; color: #64748b; margin: 8px 0 12px; }
.rev { border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px; margin-bottom: 8px; }
.rev span { margin-left: 8px; font-size: 12px; color: #64748b; }
.hash { font-family: ui-monospace, monospace; }
.actions { margin-top: 6px; display: flex; gap: 8px; }
.link { border: none; background: none; color: #2563eb; cursor: pointer; }
</style>
