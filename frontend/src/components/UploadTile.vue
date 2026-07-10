<template>
  <div class="upload-tile" :class="{ filled: files.length > 0 }" @click="openPicker">
    <div class="upload-tile-icon">
      <span v-if="files.length">&#x2705;</span>
      <span v-else>&#x1F4C4;</span>
    </div>
    <div class="upload-tile-label">{{ label }}</div>
    <div class="upload-tile-hint" v-if="!files.length">点击上传</div>
    <div class="upload-tile-files" v-else>
      <span v-for="f in files.slice(0, 3)" :key="f" class="upload-tile-file">{{ f }}</span>
      <span v-if="files.length > 3" class="upload-tile-more">+{{ files.length - 3 }}</span>
    </div>
    <input type="file" ref="fileInput" hidden multiple @change="onChange" />
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  label: { type: String, default: '' },
  category: { type: String, default: '' },
  files: { type: Array, default: () => [] },
})

const emit = defineEmits(['upload'])
const fileInput = ref(null)

function openPicker() {
  if (fileInput.value) fileInput.value.click()
}

function onChange(e) {
  if (e.target.files.length) {
    emit('upload', props.category, e.target.files)
  }
  e.target.value = ''
}
</script>
