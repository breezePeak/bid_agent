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

function artifactPayload(artifacts, kind) {
  const artifact = arrayOrEmpty(artifacts).find(item => item?.artifact_kind === kind)
  return objectOrEmpty(artifact?.payload)
}

function uniqueText(values) {
  return [...new Set(arrayOrEmpty(values).map(value => String(value || '').trim()).filter(Boolean))]
}

const SCORE_INTEGRITY_CODE = 'V3_SCORE_INTEGRITY_BLOCKED'
const REQUIREMENT_COVERAGE_CODE = 'V3_REQUIREMENT_COVERAGE_BLOCKED'
const SCORE_SEMANTIC_ERROR_PREFIX = 'score_semantic_'
const SCORE_AUDIT_ISSUES = [
  ['unlinked_score_point_ids', '未关联招标需求'],
  ['invalid_anchor_score_point_ids', '来源引用无效'],
  ['unknown_requirement_ids', '引用了未知需求'],
  ['mismatched_requirement_ids', '需求引用不一致'],
  ['bulk_linked_score_point_ids', '批量关联异常'],
  ['semantic_incomplete_score_point_ids', '评分语义拆分不完整'],
  ['invalid_condition_source_ids', '满分条件来源无效'],
  ['invalid_condition_identity_ids', '满分条件稳定ID或来源锚点无效'],
  ['duplicate_condition_ids', '满分条件ID重复'],
]

function scoreAuditIssueCount(audit, message, field) {
  if (Array.isArray(audit[field])) return audit[field].length
  const escapedField = field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const matched = String(message || '').match(
    new RegExp(`['"]?${escapedField}['"]?\\s*:\\s*\\[([^\\]]*)\\]`),
  )
  if (!matched) return 0
  const quotedItems = matched[1].match(/'[^']*'|"[^"]*"/g)
  if (quotedItems) return quotedItems.length
  return matched[1].split(',').filter(item => item.trim()).length
}

function commandErrorParts(cause) {
  const responsePayload = objectOrEmpty(cause?.response?.data)
  const commandPayload = objectOrEmpty(cause?.v3Payload)
  const payload = Object.keys(responsePayload).length
    ? responsePayload
    : (Object.keys(commandPayload).length ? commandPayload : objectOrEmpty(cause))
  const receipt = objectOrEmpty(payload.receipt)
  const receiptError = objectOrEmpty(receipt.error)
  const topError = objectOrEmpty(payload.error)
  const structuredError = Object.keys(receiptError).length ? receiptError : topError
  return {
    code: String(structuredError.code || payload.code || '').trim(),
    details: objectOrEmpty(structuredError.details || payload.details),
    message: String(
      structuredError.message
      || receipt.message
      || payload.message
      || cause?.message
      || '',
    ).trim(),
  }
}

function semanticIssueDescription(rawReason) {
  const reason = String(rawReason || '')
    .replace(/^独立得分单元\s+\S+\s+/, '')
    .replace(/^得分单元\s+\S+\s+/, '')
    .replace(/SP-[a-f0-9]+-L\d+/gi, '最高分档')
    .trim()
  if (reason.includes('full 档次必须等于')) {
    return reason.includes('actual=[]')
      ? '没有将最高分档标记为满分。'
      : '将多个或非最高档评分档次标记为满分；只能保留最高分档。'
  }
  if (reason.includes('缺少满分原子条件')) {
    return '未拆出可追溯的满分原子条件。'
  }
  if (reason.includes('未无损覆盖最高档')) {
    const missing = (reason.match(/遗漏：(.+)$/) || [])[1]
    return missing
      ? `满分条件遗漏：${missing.trim()}`
      : '满分条件没有完整覆盖最高分档要求。'
  }
  if (reason.includes('source_excerpt') || reason.includes('SourceBlock')) {
    return '满分条件的原文引用或字符位置无效。'
  }
  return reason.replace(/^ValueError:\s*/, '') || '评分语义校验未通过。'
}

