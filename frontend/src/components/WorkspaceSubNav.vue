<template>
  <nav class="workspace-subnav" aria-label="工作空间导航">
    <router-link
      class="nav-item"
      :class="{ active: mode === 'home' || mode === 'chapter' }"
      :to="`/business/${workspaceId}`"
    >写作工作台</router-link>
    <router-link
      class="nav-item"
      :class="{ active: mode === 'pipeline' }"
      :to="`/business/${workspaceId}/pipeline`"
    >流水线</router-link>
    <div class="spacer" />
    <span v-if="chapterHint" class="hint">{{ chapterHint }}</span>
  </nav>
</template>

<script setup>
defineProps({
  workspaceId: { type: String, required: true },
  mode: { type: String, default: 'home' },
  chapterHint: { type: String, default: '' },
})
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
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 13px;
  color: #475569;
  text-decoration: none;
  border: 1px solid transparent;
}
.nav-item:hover {
  background: #f1f5f9;
  color: #0f172a;
}
.nav-item.active {
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
  border-color: rgba(37, 99, 235, 0.2);
  font-weight: 600;
}
.nav-item.muted {
  cursor: default;
}
.spacer { flex: 1; }
.hint {
  font-size: 12px;
  color: #64748b;
}
</style>
