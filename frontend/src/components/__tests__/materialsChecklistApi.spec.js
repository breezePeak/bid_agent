/**
 * Lightweight contract checks for materials checklist API helpers.
 * Run with: node --test (or your frontend test runner if configured).
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

// Pure mapping logic mirrored from StepDetailView.stageToCommand
function stageToCommand(stageId) {
  const s = String(stageId || '').trim()
  if (!s) return ''
  const map = {
    select_contexts: 'select-context-all',
    plan_chapter_jobs: 'plan-jobs',
    write_chapters: 'write-all',
    review_fix_chapters: 'review-fix-all',
    global_review: 'global-review',
    compliance_check: 'compliance-check',
    build_materials_checklist: 'build-materials-checklist',
    generate_outline: 'generate-outline',
    extract_facts: 'extract-facts',
  }
  if (map[s]) return map[s]
  if (s.includes('-')) return s
  return s.replace(/_/g, '-')
}

describe('stageToCommand', () => {
  it('maps known stages', () => {
    assert.equal(stageToCommand('write_chapters'), 'write-all')
    assert.equal(stageToCommand('global_review'), 'global-review')
    assert.equal(stageToCommand('build_materials_checklist'), 'build-materials-checklist')
  })
  it('handles hyphenated and empty', () => {
    assert.equal(stageToCommand('plan-jobs'), 'plan-jobs')
    assert.equal(stageToCommand(''), '')
  })
})
