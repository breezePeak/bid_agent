import { reactive } from 'vue'

const DEFAULTS = {
  type: 'confirm',
  tone: 'info',
  title: '请确认',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
}

export const dialogState = reactive({
  visible: false,
  id: 0,
  ...DEFAULTS,
})

let resolver = null
const queue = []

function openJob(job) {
  resolver = job.resolve
  Object.assign(dialogState, DEFAULTS, job.options, {
    visible: true,
    id: dialogState.id + 1,
  })
}

function present(options) {
  return new Promise((resolve) => {
    const job = { options, resolve }
    if (dialogState.visible || resolver) {
      queue.push(job)
      return
    }
    openJob(job)
  })
}

export function settleDialog(result) {
  if (!dialogState.visible && !resolver) return
  const resolve = resolver
  resolver = null
  dialogState.visible = false
  resolve?.(result)
}

export function flushDialogQueue() {
  if (dialogState.visible || resolver || !queue.length) return
  openJob(queue.shift())
}

export function confirmDialog(options) {
  const opts = typeof options === 'string' ? { message: options } : { ...options }
  return present({
    type: 'confirm',
    tone: opts.tone || 'danger',
    title: opts.title || '请确认',
    confirmText: opts.confirmText || '确定',
    cancelText: opts.cancelText || '取消',
    ...opts,
  })
}

export function alertDialog(options) {
  const opts = typeof options === 'string' ? { message: options } : { ...options }
  return present({
    type: 'alert',
    tone: opts.tone || 'info',
    title: opts.title || '提示',
    confirmText: opts.confirmText || '知道了',
    cancelText: '',
    ...opts,
  })
}
