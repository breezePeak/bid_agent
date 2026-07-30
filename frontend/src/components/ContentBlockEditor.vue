<template>
  <div class="block-editor">
    <div class="toolbar">
      <button type="button" class="btn" :disabled="readonly || busy" @click="addParagraph">插入段落</button>
      <button type="button" class="btn" :disabled="readonly || busy || !dirty" @click="save">保存正文</button>
      <span v-if="dirty" class="dirty">未保存</span>
      <span v-if="remoteHint" class="remote">{{ remoteHint }}</span>
    </div>
    <div v-if="!localBlocks.length" class="empty">暂无正文块。可生成草稿或手动插入段落。</div>
    <div
      v-for="(block, index) in localBlocks"
      :key="block.block_id"
      class="block"
      :class="{ locked: block.lock_state === 'USER_LOCKED' || block.human_locked }"
    >
      <div class="block-meta">
        <span>#{{ index + 1 }} {{ block.type }}</span>
        <span>{{ block.source || 'AI_GENERATED' }}</span>
        <span v-if="block.lock_state === 'USER_LOCKED' || block.human_locked">已锁定</span>
        <button
          type="button"
          class="link"
          :disabled="readonly || busy || index === 0"
          @click="move(block.block_id, index - 1)"
        >上移</button>
        <button
          type="button"
          class="link"
          :disabled="readonly || busy || index >= localBlocks.length - 1"
          @click="move(block.block_id, index + 1)"
        >下移</button>
        <button type="button" class="link danger" :disabled="readonly || busy" @click="remove(block.block_id)">删除</button>
      </div>
      <textarea
        :value="block.content"
        :disabled="readonly || busy"
        rows="4"
        @input="onEdit(block.block_id, $event.target.value)"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  blocks: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  remoteHint: { type: String, default: '' },
})
const emit = defineEmits(['save'])

const localBlocks = ref([])
const dirty = ref(false)
const baselineJson = ref('[]')

watch(
  () => props.blocks,
  (value) => {
    if (dirty.value) return
    const next = Array.isArray(value) ? value.map(item => ({ ...item })) : []
    localBlocks.value = next
    baselineJson.value = JSON.stringify(next)
  },
  { immediate: true, deep: true },
)

function markDirty() {
  dirty.value = JSON.stringify(localBlocks.value) !== baselineJson.value
}

function onEdit(blockId, content) {
  localBlocks.value = localBlocks.value.map(item => (
    item.block_id === blockId ? { ...item, content } : item
  ))
  markDirty()
}

function addParagraph() {
  const blockId = `local-${Date.now()}`
  localBlocks.value = [
    ...localBlocks.value,
    {
      block_id: blockId,
      type: 'paragraph',
      content: '新段落',
      source: 'USER_CREATED',
      lock_state: 'USER_LOCKED',
      human_locked: true,
      order: localBlocks.value.length,
    },
  ]
  markDirty()
}

function remove(blockId) {
  localBlocks.value = localBlocks.value.filter(item => item.block_id !== blockId)
  markDirty()
}

function move(blockId, toIndex) {
  const list = [...localBlocks.value]
  const from = list.findIndex(item => item.block_id === blockId)
  if (from < 0) return
  const [item] = list.splice(from, 1)
  list.splice(toIndex, 0, item)
  localBlocks.value = list.map((block, order) => ({ ...block, order }))
  markDirty()
}

function save() {
  const baseline = JSON.parse(baselineJson.value || '[]')
  const baselineIds = new Set(baseline.map(item => item.block_id))
  const currentIds = new Set(localBlocks.value.map(item => item.block_id))
  const operations = []

  for (const block of baseline) {
    if (!currentIds.has(block.block_id)) {
      operations.push({ op: 'delete', block_id: block.block_id })
    }
  }
  localBlocks.value.forEach((block, index) => {
    if (!baselineIds.has(block.block_id)) {
      operations.push({
        op: 'insert',
        index,
        block: {
          block_id: block.block_id,
          type: block.type || 'paragraph',
          content: block.content,
          target_node_id: block.target_node_id,
        },
      })
      return
    }
    const prev = baseline.find(item => item.block_id === block.block_id)
    if (prev && prev.content !== block.content) {
      operations.push({
        op: 'update',
        block_id: block.block_id,
        content: block.content,
        type: block.type,
      })
    }
  })
  // Final order via moves from remaining baseline order is approximate; replace_all safer for reorder+edit.
  operations.push({
    op: 'replace_all',
    blocks: localBlocks.value.map((block, order) => ({
      ...block,
      order,
      source: block.source === 'AI_GENERATED' ? 'USER_EDITED' : (block.source || 'USER_CREATED'),
      lock_state: 'USER_LOCKED',
      human_locked: true,
    })),
  })
  emit('save', operations)
  dirty.value = false
  baselineJson.value = JSON.stringify(localBlocks.value)
}

defineExpose({ dirty, markClean() {
  dirty.value = false
  baselineJson.value = JSON.stringify(localBlocks.value)
} })
</script>

<style scoped>
.block-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
}
.toolbar { display: flex; gap: 8px; align-items: center; }
.btn, .link { cursor: pointer; }
.btn { border: 1px solid #cbd5e1; background: #fff; border-radius: 6px; padding: 6px 10px; }
.link { border: none; background: none; color: #2563eb; }
.link.danger { color: #dc2626; }
.dirty { color: #d97706; font-size: 12px; }
.remote { color: #7c3aed; font-size: 12px; }
.block { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px; background: #fff; }
.block.locked { border-color: #f59e0b; }
.block-meta { display: flex; gap: 10px; font-size: 12px; color: #64748b; margin-bottom: 6px; flex-wrap: wrap; }
textarea { width: 100%; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px; font: inherit; resize: vertical; }
.empty { color: #94a3b8; padding: 24px; text-align: center; }
</style>