export function v3ErrorDetails(cause) {
  const { details: errorMetadata, message } = commandErrorParts(cause)
  const renderedDetails = []
  const seen = new Set()
  const append = (title, description) => {
    const normalizedTitle = String(title || '').trim()
    const normalizedDescription = String(description || '').trim()
    const key = `${normalizedTitle}\u0000${normalizedDescription}`
    if (!normalizedTitle || !normalizedDescription || seen.has(key)) return
    seen.add(key)
    renderedDetails.push({ title: normalizedTitle, description: normalizedDescription })
  }
  const diagnosticTexts = arrayOrEmpty(errorMetadata.diagnostics)
    .map(item => (
      typeof item === 'string'
        ? item
        : String(objectOrEmpty(item).message || objectOrEmpty(item).reason || '').trim()
    ))
    .filter(Boolean)
  for (const rawText of uniqueText([...diagnosticTexts, message])) {
    // Batch orchestration prefixes each persisted diagnostic with
    // "SSB-…/<group>:".  Remove only that envelope so the score-point title
    // and the original validation reason remain available to the UI.
    const text = rawText.replace(/^SSB-[^/;]+\/[^:;]+:\s*/, '')
    const rulePattern = /(?:^|;\s*)SP-[^/;]+\/([^:;]+):\s*ValueError:\s*([\s\S]*?)(?=;\s*(?:SP-[^/;]+\/|ValueError:)|$)/g
    let matched
    while ((matched = rulePattern.exec(text)) !== null) {
      append(matched[1], semanticIssueDescription(matched[2]))
    }
    const missingRulePattern = /(?:^|;\s*)ValueError:\s*SP-[^/;]+\/(.+?)\s+(缺少评分语义 interpretation|出现 \d+ 次评分语义 interpretation)(?=;|$)/g
    while ((matched = missingRulePattern.exec(text)) !== null) {
      append(matched[1], semanticIssueDescription(matched[2]))
    }
    const jsonPattern = /JSONDecodeError:\s*([^;]+)/g
    while ((matched = jsonPattern.exec(text)) !== null) {
      const reason = String(matched[1] || '')
      const character = (reason.match(/\(char\s+(\d+)\)/i) || [])[1]
      append(
        '首次模型输出',
        character
          ? `返回的 JSON 格式不合法，第 ${character} 个字符附近缺少逗号、引号或分隔符。`
          : '返回的 JSON 格式不合法，无法进入评分语义校验。',
      )
    }
    const unitPattern = /(?:^|;\s*)ValueError:\s*((?:独立)?得分单元)\s+(\S+)\s+([^;]+)/g
    while ((matched = unitPattern.exec(text)) !== null) {
      append(
        `${matched[1]} ${matched[2]}`,
        semanticIssueDescription(`${matched[1]} ${matched[2]} ${matched[3]}`),
      )
    }
    const globalMatch = text.match(/(?:^|;\s*)ValueError:\s*([^;]+)$/)
    if (
      globalMatch
      && !/^(?:(?:独立)?得分单元\s+|SP-[^/;]+\/)/.test(globalMatch[1])
    ) {
      append('全局校验', semanticIssueDescription(globalMatch[1]))
    }
  }
  return renderedDetails
}

