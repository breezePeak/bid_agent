import { STAGE_DEFS } from '../config/stages.js'

/**
 * Offline demo: simulates a full pipeline run with agent activity.
 */

const ROLE_POOL = [
  { role: 'chapter_writer', color: 'blue', emoji: '✍️', label: '写作 Agent' },
  { role: 'chapter_reviewer', color: 'purple', emoji: '🔍', label: '审核 Agent' },
  { role: 'chapter_rewriter', color: 'orange', emoji: '📝', label: '改稿 Agent' },
  { role: 'global_reviewer', color: 'teal', emoji: '📋', label: '全文审核' },
]

function nowIso() {
  return new Date().toISOString()
}

export function createDemoController(store) {
  let tick = 0
  let timer = null
  let chapterCount = 5

  function buildStatus() {
    const stageIndex = Math.min(Math.floor(tick / 8), STAGE_DEFS.length)
    const sub = tick % 8
    const running = stageIndex < STAGE_DEFS.length
    const current = running ? STAGE_DEFS[stageIndex] : null

    const workflow = STAGE_DEFS.map((def, i) => {
      let state = 'pending'
      let done = false
      let ready = false
      let message = ''
      if (i < stageIndex) {
        state = 'done'
        done = true
        ready = true
        message = '已完成'
      } else if (i === stageIndex && running) {
        state = 'running'
        ready = true
        message = `执行中… ${Math.min(99, sub * 12 + 8)}%`
      } else if (i === stageIndex + 1) {
        state = 'ready'
        ready = true
        message = '就绪'
      } else if (i > 0 && i < stageIndex) {
        state = 'done'
        done = true
      }
      return {
        id: def.id,
        label: def.label,
        command: def.command,
        kind: def.id === 'init_workspace' ? 'utility' : 'core',
        state,
        done,
        ready,
        message,
        duration_label: done ? `${(8 + (i % 5) * 3).toFixed(1)}s` : '',
      }
    })

    const phase = current?.id || 'idle'
    const phaseLabel = current?.label || '待命'
    const isWrite = current?.id === 'write_chapters'
    const isReview = current?.id === 'review_fix_chapters'
    const isGlobal = current?.id === 'global_review'
    const isSelect = current?.id === 'select_contexts'

    const agents = [
      {
        id: 'coordinator:main',
        role: 'coordinator',
        label: '掌炉真人',
        emoji: '☯',
        color: 'gold',
        chapter_id: '',
        status: 'running',
        message: running ? `掌炉 · ${phaseLabel}` : '静守炉前 · 候令',
        is_coordinator: true,
      },
    ]

    // 始终保留若干列队道众，形成红毯朝班
    const queueRoles = [
      { role: 'chapter_writer', label: '炼丹弟子' },
      { role: 'chapter_reviewer', label: '验丹长老' },
      { role: 'chapter_rewriter', label: '改火道人' },
      { role: 'outline_generator', label: '立鼎师' },
      { role: 'chapter_context_selector', label: '采药童子' },
      { role: 'tender_requirement_extractor', label: '辨材道人' },
    ]
    for (let q = 0; q < 6; q++) {
      const rr = queueRoles[q % queueRoles.length]
      agents.push({
        id: `queue:${rr.role}:${q}`,
        role: rr.role,
        label: rr.label,
        emoji: '☯',
        chapter_id: '',
        status: 'queued',
        message: '丹墀列队 · 候传',
      })
    }

    if (isWrite || isSelect) {
      for (let c = 1; c <= chapterCount; c++) {
        const cid = String(c).padStart(2, '0')
        const slot = (tick + c) % 10
        let status = 'queued'
        let message = '丹墀列队'
        if (slot < 3) {
          status = 'running'
          message = isWrite ? `阁内炼制第 ${cid} 章…` : `阁内选药 ${cid}`
        } else if (slot < 6) {
          status = 'done'
          message = '退朝歇息'
        } else if (slot === 9 && c === chapterCount) {
          status = 'failed'
          message = '炸炉 · 重炼'
        }
        agents.push({
          id: `${isWrite ? 'chapter_writer' : 'chapter_context_selector'}:${cid}`,
          role: isWrite ? 'chapter_writer' : 'chapter_context_selector',
          label: isWrite ? '炼丹弟子' : '采药童子',
          emoji: isWrite ? '🔥' : '🌿',
          chapter_id: cid,
          status,
          message,
          attempt: status === 'failed' ? 2 : 1,
        })
      }
    } else if (isReview) {
      for (let c = 1; c <= chapterCount; c++) {
        const cid = String(c).padStart(2, '0')
        const slot = (tick + c * 2) % 9
        const role = slot < 3 ? 'chapter_reviewer' : slot < 5 ? 'chapter_rewriter' : 'chapter_reviewer'
        const meta = ROLE_POOL.find((r) => r.role === role) || ROLE_POOL[0]
        let status = 'queued'
        let message = '丹墀列队'
        if (slot < 3) {
          status = 'running'
          message = role === 'chapter_rewriter' ? `阁内改火 ${cid}` : `阁内验丹 ${cid}`
        } else if (slot < 7) {
          status = 'done'
          message = '退朝'
        }
        agents.push({
          id: `${role}:${cid}`,
          role,
          label: meta.label,
          emoji: meta.emoji,
          color: meta.color,
          chapter_id: cid,
          status,
          message,
        })
      }
    } else if (isGlobal) {
      agents.push({
        id: 'global_reviewer:all',
        role: 'global_reviewer',
        label: '总坛真人',
        emoji: '📜',
        color: 'teal',
        chapter_id: 'ALL',
        status: 'running',
        message: '殿内通览全文…',
      })
    } else if (running && current?.agents?.length) {
      agents.push({
        id: `${current.agents[0]}:01`,
        role: current.agents[0],
        label: current.agents[0],
        emoji: '☯',
        color: 'slate',
        chapter_id: '01',
        status: 'running',
        message: `${current.label} · 升殿办差`,
      })
    }

    const summary = { total: agents.length, running: 0, done: 0, failed: 0, queued: 0 }
    for (const a of agents) {
      if (summary[a.status] != null) summary[a.status] += 1
    }

    const doneChapters = Math.min(chapterCount, Math.floor(stageIndex / 2))

    return {
      running,
      current_task: current?.command || '',
      active_run: { id: 'demo-run', name: '演示标书 · 指挥舱' },
      project_profile: { project_type: 'software_project' },
      run_state: {
        stage: current?.id || 'idle',
        status: running ? 'running' : 'ok',
        message: running ? `Demo 执行 ${phaseLabel}` : '演示流程已完成，即将循环',
        updated_at: nowIso(),
      },
      workflow,
      workspace: {
        jobs_count: stageIndex >= 8 ? chapterCount : 0,
        contexts_count: stageIndex >= 9 ? chapterCount : 0,
        chapters_count: stageIndex >= 10 ? doneChapters : 0,
        reviews_count: stageIndex >= 11 ? doneChapters : 0,
        outline: stageIndex >= 7,
        score_points: stageIndex >= 3,
        global_facts: stageIndex >= 4,
      },
      outputs: {
        final_md: stageIndex >= 18,
        final_docx: stageIndex >= 19,
      },
      agent_activity: {
        status: running ? 'running' : 'idle',
        phase,
        phase_label: phaseLabel,
        summary,
        agents,
        materials_deferred: stageIndex >= 5 && stageIndex < 12 ? 2 : 0,
      },
      materials_summary: { deferred: stageIndex >= 5 && stageIndex < 12 ? 2 : 0 },
      issues_summary: { open: stageIndex >= 11 && stageIndex < 17 ? 1 : 0 },
      run_events_tail: [
        {
          ts: nowIso(),
          stage: current?.id || 'idle',
          event_type: running ? 'progress' : 'success',
          message: running ? `${phaseLabel} 推进中` : '全流程完成',
        },
        {
          ts: nowIso(),
          stage: 'system',
          event_type: 'info',
          message: `Agent 在线 ${summary.running} · 完成 ${summary.done}`,
        },
      ],
    }
  }

  function step() {
    tick += 1
    if (tick > STAGE_DEFS.length * 8 + 12) tick = 0
    store.applyStatus(buildStatus(), { demo: true, connected: false })
  }

  return {
    start(intervalMs = 900) {
      this.stop()
      step()
      timer = setInterval(step, intervalMs)
    },
    stop() {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    },
    isRunning() {
      return Boolean(timer)
    },
  }
}
