export const V3_WORKSPACES_PATH = '/v3/workspaces'

function requireText(value, fieldName) {
  const text = String(value ?? '').trim()
  if (!text) throw new TypeError(`${fieldName} is required`)
  return text
}

function objectOrEmpty(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function arrayOrEmpty(value) {
  return Array.isArray(value) ? value : []
}

export function v3WorkspacePath(runId, resource = '') {
  const encodedRunId = encodeURIComponent(requireText(runId, 'runId'))
  const normalizedResource = String(resource ?? '').replace(/^\/+|\/+$/g, '')
  return normalizedResource
    ? `${V3_WORKSPACES_PATH}/${encodedRunId}/${normalizedResource}`
    : `${V3_WORKSPACES_PATH}/${encodedRunId}`
}

export function normalizeV3WorkspaceSnapshot(payload) {
  const envelope = objectOrEmpty(payload)
  const raw = objectOrEmpty(
    Object.prototype.hasOwnProperty.call(envelope, 'snapshot')
      ? envelope.snapshot
      : envelope,
  )
  const inputs = objectOrEmpty(raw.inputs)
  const quality = objectOrEmpty(raw.quality)
  const materials = objectOrEmpty(raw.materials)
  const revision = Number(raw.workspace_revision)

  return {
    ...raw,
    workspace_revision: Number.isFinite(revision) && revision >= 0 ? revision : 0,
    inputs: {
      ...inputs,
      inputs: arrayOrEmpty(inputs.inputs),
    },
    document: objectOrEmpty(raw.document),
    content_units: arrayOrEmpty(raw.content_units),
    quality: {
      ...quality,
      report: objectOrEmpty(quality.report),
    },
    materials: {
      ...materials,
      summary: objectOrEmpty(materials.summary),
      items: arrayOrEmpty(materials.items),
    },
    evidence_needs: arrayOrEmpty(raw.evidence_needs),
  }
}

export function workspaceRevisionFromV3Payload(payload) {
  return normalizeV3WorkspaceSnapshot(payload).workspace_revision
}

export function buildV3Command({
  commandId,
  kind,
  payload = {},
  expectedRevision = 0,
}) {
  const normalizedCommandId = requireText(commandId, 'commandId')
  const normalizedKind = requireText(kind, 'kind')
  const revision = Number(expectedRevision)
  if (!Number.isInteger(revision) || revision < 0) {
    throw new TypeError('expectedRevision must be a non-negative integer')
  }

  return {
    command_id: normalizedCommandId,
    kind: normalizedKind,
    payload: objectOrEmpty(payload),
    expected_revision: revision,
    idempotency_key: normalizedCommandId,
  }
}

export function buildRunPipelineCommand(commandId, expectedRevision) {
  return buildV3Command({
    commandId,
    kind: 'document.run_pipeline',
    payload: {},
    expectedRevision,
  })
}

export function buildResearchResolveCommand(
  commandId,
  expectedRevision,
  needId,
  attachmentInputIds = [],
) {
  return buildV3Command({
    commandId,
    kind: 'research.resolve',
    payload: {
      need_id: requireText(needId, 'needId'),
      provider_id: 'deepseek_web',
      attachment_input_ids: [...arrayOrEmpty(attachmentInputIds)],
    },
    expectedRevision,
  })
}

export function isDeepSeekEligibleInput(item) {
  return Boolean(
    item
    && item.active
    && /\.(pdf|docx?|txt|md|csv|xlsx?|pptx?|png|jpe?g|webp)$/i.test(String(item.filename || '')),
  )
}

export function selectDeepSeekAttachmentIds(inputs, selectedInputIds) {
  const eligibleIds = new Set(
    arrayOrEmpty(inputs)
      .filter(isDeepSeekEligibleInput)
      .map(item => String(item.input_id || '').trim())
      .filter(Boolean),
  )
  return [...new Set(
    arrayOrEmpty(selectedInputIds)
      .map(inputId => String(inputId || '').trim())
      .filter(inputId => eligibleIds.has(inputId)),
  )]
}
