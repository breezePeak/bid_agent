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
