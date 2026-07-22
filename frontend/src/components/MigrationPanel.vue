<template>
  <div class="migration-panel">
    <div class="ip-header">
      <div class="ip-title-row"><strong>V1 → V2 迁移</strong><button class="btn btn-sm" @click="refresh" :disabled="loading">刷新</button></div>
      <div class="ip-empty-soft">{{ migrationLabel }}</div>
    </div>
    <div class="migration-actions">
      <button class="btn btn-sm" @click="propose('migration.scan')">扫描旧状态</button>
      <button class="btn btn-sm" @click="propose('migration.cutover')" :disabled="migration.status === 'needs_reconciliation'">切换 V2</button>
    </div>
    <div v-if="backups.length" class="migration-backups">
      <b>恢复演练备份</b>
      <div v-for="backup in backups" :key="backup.path" class="migration-backup">
        <span>{{ backup.path }} · {{ backup.verified ? '已校验' : '未通过校验' }}</span>
        <button class="btn btn-sm" :disabled="!backup.verified || drilling === backup.path" @click="drill(backup)">演练</button>
      </div>
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
import { confirmWorkspaceAction, drillMigrationBackup, fetchMigrationBackups, fetchWorkspaceSnapshot, submitWorkspaceCommand } from '../api'

const props = defineProps({ runId: { type: String, required: true } })
const migration = ref({})
const loading = ref(false)
const pending = ref(null)
const choices = ref({})
const backups = ref([])
const drilling = ref('')
const openConflicts = computed(() => (migration.value.conflicts || []).filter(item => item.status === 'open'))
const migrationLabel = computed(() => {
  if (migration.value.status === 'needs_reconciliation') return `待协调 ${migration.value.open_count || 0} 项`
  if (migration.value.status === 'cutover_stale') return 'V2 切换已失效：旧状态源变化，需重新扫描并切换'
  if (migration.value.cutover?.status === 'active') return 'V2 控制面已切换'
  return '迁移状态就绪'
})

async function refresh() {
  if (!props.runId) return
  loading.value = true
  try {
    migration.value = (await fetchWorkspaceSnapshot(props.runId))?.data?.snapshot?.migration || {}
    backups.value = (await fetchMigrationBackups(props.runId))?.data?.backups || []
  }
  finally { loading.value = false }
}
async function drill(backup) {
  drilling.value = backup.path
  try {
    const result = await drillMigrationBackup(props.runId, backup.path)
    window.alert(result?.data?.backup?.recovery_drill === 'passed' ? '恢复演练通过' : '恢复演练未通过')
  } catch (error) { window.alert(error?.response?.data?.message || error?.message || '恢复演练失败') }
  finally { drilling.value = '' }
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
.migration-actions,.migration-pending{display:flex;gap:8px;align-items:center;padding:8px 12px}.migration-conflict{display:grid;gap:7px;padding:10px 12px;border-top:1px solid var(--border-color,#ddd);font-size:12px}.migration-conflict select{max-width:150px}.migration-pending{background:#fff6df;font-size:12px;flex-wrap:wrap}.migration-backups{display:grid;gap:6px;padding:10px 12px;border-top:1px solid var(--border-color,#ddd);font-size:12px}.migration-backup{display:flex;justify-content:space-between;gap:8px;align-items:center}
</style>
