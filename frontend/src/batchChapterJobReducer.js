/**
 * Pure state reducer for durable chapter batch jobs.
 * Kept independent of Vue so reconnect/replay behavior can be tested without
 * building the frontend bundle.
 */
export const initialBatchChapterJobState = () => ({
  job: null,
  items: {},
  chatByChapter: {},
  lastSequence: 0,
  eventIds: new Set(),
});

const TERMINAL_ITEMS = new Set(["succeeded", "failed", "paused", "skipped"]);

export function hydrateBatchChapterJob(state, job) {
  if (!job || typeof job !== "object") return state
  const incomingJobId = job.job_id || job.id
  const currentJobId = state.job?.job_id || state.job?.id
  const sameJob = !currentJobId || currentJobId === incomingJobId
  const base = sameJob ? state : initialBatchChapterJobState()
  const items = { ...base.items }
  for (const item of job.items || []) {
    if (!item?.chapter_id) continue
    items[item.chapter_id] = {
      ...(items[item.chapter_id] || {}),
      chapterId: item.chapter_id,
      chapterTitle: item.chapter_title || item.chapter_id,
      status: item.status || "queued",
      stage: item.stage || "queued",
      error: item.error || null,
      contentRevision: Number(item.content_revision || 0),
      attempt: Number(item.attempt || 0),
    }
  }
  return { ...base, job: { ...job, id: incomingJobId }, items }
}

function itemFor(state, event) {
  const id = event.chapter_id;
  if (!id) return null;
  const previous = state.items[id] || {
    chapterId: id,
    chapterTitle: event.chapter_title || id,
    status: "queued",
    stage: "queued",
    messages: [],
  };
  return {
    ...previous,
    chapterTitle: event.chapter_title || previous.chapterTitle,
  };
}

function chatMessage(event) {
  const error = event.error;
  return {
    id: event.event_id || `${event.sequence}:${event.type}`,
    type: event.type,
    stage: event.stage,
    text: error?.message || event.message || event.data?.text || "",
    error: error || null,
    sequence: event.sequence,
  };
}

export function reduceBatchChapterJob(state, event) {
  if (!event || typeof event !== "object") return state;
  const eventId = event.event_id;
  if (eventId && state.eventIds.has(eventId)) return state;
  if (event.sequence != null && event.sequence <= state.lastSequence) return state;

  const next = {
    ...state,
    eventIds: new Set(state.eventIds),
    items: { ...state.items },
    chatByChapter: { ...state.chatByChapter },
    lastSequence: Math.max(state.lastSequence || 0, event.sequence || 0),
  };
  if (eventId) next.eventIds.add(eventId);

  if (event.job_id && !next.job) next.job = { id: event.job_id };
  if (event.type === "job_created" || event.type === "job_started") {
    next.job = { ...(next.job || {}), ...event.data, id: event.job_id || next.job?.id, status: event.status || "running" };
  }
  if (["job_paused", "job_completed", "job_failed", "job_cancelled"].includes(event.type)) {
    const terminalStatus = event.type === "job_completed" ? "succeeded" : event.type.replace("job_", "")
    next.job = { ...(next.job || {}), status: terminalStatus };
  }

  const item = itemFor(next, event);
  if (item) {
    const incomingAttempt = Number(event.data?.attempt ?? item.attempt ?? 0)
    const currentAttempt = Number(item.attempt || 0)
    if (incomingAttempt < currentAttempt) return next
    const preserveTerminal = TERMINAL_ITEMS.has(item.status) && incomingAttempt <= currentAttempt
    const status = event.status || (event.type === "chapter_committed" ? "succeeded" : undefined);
    next.items[item.chapterId] = {
      ...item,
      status: preserveTerminal ? item.status : (status || item.status),
      stage: preserveTerminal ? item.stage : (event.stage || item.stage),
      error: event.error || (event.type === "chapter_failed" ? event.data?.error : item.error) || null,
      contentRevision: event.data?.head_content_revision ?? event.data?.content_revision ?? item.contentRevision,
      attempt: incomingAttempt,
    };
    if (!preserveTerminal && event.type !== "chapter_queued" && !TERMINAL_ITEMS.has(next.items[item.chapterId].status)) {
      next.items[item.chapterId].status = event.status || "running";
    }
    const chat = next.chatByChapter[item.chapterId] || [];
    if (event.message || event.data?.text || event.error || ["thinking_delta", "research_result", "draft_delta", "chapter_failed", "chapter_committed"].includes(event.type)) {
      next.chatByChapter[item.chapterId] = [...chat, chatMessage(event)];
    }
  }
  return next;
}

export function reduceBatchChapterJobEvents(state, events) {
  return (events || []).reduce(reduceBatchChapterJob, state);
}
