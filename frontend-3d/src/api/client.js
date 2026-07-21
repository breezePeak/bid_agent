/**
 * Thin API client — proxies to bid_agent backend via Vite (/api → :7860)
 */

async function getJson(url) {
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 120)}`)
  }
  return res.json()
}

export async function fetchStatus() {
  return getJson('/api/status')
}

export async function fetchAgentActivity() {
  return getJson('/api/agent/activity')
}

export async function fetchAgentGoal() {
  return getJson('/api/agent/goal')
}

export async function fetchRuns() {
  return getJson('/api/runs')
}

export async function selectRun(runId) {
  const res = await fetch('/api/select-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  })
  if (!res.ok) throw new Error(`select-run failed: ${res.status}`)
  return res.json()
}

export async function probeBackend() {
  try {
    await getJson('/api/status')
    return true
  } catch {
    return false
  }
}

/** 创建工作空间并设为当前 */
export async function startRun({ name, project_type = 'software_project', expected_pages = 0 } = {}) {
  const res = await fetch('/api/start-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      name: name || `炼丹阁-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}`,
      project_type,
      expected_pages,
    }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok || data.ok === false) {
    throw new Error(data.message || `start-run failed: ${res.status}`)
  }
  return data
}

/** multipart 上传 sources/{category} */
export async function uploadFiles(category, files) {
  const list = Array.from(files || []).filter(Boolean)
  if (!list.length) throw new Error('没有可上传的文件')
  const form = new FormData()
  for (const f of list) {
    if (f instanceof File || f instanceof Blob) form.append('files', f, f.name || 'file')
    else throw new Error('无效文件对象')
  }
  const res = await fetch(`/api/upload?category=${encodeURIComponent(category)}`, {
    method: 'POST',
    body: form,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok || data.ok === false) {
    throw new Error(data.message || `upload ${category} failed: ${res.status}`)
  }
  return data
}

/** 启动后端自动流水线 */
export async function startPipeline({ start_command = '', run_id = '' } = {}) {
  const res = await fetch('/api/start-pipeline', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ start_command, run_id }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok || data.ok === false) {
    throw new Error(data.message || `start-pipeline failed: ${res.status}`)
  }
  return data
}
