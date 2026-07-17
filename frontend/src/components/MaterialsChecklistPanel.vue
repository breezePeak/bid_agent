<template>
  <div class="mcp">
    <div class="mcp-header">
      <div class="mcp-title-row">
        <strong>材料清单</strong>
        <button class="btn btn-sm" :disabled="loading" @click="refresh">刷新</button>
      </div>
      <div v-if="exists" class="mcp-banner" :class="{ warn: (summary.deferred || 0) > 0 }">
        <div class="mcp-banner-kicker">
          {{ (summary.deferred || 0) > 0 ? '有待补材料 · 写作将留白占位' : '清单已就绪' }}
        </div>
        <div class="mcp-banner-stats">
          共 {{ summary.total || 0 }}
          · 待补 {{ summary.deferred || 0 }}
          · 已齐 {{ summary.ready || 0 }}
          · 放弃 {{ summary.waived || 0 }}
        </div>
      </div>
      <div v-else class="mcp-empty">{{ emptyMsg }}</div>
      <div class="mcp-actions" v-if="exists">
        <button class="btn btn-sm" :disabled="busy" @click="doRebuild">重建清单</button>
        <button
          class="btn btn-sm btn-primary"
          :disabled="busy || !refillPlans.length"
          :title="refillPlans.length ? `将回填 ${refillPlans.length} 章` : '暂无已 ready 且正文仍有占位的章节'"
          @click="doRefill"
        >补料回填{{ refillPlans.length ? ` (${refillPlans.length})` : '' }}</button>
      </div>
      <div v-if="msg" class="mcp-msg">{{ msg }}</div>
    </div>

    <div class="mcp-filters" v-if="exists">
      <button
        v-for="f in filters"
        :key="f.key"
        class="mcp-filter"
        :class="{ on: filter === f.key }"
        @click="filter = f.key"
      >{{ f.label }}</button>
    </div>

    <div class="mcp-list" v-if="filtered.length">
      <div
        v-for="item in filtered"
        :key="item.item_id"
        class="mcp-item"
        :class="['rs-' + (item.response_status || 'deferred'), 'es-' + (item.evidence_status || '')]"
      >
        <div class="mcp-item-head">
          <span class="mcp-id">{{ item.item_id }}</span>
          <span class="mcp-cat">{{ categoryLabel(item.category) }}</span>
          <span class="mcp-sev">{{ severityLabel(item.severity) }}</span>
        </div>
        <div class="mcp-req">{{ item.requirement }}</div>
        <div class="mcp-meta">
          <span>证据 {{ evidenceLabel(item.evidence_status) }}</span>
          <span v-if="item.suggested_attachment">建议：{{ item.suggested_attachment }}</span>
        </div>
        <div class="mcp-reason" v-if="item.reason">{{ item.reason }}</div>
        <div class="mcp-status-row">
          <button
            v-for="st in statuses"
            :key="st.key"
            class="mcp-st"
            :class="{ on: item.response_status === st.key }"
            :disabled="busyId === item.item_id"
            @click="setStatus(item, st.key)"
          >{{ st.label }}</button>
        </div>
        <textarea
          class="mcp-note"
          rows="2"
          :placeholder="notePlaceholder(item.response_status)"
          :value="notes[item.item_id] ?? item.reason ?? ''"
          @input="notes[item.item_id] = $event.target.value"
          @blur="saveNote(item)"
        />
      </div>
    </div>
    <div v-else-if="exists" class="mcp-empty">当前筛选下没有条目</div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import {
  fetchMaterialsChecklist,
  updateMaterialsChecklistItem,
  rebuildMaterialsChecklist,
  refillMaterialsChecklist,
} from '../api'

const props = defineProps({
  runId: { type: String, required: true },
})

const loading = ref(false)
const busy = ref(false)
const busyId = ref('')
const exists = ref(false)
const summary = ref({})
const items = ref([])
const refillPlans = ref([])
const filter = ref('deferred')
const notes = ref({})
const msg = ref('')
const emptyMsg = ref('暂无材料清单。跑完「材料资格清单」阶段后会显示。')

