<template>
  <section class="legacy-panel" aria-labelledby="legacy-bid-heading">
    <header>
      <div>
        <p>标书改写 · 独立材料旁路</p>
        <h3 id="legacy-bid-heading">旧投标书（必传）</h3>
        <small>旧标书不会进入新项目的材料清单或目录生成输入。</small>
      </div>
      <label class="upload-button">
        {{ uploading ? '解析中…' : (summary.active_id ? '替换旧标书' : '上传旧标书') }}
        <input type="file" accept=".docx,.pdf,.md,.txt" :disabled="uploading" @change="handleUpload" />
      </label>
    </header>
    <p v-if="error" class="legacy-error" role="alert">{{ error }}</p>
    <p v-else-if="summary.active_id" class="legacy-status">
      {{ summary.filename }} · {{ statusText }}
    </p>
    <LegacyBidIndexPreview :index="index" />
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { fetchLegacyBidIndex, uploadLegacyBid } from '../api'
import LegacyBidIndexPreview from './LegacyBidIndexPreview.vue'

const props = defineProps({
  runId: { type: String, required: true },
  summary: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['uploaded'])
const uploading = ref(false)
const error = ref('')
const index = ref(null)
const statusText = computed(() => ({ not_uploaded: '未上传', parsing: '解析中', ready: '解析完成', failed: '解析失败' }[props.summary.status] || props.summary.status))

watch(() => props.summary.active_id, async id => {
  if (!id || props.summary.status !== 'ready') { index.value = null; return }
  try {
    const { data } = await fetchLegacyBidIndex(props.runId, id)
    index.value = data?.index || null
  } catch (cause) {
    error.value = cause?.response?.data?.message || '旧标书索引读取失败'
  }
}, { immediate: true })

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  uploading.value = true
  error.value = ''
  try {
    const { data } = await uploadLegacyBid(props.runId, file)
    index.value = data?.index || null
    emit('uploaded')
  } catch (cause) {
    error.value = cause?.response?.data?.message || '旧标书上传或解析失败'
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.legacy-panel { display: grid; gap: 12px; margin: 16px; padding: 18px; border: 1px solid #c7d2fe; border-radius: 14px; background: #fff; box-shadow: 0 8px 24px rgba(30, 41, 59, .06); }
.legacy-panel header { display: flex; gap: 16px; align-items: center; justify-content: space-between; }
.legacy-panel p, .legacy-panel h3 { margin: 0; }
.legacy-panel header p { color: #4f46e5; font-size: 11px; font-weight: 800; }
.legacy-panel header h3 { margin-top: 3px; color: #172554; }
.legacy-panel header small, .legacy-status { color: #64748b; }
.upload-button { position: relative; flex: 0 0 auto; padding: 10px 14px; border-radius: 9px; color: #fff; background: #4f46e5; cursor: pointer; font-weight: 700; }
.upload-button input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.legacy-error { color: #b91c1c; }
@media (max-width: 720px) { .legacy-panel header { align-items: stretch; flex-direction: column; } .upload-button { text-align: center; } }
</style>
