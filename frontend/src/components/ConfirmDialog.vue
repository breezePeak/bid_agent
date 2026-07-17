<template>
  <Teleport to="body">
    <div v-if="visible" class="dialog-overlay" @click.self="onCancel">
      <div class="dialog" :style="{ width: width + 'px' }">
        <div class="dialog-header">
          <h2>{{ title }}</h2>
          <button class="btn btn-icon" @click="onCancel">&times;</button>
        </div>
        <div class="dialog-body">
          <p v-if="message">{{ message }}</p>
          <slot />
          <div class="dialog-footer">
            <button class="btn" @click="onCancel">取消</button>
            <button class="btn btn-danger" @click="onConfirm" :disabled="confirming">
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  width: { type: Number, default: 440 },
})

const emit = defineEmits(['confirm', 'cancel'])
const confirming = ref(false)

async function onConfirm() {
  confirming.value = true
  try {
    await emit('confirm')
  } finally {
    confirming.value = false
  }
}

function onCancel() {
  emit('cancel')
}
</script>