const filters = [
  { key: 'deferred', label: '待补' },
  { key: 'ready', label: '已齐' },
  { key: 'waived', label: '放弃' },
  { key: 'all', label: '全部' },
]
const statuses = [
  { key: 'deferred', label: '待补' },
  { key: 'ready', label: '已齐' },
  { key: 'waived', label: '放弃' },
]

const filtered = computed(() => {
  const list = Array.isArray(items.value) ? items.value : []
  if (filter.value === 'all') return list
  return list.filter((i) => (i.response_status || 'deferred') === filter.value)
})

function categoryLabel(c) {
  return ({ qualification: '资格', disqualification: '废标', mandatory_doc: '必交件', evidence: '证据' })[c] || c || '—'
}
function severityLabel(s) {
  return ({ fatal: '致命', critical: '严重', major: '重要', minor: '次要', info: '提示' })[s] || s || '—'
}
function evidenceLabel(s) {
  return ({ missing: '缺失', weak: '弱', satisfied: '足' })[s] || s || '—'
}
function notePlaceholder(st) {
  if (st === 'waived') return '可选：放弃原因'
  if (st === 'ready') return '可选：材料已齐说明'
  return '可选：暂不能提供的原因（写入正文留白）'
}

function applyPayload(data) {
  exists.value = !!data.exists
  summary.value = data.summary || data.checklist?.summary || {}
  items.value = data.items || data.checklist?.items || []
  refillPlans.value = data.refill_plans || []
  if (!data.exists) emptyMsg.value = data.message || emptyMsg.value
}

async function refresh() {
  loading.value = true
  msg.value = ''
  try {
    const { data } = await fetchMaterialsChecklist()
    if (data?.ok) applyPayload(data)
    else emptyMsg.value = data?.message || '加载失败'
  } catch (e) {
    emptyMsg.value = e.message || '加载材料清单失败'
  } finally {
    loading.value = false
  }
}

async function setStatus(item, status) {
  busyId.value = item.item_id
  msg.value = ''
  try {
    const reason = (notes.value[item.item_id] ?? item.reason ?? '').trim()
    const { data } = await updateMaterialsChecklistItem({
      item_id: item.item_id,
      response_status: status,
      reason,
    })
    if (!data?.ok) throw new Error(data?.message || '更新失败')
    if (data.checklist) {
      applyPayload({ exists: true, checklist: data.checklist, items: data.checklist.items, summary: data.checklist.summary, refill_plans: refillPlans.value })
      await refresh()
    } else {
      await refresh()
    }
    msg.value = data.message || '已更新'
  } catch (e) {
    msg.value = e.response?.data?.message || e.message || '更新失败'
  } finally {
    busyId.value = ''
  }
}

async function saveNote(item) {
  const reason = (notes.value[item.item_id] ?? '').trim()
  if (reason === (item.reason || '')) return
  await setStatus(item, item.response_status || 'deferred')
}

async function doRebuild() {
  busy.value = true
  msg.value = '重建中…'
  try {
    const { data } = await rebuildMaterialsChecklist()
    if (!data?.ok) throw new Error(data?.message || '重建失败')
    await refresh()
    msg.value = data.message || '已重建'
  } catch (e) {
    msg.value = e.response?.data?.message || e.message || '重建失败'
  } finally {
    busy.value = false
  }
}

async function doRefill() {
  busy.value = true
  msg.value = '正在按已齐材料回填章节…'
  try {
    const { data } = await refillMaterialsChecklist({})
    if (!data?.ok && !(data?.rewritten || []).length) throw new Error(data?.message || '回填失败')
    await refresh()
    msg.value = data.message || '回填完成'
  } catch (e) {
    msg.value = e.response?.data?.message || e.message || '回填失败'
  } finally {
    busy.value = false
  }
}

watch(() => props.runId, () => refresh())
onMounted(refresh)
defineExpose({ refresh })
</script>