export function formatV3ApiError(cause, fallback) {
  const { code, details, message } = commandErrorParts(cause)
  const semanticCode = code.startsWith(SCORE_SEMANTIC_ERROR_PREFIX)
    ? code
    : (message.match(/score_semantic_[a-z_]+/) || [])[0]
  if (semanticCode || message.includes('评分语义推理失败')) {
    const diagnosticCode = semanticCode || 'V3_SCORE_SEMANTIC_BLOCKED'
    return `评分理解批次未通过结构或来源校验（错误码：${diagnosticCode}）。流水线已在评分理解阶段停止，评分目录尚未生成。`
  }
  const isRequirementCoverageError = code === REQUIREMENT_COVERAGE_CODE
    || message.includes('RequirementLedger 未覆盖 SourceBlock')
    || message.includes('RequirementLedger 需求覆盖校验失败')
  if (isRequirementCoverageError) {
    const audit = objectOrEmpty(details.coverage_audit || details.audit || details)
    const count = scoreAuditIssueCount(audit, message, 'missing_chunk_ids')
    const diagnosticCode = code || REQUIREMENT_COVERAGE_CODE
    const summary = count > 0
      ? `发现 ${count} 个来源片段未被需求台账覆盖。`
      : '需求台账未完整覆盖招标来源。'
    return `需求覆盖校验未通过（错误码：${diagnosticCode}）。${summary}请重新解析；若仍失败，请检查目录页、日期行和扫描件识别结果。`
  }
  const isScoreIntegrityError = code === SCORE_INTEGRITY_CODE
    || message.includes('ScoreModel 引用审计失败')
  if (!isScoreIntegrityError) return message || fallback

  const audit = objectOrEmpty(details.score_audit || details.audit || details)
  const issues = SCORE_AUDIT_ISSUES
    .map(([field, label]) => [scoreAuditIssueCount(audit, message, field), label])
    .filter(([count]) => count > 0)
    .map(([count, label]) => `${label} ${count} 个`)
  const diagnosticCode = code || SCORE_INTEGRITY_CODE
  const summary = issues.length
    ? `发现${issues.join('、')}。`
    : '评分点与招标需求的引用关系未通过校验。'
  return `评分点引用校验未通过（错误码：${diagnosticCode}）。${summary}请重新解析评分点；若仍失败，请检查评分表的行、合并单元格和分值列。`
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
  const document = objectOrEmpty(raw.document)
  const promotedArtifacts = arrayOrEmpty(raw.promoted_artifacts)
  const rawAnalysis = objectOrEmpty(raw.analysis)
  const rawPipeline = objectOrEmpty(rawAnalysis.pipeline)
  const rawGeneration = objectOrEmpty(raw.generation)
  const rawGenerationContent = objectOrEmpty(rawGeneration.content)
  const rawGenerationResearch = objectOrEmpty(rawGeneration.research)
  const rawWorkflow = objectOrEmpty(raw.workflow)
  const rawGlobalProjectContext = objectOrEmpty(raw.global_project_context)
  const rawProfile = objectOrEmpty(raw.profile)
  const rawLegacyBid = objectOrEmpty(raw.legacy_bid)
  const rawMaterialReadiness = objectOrEmpty(raw.material_readiness)
  const globalProjectContext = {
    ...rawGlobalProjectContext,
    identity: objectOrEmpty(rawGlobalProjectContext.identity),
    background: arrayOrEmpty(rawGlobalProjectContext.background),
    goals: arrayOrEmpty(rawGlobalProjectContext.goals),
    scope: arrayOrEmpty(rawGlobalProjectContext.scope),
    boundaries: arrayOrEmpty(rawGlobalProjectContext.boundaries),
    work_packages: arrayOrEmpty(rawGlobalProjectContext.work_packages),
    inputs: arrayOrEmpty(rawGlobalProjectContext.inputs),
    processing: arrayOrEmpty(rawGlobalProjectContext.processing),
    outputs: arrayOrEmpty(rawGlobalProjectContext.outputs),
    deliverables: arrayOrEmpty(rawGlobalProjectContext.deliverables),
    acceptance_conditions: arrayOrEmpty(rawGlobalProjectContext.acceptance_conditions),
    milestones: arrayOrEmpty(rawGlobalProjectContext.milestones),
    constraints: arrayOrEmpty(rawGlobalProjectContext.constraints),
    confirmed_facts: arrayOrEmpty(rawGlobalProjectContext.confirmed_facts),
    terminology: objectOrEmpty(rawGlobalProjectContext.terminology),
  }
  const analysis = {
    ...rawAnalysis,
    source_index: objectOrEmpty(
      rawAnalysis.source_index || artifactPayload(promotedArtifacts, 'SourceIndex'),
    ),
    requirement_ledger: objectOrEmpty(
      rawAnalysis.requirement_ledger || artifactPayload(promotedArtifacts, 'RequirementLedger'),
    ),
    score_model: objectOrEmpty(
      rawAnalysis.score_model || artifactPayload(promotedArtifacts, 'ScoreModel'),
    ),
    legacy_topic_graph: objectOrEmpty(
      rawAnalysis.topic_graph || artifactPayload(promotedArtifacts, 'ResponseTopicGraph'),
    ),
    chapter_blueprint: objectOrEmpty(
      rawAnalysis.chapter_blueprint
      || document.plan
      || artifactPayload(promotedArtifacts, 'ChapterBlueprint'),
    ),
    stale: rawAnalysis.stale === true,
    stale_artifact_kinds: arrayOrEmpty(rawAnalysis.stale_artifact_kinds),
    artifact_states: objectOrEmpty(rawAnalysis.artifact_states),
    latest_operation: objectOrEmpty(rawAnalysis.latest_operation),
    pipeline: {
      ...rawPipeline,
      stages: arrayOrEmpty(rawPipeline.stages).map(stage => ({
        ...objectOrEmpty(stage),
        llm_request_count: Number(objectOrEmpty(stage).llm_request_count) || 0,
        llm_requests: arrayOrEmpty(objectOrEmpty(stage).llm_requests),
        warnings: arrayOrEmpty(objectOrEmpty(stage).warnings),
        warning_count: Number(objectOrEmpty(stage).warning_count) || 0,
      })),
      products: arrayOrEmpty(rawPipeline.products),
    },
  }
  const revision = Number(raw.workspace_revision)

  return {
    ...raw,
    workspace_revision: Number.isFinite(revision) && revision >= 0 ? revision : 0,
    profile: {
      ...rawProfile,
      project_mode: String(rawProfile.project_mode || 'full_write'),
    },
    legacy_bid: {
      ...rawLegacyBid,
      status: String(rawLegacyBid.status || 'not_uploaded'),
      active_id: String(rawLegacyBid.active_id || ''),
      filename: String(rawLegacyBid.filename || ''),
      section_count: Number(rawLegacyBid.section_count) || 0,
      block_count: Number(rawLegacyBid.block_count) || 0,
      needs_review_count: Number(rawLegacyBid.needs_review_count) || 0,
    },
    material_readiness: {
      ...rawMaterialReadiness,
      ready: rawMaterialReadiness.ready === true,
      required: arrayOrEmpty(rawMaterialReadiness.required),
      items: objectOrEmpty(rawMaterialReadiness.items),
    },
    inputs: {
      ...inputs,
      inputs: arrayOrEmpty(inputs.inputs),
    },
    promoted_artifacts: promotedArtifacts,
    global_project_context: globalProjectContext,
    analysis,
    planning: objectOrEmpty(raw.planning),
    workflow: {
      ...rawWorkflow,
      phase: String(rawWorkflow.phase || 'materials'),
      status: String(rawWorkflow.status || 'not_started'),
      operation_id: String(rawWorkflow.operation_id || ''),
      attempt: Number(rawWorkflow.attempt) || 0,
      current_stage_id: String(rawWorkflow.current_stage_id || ''),
      can_resume: rawWorkflow.can_resume === true,
      stages: arrayOrEmpty(rawWorkflow.stages).map(stage => ({
        ...objectOrEmpty(stage),
        attempt: Number(objectOrEmpty(stage).attempt) || 0,
        llm_request_count: Number(objectOrEmpty(stage).llm_request_count) || 0,
        llm_requests: arrayOrEmpty(objectOrEmpty(stage).llm_requests),
      })),
      pending_reviews: arrayOrEmpty(rawWorkflow.pending_reviews).map(review => ({
        ...objectOrEmpty(review),
        review_id: String(objectOrEmpty(review).review_id || ''),
        kind: String(objectOrEmpty(review).kind || ''),
        status: String(objectOrEmpty(review).status || 'pending'),
        items: arrayOrEmpty(objectOrEmpty(review).items),
      })),
    },
    document,
    generation: {
      ...rawGeneration,
      stages: arrayOrEmpty(rawGeneration.stages).map(stage => ({
        ...objectOrEmpty(stage),
        summary: objectOrEmpty(objectOrEmpty(stage).summary),
        warnings: arrayOrEmpty(objectOrEmpty(stage).warnings),
        warning_count: Number(objectOrEmpty(stage).warning_count) || 0,
      })),
      content: {
        ...rawGenerationContent,
        total_units: Number(rawGenerationContent.total_units) || 0,
        completed_units: Number(rawGenerationContent.completed_units) || 0,
        running_units: Number(rawGenerationContent.running_units) || 0,
        failed_units: Number(rawGenerationContent.failed_units) || 0,
        stale_units: Number(rawGenerationContent.stale_units) || 0,
        blocked_units: Number(rawGenerationContent.blocked_units) || 0,
        units: arrayOrEmpty(rawGenerationContent.units).map(unit => ({
          ...objectOrEmpty(unit),
          stale: objectOrEmpty(unit).stale === true,
          stale_reason: String(objectOrEmpty(unit).stale_reason || ''),
          blocked_human: objectOrEmpty(unit).blocked_human === true,
          writer_fingerprint: String(objectOrEmpty(unit).writer_fingerprint || ''),
        })),
      },
      research: {
        ...rawGenerationResearch,
        calls: arrayOrEmpty(rawGenerationResearch.calls).map(call => ({
          ...objectOrEmpty(call),
          applicable_chapter_ids: arrayOrEmpty(objectOrEmpty(call).applicable_chapter_ids),
          applicable_chapter_titles: arrayOrEmpty(objectOrEmpty(call).applicable_chapter_titles),
          prohibited_research_scopes: arrayOrEmpty(objectOrEmpty(call).prohibited_research_scopes),
          queries: arrayOrEmpty(objectOrEmpty(call).queries).map(query => ({
            ...objectOrEmpty(query),
            target_node_ids: arrayOrEmpty(objectOrEmpty(query).target_node_ids),
            attempts: arrayOrEmpty(objectOrEmpty(query).attempts),
            sources: arrayOrEmpty(objectOrEmpty(query).sources),
          })),
          runtime: objectOrEmpty(objectOrEmpty(call).runtime),
          used_evidence_by_chapter: objectOrEmpty(objectOrEmpty(call).used_evidence_by_chapter),
        })),
        results: arrayOrEmpty(rawGenerationResearch.results).map(result => ({
          ...objectOrEmpty(result),
          attempts: arrayOrEmpty(objectOrEmpty(result).attempts),
        })),
        questions: arrayOrEmpty(rawGenerationResearch.questions),
      },
      delivery: objectOrEmpty(rawGeneration.delivery),
    },
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

export function projectV3Planning(payload) {
  const snapshot = normalizeV3WorkspaceSnapshot(payload)
  const analysisStatus = String(snapshot.analysis.status || '')
  const outdated = Boolean(
    snapshot.analysis.stale
    || snapshot.analysis.latest_operation.result_outdated
    || snapshot.planning.status === 'outdated'
    || ['stale', 'failed', 'outdated'].includes(analysisStatus),
  )
  const scoreModel = outdated ? {} : objectOrEmpty(snapshot.analysis.score_model)
  const topicGraph = outdated ? {} : objectOrEmpty(snapshot.analysis.legacy_topic_graph)
  const blueprint = outdated ? {} : objectOrEmpty(snapshot.analysis.chapter_blueprint)
  const scorePoints = arrayOrEmpty(scoreModel.points)
  const nodes = arrayOrEmpty(blueprint.nodes)
  const qualityGates = arrayOrEmpty(blueprint.document_quality_gates)
  const requirements = arrayOrEmpty(snapshot.analysis.requirement_ledger?.requirements)
  const requirementsById = new Map(
    requirements
      .filter(requirement => requirement?.requirement_id)
      .map(requirement => [String(requirement.requirement_id), requirement]),
  )
  const inputsById = new Map(
    arrayOrEmpty(snapshot.inputs.inputs)
      .filter(input => input?.input_id)
      .map(input => [String(input.input_id), input]),
  )
  const responseUnitOwners = new Map()
  const conditionOwners = new Map()
  const responseUnitsById = new Map()
  const conditionsById = new Map()
  const responseUnitIdsByCondition = new Map()
  for (const point of scorePoints) {
    const scoreId = String(point?.score_point_id || '')
    for (const unit of arrayOrEmpty(point?.response_units)) {
      const unitId = String(unit?.unit_id || '')
      if (!unitId) continue
      responseUnitOwners.set(unitId, scoreId)
      responseUnitsById.set(unitId, unit)
      for (const conditionId of uniqueText(unit?.condition_ids)) {
        const unitIds = responseUnitIdsByCondition.get(conditionId) || []
        responseUnitIdsByCondition.set(conditionId, uniqueText([...unitIds, unitId]))
      }
    }
    for (const condition of arrayOrEmpty(point?.score_conditions)) {
      const conditionId = String(condition?.condition_id || '')
      if (!conditionId) continue
      conditionOwners.set(conditionId, scoreId)
      conditionsById.set(conditionId, condition)
    }
  }
  const dutiesById = new Map(
    arrayOrEmpty(topicGraph.duties)
      .filter(duty => duty?.duty_id)
      .map(duty => [String(duty.duty_id), duty]),
  )
  const scorePointsById = new Map(
    scorePoints
      .filter(point => point?.score_point_id)
      .map(point => [String(point.score_point_id), point]),
  )
  const directScoreIdsByChapter = new Map()
  const responseUnitDestinations = new Map()
  const conditionDestinations = new Map()
  const appendDestination = (target, bindingId, destination) => {
    if (!bindingId) return
    const current = target.get(bindingId) || []
    const identity = destination.type === 'chapter'
      ? `chapter:${destination.chapter_id}`
      : `document_quality_gate:${destination.gate_id}`
    if (current.some(item => item.identity === identity)) return
    target.set(bindingId, [...current, { ...destination, identity }])
  }

  for (const node of nodes) {
    const chapterId = String(node?.chapter_id || '')
    if (!chapterId) continue
    const destination = {
      type: 'chapter',
      chapter_id: chapterId,
      title: String(node?.title || chapterId),
    }
    for (const unitId of uniqueText([
      ...arrayOrEmpty(node?.primary_response_unit_ids),
      ...arrayOrEmpty(node?.supporting_response_unit_ids),
    ])) {
      appendDestination(responseUnitDestinations, unitId, destination)
    }
    for (const conditionId of uniqueText(node?.score_condition_ids)) {
      appendDestination(conditionDestinations, conditionId, destination)
    }
  }
  for (const gate of qualityGates) {
    const gateId = String(gate?.gate_id || '')
    if (!gateId) continue
    const destination = {
      type: 'document_quality_gate',
      gate_id: gateId,
      title: arrayOrEmpty(gate?.criteria)[0] || '全文质量门',
    }
    for (const unitId of uniqueText(gate?.response_unit_ids)) {
      appendDestination(responseUnitDestinations, unitId, destination)
    }
    for (const conditionId of uniqueText(gate?.score_condition_ids)) {
      appendDestination(conditionDestinations, conditionId, destination)
    }
  }

  if (String(blueprint.planning_model || 'topic_graph') === 'score_direct') {
    for (const node of nodes) {
      const chapterId = String(node?.chapter_id || '')
      if (!chapterId) continue
      const scoreIds = uniqueText([
        ...arrayOrEmpty(node?.score_point_ids),
        ...arrayOrEmpty(node?.primary_response_unit_ids)
          .map(unitId => responseUnitOwners.get(String(unitId))),
        ...arrayOrEmpty(node?.supporting_response_unit_ids)
          .map(unitId => responseUnitOwners.get(String(unitId))),
        ...arrayOrEmpty(node?.score_condition_ids)
          .map(conditionId => conditionOwners.get(String(conditionId))),
      ]).filter(scoreId => scorePointsById.has(scoreId))
      directScoreIdsByChapter.set(chapterId, scoreIds)
    }
  } else {
    for (const assignment of arrayOrEmpty(blueprint.assignments)) {
      const chapterId = String(assignment?.chapter_id || '')
      const duty = dutiesById.get(String(assignment?.duty_id || ''))
      if (!chapterId || !duty) continue
      const scoreIds = uniqueText(duty.score_point_ids)
        .filter(scoreId => scorePointsById.has(scoreId))
      if (!scoreIds.length) continue
      directScoreIdsByChapter.set(
        chapterId,
        uniqueText([...(directScoreIdsByChapter.get(chapterId) || []), ...scoreIds]),
      )
    }
  }

  const nodesById = new Map(
    nodes
      .filter(node => node?.chapter_id)
      .map(node => [String(node.chapter_id), node]),
  )
  const sourceLocation = sourceAnchor => {
    const anchor = objectOrEmpty(sourceAnchor)
    const inputId = String(anchor.source_input_id || '')
    const input = inputsById.get(inputId)
    const parts = [
      String(input?.filename || inputId || '').trim(),
      Number(anchor.page) > 0 ? `第 ${Number(anchor.page)} 页` : '',
      String(anchor.location || '').trim(),
    ].filter(Boolean)
    return {
      source_input_id: inputId,
      filename: String(input?.filename || ''),
      chunk_id: String(anchor.chunk_id || ''),
      page: Number(anchor.page) > 0 ? Number(anchor.page) : null,
      location: String(anchor.location || ''),
      label: parts.join(' · ') || '未提供来源位置',
    }
  }
  const projectRequirement = requirementId => {
    const normalizedId = String(requirementId || '')
    const requirement = requirementsById.get(normalizedId)
    if (!requirement) {
      return {
        requirement_id: normalizedId,
        original_text: '',
        normalized_requirement: '',
        source_location: sourceLocation(null),
        missing: true,
      }
    }
    return {
      ...requirement,
      source_location: sourceLocation(requirement.source_anchor),
      missing: false,
    }
  }
  const projectResponseUnit = unitIdOrValue => {
    const unit = typeof unitIdOrValue === 'object'
      ? unitIdOrValue
      : responseUnitsById.get(String(unitIdOrValue || ''))
    if (!unit) return null
    const unitId = String(unit.unit_id || '')
    return {
      ...unit,
      unit_id: unitId,
      score_point_id: responseUnitOwners.get(unitId) || '',
      destinations: arrayOrEmpty(responseUnitDestinations.get(unitId))
        .map(({ identity, ...destination }) => destination),
    }
  }
  const destinationsForCondition = conditionId => {
    const direct = arrayOrEmpty(conditionDestinations.get(conditionId))
    const resolved = direct.length
      ? direct
      : uniqueText(responseUnitIdsByCondition.get(conditionId))
        .flatMap(unitId => arrayOrEmpty(responseUnitDestinations.get(unitId)))
    const seen = new Set()
    return resolved
      .filter(destination => {
        const identity = String(destination?.identity || '')
        if (!identity || seen.has(identity)) return false
        seen.add(identity)
        return true
      })
      .map(({ identity, ...destination }) => destination)
  }
  const projectCondition = conditionIdOrValue => {
    const condition = typeof conditionIdOrValue === 'object'
      ? conditionIdOrValue
      : conditionsById.get(String(conditionIdOrValue || ''))
    if (!condition) return null
    const conditionId = String(condition.condition_id || '')
    return {
      ...condition,
      condition_id: conditionId,
      normalized_condition: String(condition.normalized_condition || condition.text || ''),
      condition_role: String(condition.condition_role || 'content'),
      source_location: sourceLocation(condition.source_anchor),
      score_point_id: conditionOwners.get(conditionId) || '',
      response_units: uniqueText(responseUnitIdsByCondition.get(conditionId))
        .map(projectResponseUnit)
        .filter(Boolean),
      destinations: destinationsForCondition(conditionId),
    }
  }
  const projectScorePoint = point => ({
    ...point,
    score_conditions: arrayOrEmpty(point?.score_conditions)
      .map(projectCondition)
      .filter(Boolean),
    response_units: arrayOrEmpty(point?.response_units)
      .map(projectResponseUnit)
      .filter(Boolean),
  })
  const childrenByParent = new Map()
  for (const node of nodesById.values()) {
    const parentId = String(node.parent_chapter_id || '')
    const resolvedParent = parentId && nodesById.has(parentId) ? parentId : ''
    const siblings = childrenByParent.get(resolvedParent) || []
    siblings.push(node)
    childrenByParent.set(resolvedParent, siblings)
  }
  for (const siblings of childrenByParent.values()) {
    siblings.sort((left, right) => {
      const orderDifference = Number(left.order || 0) - Number(right.order || 0)
      return orderDifference || String(left.title || '').localeCompare(String(right.title || ''), 'zh-CN')
    })
  }

  const visited = new Set()
  const buildNode = (node, number, depth, ancestors = new Set()) => {
    const chapterId = String(node.chapter_id)
    if (ancestors.has(chapterId)) return null
    visited.add(chapterId)
    const nextAncestors = new Set(ancestors)
    nextAncestors.add(chapterId)
    const children = arrayOrEmpty(childrenByParent.get(chapterId))
      .map((child, index) => buildNode(child, `${number}.${index + 1}`, depth + 1, nextAncestors))
      .filter(Boolean)
    const directScorePointIds = uniqueText(directScoreIdsByChapter.get(chapterId))
    const scorePointIds = uniqueText([
      ...directScorePointIds,
      ...children.flatMap(child => child.score_point_ids),
    ])
    return {
      ...node,
      number,
      depth,
      direct_score_point_ids: directScorePointIds,
      score_point_ids: scorePointIds,
      score_points: scorePointIds
        .map(scoreId => scorePointsById.get(scoreId))
        .filter(Boolean)
        .map(projectScorePoint),
      primary_response_unit_ids: uniqueText(node.primary_response_unit_ids),
      supporting_response_unit_ids: uniqueText(node.supporting_response_unit_ids),
      score_condition_ids: uniqueText(node.score_condition_ids),
      score_conditions: uniqueText(node.score_condition_ids)
        .map(projectCondition)
        .filter(Boolean),
      requirement_ids: uniqueText(node.requirement_ids),
      requirements: uniqueText(node.requirement_ids)
        .map(projectRequirement),
      children,
    }
  }

  const outline = arrayOrEmpty(childrenByParent.get(''))
    .map((node, index) => buildNode(node, String(index + 1), 1))
    .filter(Boolean)
  for (const node of nodesById.values()) {
    if (visited.has(String(node.chapter_id))) continue
    const projected = buildNode(node, String(outline.length + 1), 1)
    if (projected) outline.push(projected)
  }

  const projectedQualityGates = qualityGates.map(gate => ({
    ...gate,
    response_unit_ids: uniqueText(gate?.response_unit_ids),
    response_units: uniqueText(gate?.response_unit_ids)
      .map(projectResponseUnit)
      .filter(Boolean),
    score_point_ids: uniqueText(gate?.score_point_ids),
    score_points: uniqueText(gate?.score_point_ids)
      .map(scoreId => scorePointsById.get(scoreId))
      .filter(Boolean)
      .map(projectScorePoint),
    score_condition_ids: uniqueText(gate?.score_condition_ids),
    score_conditions: uniqueText(gate?.score_condition_ids)
      .map(projectCondition)
      .filter(Boolean),
    requirement_ids: uniqueText(gate?.requirement_ids),
    requirements: uniqueText(gate?.requirement_ids)
      .map(projectRequirement),
  }))
  const coveredResponseUnitIds = new Set([
    ...nodes.flatMap(node => uniqueText(node?.primary_response_unit_ids)),
    ...qualityGates
      .flatMap(gate => uniqueText(gate?.response_unit_ids)),
  ])
  const explicitCoveredScoreIds = new Set(
    [...directScoreIdsByChapter.values()].flatMap(value => value),
  )
  const allResponseUnits = [...responseUnitsById.values()]
  if (String(blueprint.planning_model || 'topic_graph') !== 'score_direct') {
    for (const [unitId, scoreId] of responseUnitOwners.entries()) {
      if (explicitCoveredScoreIds.has(scoreId)) coveredResponseUnitIds.add(unitId)
    }
  }
  const uncoveredResponseUnits = allResponseUnits.filter(
    unit => !coveredResponseUnitIds.has(String(unit?.unit_id || '')),
  )
  const coveredScorePointIds = new Set(
    scorePoints
      .filter(point => {
        const units = arrayOrEmpty(point?.response_units)
        return units.length
          ? units.every(unit => coveredResponseUnitIds.has(String(unit?.unit_id || '')))
          : explicitCoveredScoreIds.has(String(point?.score_point_id || ''))
      })
      .map(point => String(point.score_point_id)),
  )
  const uncoveredScorePoints = scorePoints.filter(
    point => !coveredScorePointIds.has(String(point?.score_point_id || '')),
  )
  const calculatedPoints = scorePoints
    .map(point => Number(point?.max_points))
    .filter(value => Number.isFinite(value))
    .reduce((total, value) => total + value, 0)

  return {
    outdated,
    score_model: scoreModel,
    blueprint,
    score_points: scorePoints.map(projectScorePoint),
    score_conditions: [...conditionsById.values()].map(projectCondition).filter(Boolean),
    response_units: [...responseUnitsById.values()].map(projectResponseUnit).filter(Boolean),
    requirements: requirements.map(item => projectRequirement(item.requirement_id)),
    quality_gates: projectedQualityGates,
    outline,
    uncovered_score_points: uncoveredScorePoints,
    uncovered_response_units: uncoveredResponseUnits.map(projectResponseUnit).filter(Boolean),
    summary: {
      total_points: Number.isFinite(Number(scoreModel.total_points))
        ? Number(scoreModel.total_points)
        : calculatedPoints,
      score_point_count: scorePoints.length,
      covered_score_point_count: coveredScorePointIds.size,
      uncovered_score_point_count: uncoveredScorePoints.length,
      response_unit_count: allResponseUnits.length,
      covered_response_unit_count: allResponseUnits.length - uncoveredResponseUnits.length,
      uncovered_response_unit_count: uncoveredResponseUnits.length,
      chapter_count: nodes.length,
    },
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

export function buildRunPipelineCommand(commandId, expectedRevision, chapterIds = []) {
  return buildV3Command({
    commandId,
    kind: 'document.run_pipeline',
    payload: {
      chapter_ids: [...new Set(
        arrayOrEmpty(chapterIds).map(item => String(item || '').trim()).filter(Boolean),
      )],
    },
    expectedRevision,
  })
}

export function buildPrepareOutlineCommand(commandId, expectedRevision, options = {}) {
  const reviewFeedback = String(options.reviewFeedback || '').trim()
  const baseBlueprintHash = String(options.baseBlueprintHash || '').trim()
  const projectFeedback = String(options.projectFeedback || '').trim()
  return buildV3Command({
    commandId,
    kind: 'document.prepare_outline',
    payload: {
      ...(reviewFeedback ? { review_feedback: reviewFeedback } : {}),
      ...(baseBlueprintHash ? { base_blueprint_hash: baseBlueprintHash } : {}),
      ...(projectFeedback ? { project_feedback: projectFeedback } : {}),
    },
    expectedRevision,
  })
}

export function buildConfirmPlanningCommand(commandId, expectedRevision, planningSnapshot) {
  return buildV3Command({
    commandId,
    kind: 'document.confirm_planning',
    payload: { decision: 'confirm', planning_snapshot: objectOrEmpty(planningSnapshot) },
    expectedRevision,
  })
}

export function buildResearchResolveCommand(
  commandId,
  expectedRevision,
  needId,
) {
  return buildV3Command({
    commandId,
    kind: 'research.resolve',
    payload: {
      need_id: requireText(needId, 'needId'),
      provider_id: 'tavily',
      attachment_input_ids: [],
    },
    expectedRevision,
  })
}

export function buildCreateChapterCommand(commandId, expectedRevision, chapterId, title = '', metadata = {}) {
  return buildV3Command({
    commandId,
    kind: 'chapter.workspace.create',
    payload: {
      chapter_id: requireText(chapterId, 'chapterId'),
      metadata: {
        ...(title ? { title: String(title).trim() } : {}),
        ...objectOrEmpty(metadata),
      },
    },
    expectedRevision,
  })
}

export function buildSaveChapterMetadataCommand(commandId, expectedRevision, chapterId, metadata) {
  return buildV3Command({
    commandId,
    kind: 'chapter.workspace.save_metadata',
    payload: {
      chapter_id: requireText(chapterId, 'chapterId'),
      metadata: objectOrEmpty(metadata),
    },
    expectedRevision,
  })
}

export function buildArchiveChapterCommand(commandId, expectedRevision, chapterId) {
  return buildV3Command({
    commandId,
    kind: 'chapter.workspace.archive',
    payload: {
      chapter_id: requireText(chapterId, 'chapterId'),
    },
    expectedRevision,
  })
}
