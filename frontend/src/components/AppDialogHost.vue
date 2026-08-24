<template>
  <Teleport to="body">
    <Transition name="app-dialog" @after-leave="flushDialogQueue">
      <div
        v-if="dialogState.visible"
        class="app-dialog-overlay"
        role="presentation"
        @click.self="onCancel"
      >
        <div
          class="app-dialog"
          :class="`tone-${dialogState.tone}`"
          role="alertdialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="descId"
          @keydown="onKeydown"
        >
          <div class="app-dialog-icon" aria-hidden="true">
            <svg v-if="dialogState.tone === 'danger'" viewBox="0 0 24 24">
              <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
              <path d="M10 11v6M14 11v6" />
            </svg>
            <svg v-else-if="dialogState.tone === 'warning'" viewBox="0 0 24 24">
              <path d="M12 3 2 21h20L12 3Z" />
              <path d="M12 10v5M12 18h.01" />
            </svg>
            <svg v-else-if="dialogState.tone === 'success'" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="9" />
              <path d="m8.5 12.5 2.5 2.5 4.5-5" />
            </svg>
            <svg v-else viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 11v6M12 8h.01" />
            </svg>
          </div>
          <h2 :id="titleId" class="app-dialog-title">{{ dialogState.title }}</h2>
          <p :id="descId" class="app-dialog-message">{{ dialogState.message }}</p>
          <div class="app-dialog-actions">
            <button
              v-if="dialogState.type === 'confirm'"
              ref="cancelRef"
              type="button"
              class="btn"
              @click="onCancel"
            >
              {{ dialogState.cancelText }}
            </button>
            <button
              ref="confirmRef"
              type="button"
              class="btn"
              :class="confirmButtonClass"
              @click="onConfirm"
            >
              {{ dialogState.confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { dialogState, flushDialogQueue, settleDialog } from '../composables/appDialog.js'

const cancelRef = ref(null)
const confirmRef = ref(null)
let previousFocus = null

const titleId = computed(() => `app-dialog-title-${dialogState.id}`)
const descId = computed(() => `app-dialog-desc-${dialogState.id}`)
const confirmButtonClass = computed(() => {
  if (dialogState.tone === 'danger') return 'btn-danger'
  if (dialogState.tone === 'warning') return 'btn-primary'
  return 'btn-primary'
})

watch(
  () => dialogState.visible,
  async (visible) => {
    if (!visible) return
    previousFocus = document.activeElement
    await nextTick()
    const focusTarget = dialogState.type === 'confirm' && dialogState.tone === 'danger'
      ? cancelRef.value
      : confirmRef.value
    focusTarget?.focus()
  },
)

watch(
  () => dialogState.visible,
  (visible, wasVisible) => {
    if (wasVisible && !visible) {
      const target = previousFocus
      previousFocus = null
      if (target && typeof target.focus === 'function') {
        queueMicrotask(() => target.focus())
      }
    }
  },
)

function onConfirm() {
  settleDialog(true)
}

function onCancel() {
  settleDialog(dialogState.type === 'alert')
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    onCancel()
    return
  }
  if (event.key === 'Enter' && event.target?.tagName !== 'TEXTAREA' && event.target?.tagName !== 'BUTTON') {
    event.preventDefault()
    onConfirm()
    return
  }
  if (event.key !== 'Tab') return
  const buttons = [cancelRef.value, confirmRef.value].filter(Boolean)
  if (buttons.length < 2) {
    event.preventDefault()
    buttons[0]?.focus()
    return
  }
  const currentIndex = buttons.indexOf(document.activeElement)
  event.preventDefault()
  if (event.shiftKey) {
    buttons[(currentIndex <= 0 ? buttons.length : currentIndex) - 1].focus()
  } else {
    buttons[(currentIndex + 1) % buttons.length].focus()
  }
}
</script>
