<template>
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
        <div class="form-group">
          <label for="ws-type">项目类型 <span class="required">*</span></label>
          <select id="ws-type" v-model="form.projectType" required>
            <option value="">请选择项目类型</option>
            <option
              v-for="choice in projectChoices"
              :key="choice.project_type"
              :value="choice.project_type"
            >
              {{ choice.label }} - {{ choice.description }}
            </option>
          </select>
        </div>
        <div class="form-group">
          <label for="ws-pages">期望页数</label>
          <input
            id="ws-pages"
            v-model.number="form.expectedPages"
            type="number"
            min="1"
            max="9999"
            placeholder="请输入期望页数（可选）"
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
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { createRun, fetchProjectProfile } from '../api'

defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'created'])

const form = reactive({
  name: '',
  projectType: '',
  expectedPages: null,
})

const projectChoices = ref([])
const submitting = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    const { data } = await fetchProjectProfile()
    if (data.ok && data.choices) {
      projectChoices.value = data.choices
    }
  } catch (e) {
    console.error('加载项目类型失败', e)
  }
})

async function handleSubmit() {
  error.value = ''
  if (!form.name.trim()) {
    error.value = '请输入项目名称'
    return
  }
  if (!form.projectType) {
    error.value = '请选择项目类型'
    return
  }

  submitting.value = true
  try {
    const { data } = await createRun(form.name.trim(), form.projectType, form.expectedPages || 0)
    if (data.ok && data.run) {
      emit('created', data.run.id)
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
