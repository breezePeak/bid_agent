<template>
  <nav class="workspace-subnav" aria-label="工作空间导航">
    <router-link
      class="nav-item"
      :class="{
        active: mode === 'pipeline',
        muted: false,
      }"
      :to="`/business/${workspaceId}/pipeline`"
    >
      1. 流水线
      <span v-if="!hasOutline" class="badge">当前</span>
    </router-link>
    <router-link
      class="nav-item"
      :class="{
        active: mode === 'workbench' || mode === 'home' || mode === 'chapter',
        disabled: !hasOutline,
      }"
      :to="hasOutline ? `/business/${workspaceId}` : `/business/${workspaceId}/pipeline`"
      :aria-disabled="!hasOutline"
      @click="onWorkbenchClick"
    >
      2. 写作工作台
      <span v-if="hasOutline" class="badge ok">目录就绪</span>
      <span v-else-if="outlineProbing" class="badge">检测中</span>
      <span v-else class="badge wait">待目录</span>
    </router-link>
    <div class="spacer" />
    <span v-if="chapterHint" class="hint">{{ chapterHint }}</span>
  </nav>
</template>

<script setup>
const props = defineProps({
  workspaceId: { type: String, required: true },
  mode: { type: String, default: 'pipeline' },
  hasOutline: { type: Boolean, default: false },
  outlineProbing: { type: Boolean, default: false },
  chapterHint: { type: String, default: '' },
})

function onWorkbenchClick(event) {
  if (!props.hasOutline) {
    event.preventDefault()
  }
}
</script>

<style scoped>
.workspace-subnav {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  background: #fff;
  flex-shrink: 0;
}
.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 13px;
  color: #475569;
  text-decoration: none;
  border: 1px solid transparent;
}
.nav-item:hover:not(.disabled) {
  background: #f1f5f9;
  color: #0f172a;
}
.nav-item.active {
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
  border-color: rgba(37, 99, 235, 0.2);
  font-weight: 600;
}
.nav-item.disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #475569;
}
.badge.ok {
  background: #dcfce7;
  color: #166534;
}
.badge.wait {
  background: #ffedd5;
  color: #9a3412;
}
.spacer { flex: 1; }
.hint {
  font-size: 12px;
  color: #64748b;
}
</style>
