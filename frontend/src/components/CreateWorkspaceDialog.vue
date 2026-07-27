<template>
  <Teleport to="body">
    <div v-if="visible" class="dialog-overlay" @click.self="$emit('close')">
      <div class="dialog">
        <div class="dialog-header">
          <h2>新建工作空间</h2>
          <button class="btn btn-icon" @click="$emit('close')">&times;</button>
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
    const { data } = await createRun(form.name.trim(), '', 0)
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
