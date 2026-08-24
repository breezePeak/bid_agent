<template>
  <Teleport to="body">
    <Transition name="dialog">
    <div v-if="visible" class="dialog-overlay" @click.self="$emit('close')">
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="create-workspace-title">
        <div class="dialog-header">
          <h2 id="create-workspace-title">新建工作空间</h2>
          <button type="button" class="dialog-close" aria-label="关闭" @click="$emit('close')">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
          </button>
        </div>
        <form class="dialog-body" @submit.prevent="handleSubmit">
          <div class="form-group">
            <label for="ws-name">项目名称 <span class="required">*</span></label>
            <input
              id="ws-name"
              v-model="form.name"
              type="text"
              maxlength="48"
              placeholder="例如：某项目投标文件"
              required
            />
          </div>
          <fieldset class="mode-fieldset">
            <legend>编写模式</legend>
            <label class="mode-card" :class="{ selected: form.projectMode === 'full_write' }">
              <input v-model="form.projectMode" type="radio" value="full_write" />
              <span><strong>全量编写</strong><small>沿用当前完整生成流程</small></span>
            </label>
            <label class="mode-card" :class="{ selected: form.projectMode === 'bid_rewrite' }">
              <input v-model="form.projectMode" type="radio" value="bid_rewrite" />
              <span><strong>标书改写</strong><small>使用新招标书和旧投标书逐章改写</small></span>
            </label>
          </fieldset>
          <p v-if="error" class="form-error">{{ error }}</p>
          <div class="dialog-footer">
            <button type="button" class="btn" @click="$emit('close')">取消</button>
            <button type="submit" class="btn btn-primary" :disabled="submitting">
              {{ submitting ? '创建中...' : '创建' }}
            </button>
          </div>
        </form>
      </div>
    </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { createRun } from '../api'

defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'created'])

const form = reactive({
  name: '',
  projectType: '',
  expectedPages: null,
  projectMode: 'full_write',
})

const submitting = ref(false)
const error = ref('')

async function handleSubmit() {
  error.value = ''
  if (!form.name.trim()) {
    error.value = '请输入项目名称'
    return
  }
  submitting.value = true
  try {
    const { data } = await createRun(form.name.trim(), '', 0, form.projectMode)
    if (data.ok && data.workspace) {
      emit('created', data.workspace.id)
    } else {
      error.value = data.message || '创建失败'
    }
  } catch (e) {
    error.value = e.response?.data?.message || '创建失败，请检查后端服务'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.mode-fieldset { display: grid; gap: 10px; margin: 18px 0 0; padding: 0; border: 0; }
.mode-fieldset legend { margin-bottom: 8px; color: #334155; font-size: 13px; font-weight: 700; }
.mode-card { display: grid; grid-template-columns: auto 1fr; gap: 11px; align-items: start; padding: 13px; border: 1px solid #cbd5e1; border-radius: 11px; cursor: pointer; }
.mode-card.selected { border-color: #4f46e5; background: #eef2ff; }
.mode-card input { margin-top: 3px; }
.mode-card strong, .mode-card small { display: block; }
.mode-card small { margin-top: 3px; color: #64748b; font-weight: 400; }
</style>
