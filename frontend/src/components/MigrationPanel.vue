<template>
  <div class="migration-panel">
    <div class="ip-header">
      <div class="ip-title-row"><strong>V1 → V2 迁移</strong><button class="btn btn-sm" @click="refresh" :disabled="loading">刷新</button></div>
      <div class="ip-empty-soft">{{ migration.status === 'needs_reconciliation' ? `待协调 ${migration.open_count || 0} 项` : '迁移状态就绪' }}</div>
    </div>
    <div class="migration-actions">
      <button class="btn btn-sm" @click="propose('migration.scan')">扫描旧状态</button>
      <button class="btn btn-sm" @click="propose('migration.cutover')" :disabled="migration.status === 'needs_reconciliation'">切换 V2</button>
    </div>
    <div v-if="pending" class="migration-pending">
      <span>{{ pending.label || '高风险操作等待确认' }}</span>
      <button class="btn btn-sm btn-danger" @click="confirm">确认执行</button>
      <button class="btn btn-sm" @click="pending = null">取消</button>
    </div>
    <div v-for="item in openConflicts" :key="item.conflict_id" class="migration-conflict">
      <b>{{ item.domain }}</b><span>{{ item.reason }}</span>
      <select v-model="choices[item.conflict_id]"><option value="keep_orphan">保留 orphan</option><option value="bind_legacy">绑定旧状态</option><option value="mark_failed">标记失败</option></select>
      <button class="btn btn-sm" @click="proposeResolve(item)">提交处理</button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { confirmWorkspaceAction, fetchWorkspaceSnapshot, submitWorkspaceCommand } from '../api'

const props = defineProps({ runId: { type: String, required: true } })
const migration = ref({})
const loading = ref(false)
const pending = ref(null)
const choices = ref({})
const openConflicts = computed(() => (migration.value.conflicts || []).filter(item => item.status === 'open'))

async function refresh() {
  if (!props.runId) return
  loading.value = true
  try { migration.value = (await fetchWorkspaceSnapshot(props.runId))?.data?.snapshot?.migration || {} }
  finally { loading.value = false }
}
async function propose(kind, payload = {}) {
  try {
    const response = await submitWorkspaceCommand(props.runId, kind, payload)
    const action = response?.data?.action
    if (action?.confirmation_id) pending.value = { ...action, kind, payload }
    else await refresh()
  } catch (error) { window.alert(error?.response?.data?.message || error?.message || '迁移操作失败') }
}
function proposeResolve(item) {
  const reason = window.prompt('请输入协调原因（将写入审计）', '')
  if (!reason) return
  propose('migration.reconcile', { conflict_id: item.conflict_id, resolution: choices.value[item.conflict_id] || 'keep_orphan', reason })
}
async function confirm() {
  try { await confirmWorkspaceAction(props.runId, pending.value.confirmation_id); pending.value = null; await refresh() }
  catch (error) { window.alert(error?.response?.data?.message || error?.message || '确认失败') }
}
watch(() => props.runId, refresh)
onMounted(refresh)
</script>

<style scoped>
.migration-actions,.migration-pending{display:flex;gap:8px;align-items:center;padding:8px 12px}.migration-conflict{display:grid;gap:7px;padding:10px 12px;border-top:1px solid var(--border-color,#ddd);font-size:12px}.migration-conflict select{max-width:150px}.migration-pending{background:#fff6df;font-size:12px;flex-wrap:wrap}
</style>
