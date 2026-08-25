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
