const arrayOfObjects = value => (Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [])

export function normalizeChapterRewriteMatch(payload) {
  const root = payload?.rewrite_match
    || payload?.receipt?.result?.rewrite_match
    || payload?.result?.rewrite_match
    || payload
    || {}
  return {
    ...root,
    read_only: root.read_only !== false,
    target: root.target && typeof root.target === 'object' ? root.target : {},
    writing_plan: {
      ...(root.writing_plan || {}),
      blocks: arrayOfObjects(root.writing_plan?.blocks),
    },
    matches: arrayOfObjects(root.matches),
    coverage: arrayOfObjects(root.coverage),
    recommendation: root.recommendation && typeof root.recommendation === 'object'
      ? root.recommendation
      : { strategy: 'new_write', reason: '', suggestion_only: true },
    summary: root.summary && typeof root.summary === 'object' ? root.summary : {},
  }
}

export function normalizeChapterRewritePlan(payload) {
  const root = payload?.rewrite_plan
    || payload?.receipt?.result?.rewrite_plan
    || payload?.result?.rewrite_plan
    || payload
    || {}
  return {
    ...root,
    selected_legacy_blocks: arrayOfObjects(root.selected_legacy_blocks),
    new_content_items: arrayOfObjects(root.new_content_items).map(item => ({
      ...item,
      evidence_ids: Array.isArray(item.evidence_ids) ? item.evidence_ids : [],
    })),
    coverage: arrayOfObjects(root.coverage),
    pollution_findings: arrayOfObjects(root.pollution_findings),
    target: root.target && typeof root.target === 'object' ? root.target : {},
    dependencies: root.dependencies && typeof root.dependencies === 'object' ? root.dependencies : {},
    stale_reasons: Array.isArray(root.stale_reasons) ? root.stale_reasons : [],
  }
}
