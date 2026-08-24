import assert from 'node:assert/strict'
import test from 'node:test'

import {
  V3_WORKSPACES_PATH,
  buildPrepareOutlineCommand,
  buildResearchResolveCommand,
  buildRunPipelineCommand,
  formatV3ApiError,
  normalizeV3WorkspaceSnapshot,
  projectV3Planning,
  v3ErrorDetails,
  v3WorkspacePath,
  workspaceRevisionFromV3Payload,
} from '../src/api/v3Contracts.js'

test('score integrity failures show counts and diagnostic code without dumping IDs', () => {
  const message = formatV3ApiError({
    v3Payload: {
      ok: false,
      receipt: {
        status: 'rejected',
        error: {
          code: 'V3_SCORE_INTEGRITY_BLOCKED',
          message: 'ScoreModel 引用审计失败',
          details: {
            score_audit: {
              unlinked_score_point_ids: ['SP-secret-a', 'SP-secret-b'],
              unknown_requirement_ids: ['REQ-secret-a'],
              invalid_anchor_score_point_ids: [],
            },
          },
        },
      },
    },
  }, '评分点解析失败。')

  assert.match(message, /错误码：V3_SCORE_INTEGRITY_BLOCKED/)
  assert.match(message, /未关联招标需求 2 个/)
  assert.match(message, /引用了未知需求 1 个/)
  assert.doesNotMatch(message, /SP-secret|REQ-secret/)
  assert.doesNotMatch(message, /\[[^\]]*\]/)
})

test('score integrity failure safely summarizes legacy Python audit text', () => {
  const message = formatV3ApiError(new Error(
    "ScoreModel 引用审计失败: {'passed': False, 'unlinked_score_point_ids': ['SP-a', 'SP-b', 'SP-c'], 'bulk_linked_score_point_ids': []}",
  ), '评分点解析失败。')

  assert.match(message, /错误码：V3_SCORE_INTEGRITY_BLOCKED/)
  assert.match(message, /未关联招标需求 3 个/)
  assert.doesNotMatch(message, /SP-a|SP-b|SP-c/)
})

test('requirement coverage failures show a count without dumping source IDs', () => {
  const message = formatV3ApiError({
    v3Payload: {
      receipt: {
        status: 'rejected',
        error: {
          code: 'V3_REQUIREMENT_COVERAGE_BLOCKED',
          message: 'RequirementLedger 需求覆盖校验失败：3 个来源片段未覆盖。',
          details: {
            coverage_audit: {
              missing_chunk_ids: ['chunk-secret-a', 'chunk-secret-b', 'chunk-secret-c'],
            },
          },
        },
      },
    },
  }, '需求解析失败。')

  assert.match(message, /错误码：V3_REQUIREMENT_COVERAGE_BLOCKED/)
  assert.match(message, /3 个来源片段/)
  assert.doesNotMatch(message, /chunk-secret/)
})

test('score semantic failures stay actionable without dumping score rule IDs', () => {
  const message = formatV3ApiError(new Error(
    'score_semantic_candidate_invalid: 评分语义推理失败（调用 2 次）：'
    + "ValueError: SP-secret-a/price-benchmark-quote 的 source_excerpt 无法定位; "
    + "ValueError: 评分语义候选 rule_id 覆盖不完整: missing=['SP-secret-b']",
  ), '评分点解析失败。')

  assert.match(message, /评分理解批次未通过结构或来源校验/)
  assert.match(message, /错误码：score_semantic_candidate_invalid/)
  assert.match(message, /评分目录尚未生成/)
  assert.doesNotMatch(message, /SP-secret|price-benchmark-quote|missing=/)
})

test('score semantic details expose malformed JSON and failed score unit separately', () => {
  const details = v3ErrorDetails(new Error(
    'score_semantic_candidate_invalid: 评分语义推理失败（调用 2 次）：'
    + "JSONDecodeError: Expecting ',' delimiter: line 1 column 17871 (char 17870); "
    + 'ValueError: 得分单元 u-performance-experience 缺少满分原子条件',
  ))

  assert.deepEqual(details, [
    {
      title: '首次模型输出',
      description: '返回的 JSON 格式不合法，第 17870 个字符附近缺少逗号、引号或分隔符。',
    },
    {
      title: '得分单元 u-performance-experience',
      description: '未拆出可追溯的满分原子条件。',
    },
  ])
})

test('score semantic details use persisted diagnostics when stage message is generic', () => {
  const snapshot = normalizeV3WorkspaceSnapshot({
    snapshot: {
      analysis: {
        pipeline: {
          stages: [{
            stage_id: 'score_semantic',
            status: 'failed',
            error: {
              code: 'score_semantic_candidate_invalid',
              message: '评分语义推理失败',
              details: {
                attempts: 2,
                diagnostics: [
                  "JSONDecodeError: Expecting ',' delimiter: line 1 column 20 (char 19)",
                  'SSB-example/商务部分: SP-secret/业绩: '
                    + 'ValueError: 得分单元 u-performance 缺少满分原子条件',
                  'SSB-example/商务部分: ValueError: '
                    + 'SP-secret/人员配置 缺少评分语义 interpretation',
                ],
              },
            },
          }],
        },
      },
    },
  })
  const stageError = snapshot.analysis.pipeline.stages[0].error
  const cause = new Error(stageError.message)
  cause.v3Payload = { receipt: { status: 'rejected', error: stageError } }

  assert.match(
    formatV3ApiError(cause, '评分点解析失败。'),
    /评分理解批次未通过结构或来源校验/,
  )
  assert.deepEqual(v3ErrorDetails(cause), [
    {
      title: '首次模型输出',
      description: '返回的 JSON 格式不合法，第 19 个字符附近缺少逗号、引号或分隔符。',
    },
    {
      title: '业绩',
      description: '未拆出可追溯的满分原子条件。',
    },
    {
      title: '人员配置',
      description: '缺少评分语义 interpretation',
    },
  ])
})

test('workspace snapshot normalization preserves pipeline stages and products', () => {
  const normalized = normalizeV3WorkspaceSnapshot({
    snapshot: {
      analysis: {
        pipeline: {
          status: 'running',
          stages: [{
            stage_id: 'score_semantic',
            status: 'running',
            llm_request_count: 1,
            llm_requests: [{ request_id: 'request-1', status: 'running' }],
          }],
          products: [{ kind: 'ScoreStructureDraft', status: 'ready' }],
        },
      },
    },
  })

  assert.equal(normalized.analysis.pipeline.status, 'running')
  assert.equal(normalized.analysis.pipeline.stages[0].stage_id, 'score_semantic')
  assert.equal(normalized.analysis.pipeline.stages[0].llm_request_count, 1)
  assert.equal(normalized.analysis.pipeline.stages[0].llm_requests[0].request_id, 'request-1')
  assert.equal(normalized.analysis.pipeline.products[0].kind, 'ScoreStructureDraft')
})

test('workspace snapshot normalizes PR-01 mode and capability fields compatibly', () => {
  const current = normalizeV3WorkspaceSnapshot({
    snapshot: {
      writing_mode: 'bid_rewrite',
      chapter_plan_flow: 'confirmed_plan_v2',
      capabilities: { bid_rewrite: { enabled: true, released: false } },
    },
  })
  assert.equal(current.writing_mode, 'bid_rewrite')
  assert.equal(current.chapter_plan_flow, 'confirmed_plan_v2')
  assert.equal(current.capabilities.bid_rewrite.released, false)

  const legacy = normalizeV3WorkspaceSnapshot({ snapshot: { unknown_future: true } })
  assert.equal(legacy.writing_mode, 'full_write')
  assert.equal(legacy.chapter_plan_flow, 'legacy_inline')
  assert.deepEqual(legacy.capabilities, {})
})

test('workspace snapshot keeps one normalized global project context for every chapter view', () => {
  const normalized = normalizeV3WorkspaceSnapshot({
    snapshot: {
      global_project_context: {
        global_context_id: 'ProjectModel@6',
        global_context_revision: 6,
        global_context_hash: 'abc123',
        identity: { project_name: '全国国土变更调查核查项目' },
        scope: ['覆盖全国31个省级区域'],
        work_packages: ['国家级内外业核查'],
        confirmed_facts: [{ fact_id: 'PF-1', statement: '覆盖31个省级区域' }],
      },
    },
  })

  assert.equal(normalized.global_project_context.global_context_id, 'ProjectModel@6')
  assert.equal(normalized.global_project_context.global_context_revision, 6)
  assert.deepEqual(normalized.global_project_context.scope, ['覆盖全国31个省级区域'])
  assert.deepEqual(normalized.global_project_context.background, [])
  assert.equal(normalized.global_project_context.confirmed_facts[0].fact_id, 'PF-1')
})

test('workspace snapshot normalization preserves generation stages and lightweight content units', () => {
  const normalized = normalizeV3WorkspaceSnapshot({
    snapshot: {
      generation: {
        operation_id: 'operation-1',
        status: 'running',
        current_stage_id: 'execute_content_plan',
        stages: [{
          stage_id: 'execute_content_plan',
          status: 'running',
          summary: { completed_units: 2 },
        }],
        content: {
          total_units: 5,
          completed_units: 2,
          running_units: 1,
          units: [{
            unit_id: 'unit-3',
            title: '第三章',
            preview: '只保留短预览',
          }],
        },
        research: {
          questions: [{ need_id: 'EN-AUTO-1', status: 'researching' }],
        },
      },
    },
  })

  assert.equal(normalized.generation.current_stage_id, 'execute_content_plan')
  assert.equal(normalized.generation.stages[0].summary.completed_units, 2)
  assert.equal(normalized.generation.content.total_units, 5)
  assert.equal(normalized.generation.content.units[0].preview, '只保留短预览')
  assert.equal(normalized.generation.research.questions[0].status, 'researching')
})

test('unrelated V3 errors keep their original actionable message', () => {
  assert.equal(
    formatV3ApiError(
      { response: { data: { error: { code: 'UPLOAD_TYPE_UNSUPPORTED' }, message: '不支持该文件类型。' } } },
      '上传失败。',
    ),
    '不支持该文件类型。',
  )
})

test('V3 workspace routes stay in the V3 namespace and encode run IDs', () => {
  const runId = '客户 A/标书 01'
  const routes = [
    V3_WORKSPACES_PATH,
    v3WorkspacePath(runId),
    v3WorkspacePath(runId, 'snapshot'),
    v3WorkspacePath(runId, '/commands/'),
    v3WorkspacePath(runId, 'uploads'),
    v3WorkspacePath(runId, 'chat/turn'),
    v3WorkspacePath(runId, 'chapters/ch-a/chat/turn'),
    v3WorkspacePath(runId, 'chapters/ch-a/chat/history'),
    v3WorkspacePath(runId, 'exports/final'),
  ]

  assert.equal(V3_WORKSPACES_PATH, '/v3/workspaces')
  assert.equal(
    routes[2],
    `/v3/workspaces/${encodeURIComponent(runId)}/snapshot`,
  )
  for (const route of routes) {
    assert.match(route, /^\/v3\/workspaces(?:\/|$)/)
    assert.doesNotMatch(route, /\/api\/v2\/workspaces|\/v2\/workspaces/)
  }
  assert.throws(() => v3WorkspacePath(''), /runId is required/)
})

test('pipeline command uses the frozen command envelope', () => {
  assert.deepEqual(buildRunPipelineCommand('cmd-pipeline-1', 17), {
    command_id: 'cmd-pipeline-1',
    kind: 'document.run_pipeline',
    payload: { chapter_ids: [] },
    expected_revision: 17,
    idempotency_key: 'cmd-pipeline-1',
  })
  assert.deepEqual(
    buildRunPipelineCommand('cmd-pipeline-2', 18, ['chapter-3', 'chapter-3']),
    {
      command_id: 'cmd-pipeline-2',
      kind: 'document.run_pipeline',
      payload: { chapter_ids: ['chapter-3'] },
      expected_revision: 18,
      idempotency_key: 'cmd-pipeline-2',
    },
  )
})

test('outline command stops at the score-aware planning boundary', () => {
  assert.deepEqual(buildPrepareOutlineCommand('cmd-outline-1', 18), {
    command_id: 'cmd-outline-1',
    kind: 'document.prepare_outline',
    payload: {},
    expected_revision: 18,
    idempotency_key: 'cmd-outline-1',
  })
})

test('research command uses Tavily without browser-provider attachments', () => {
  const command = buildResearchResolveCommand(
    'cmd-research-1',
    23,
    'need-security-standard',
  )

  assert.deepEqual(command, {
    command_id: 'cmd-research-1',
    kind: 'research.resolve',
    payload: {
      need_id: 'need-security-standard',
      provider_id: 'tavily',
      attachment_input_ids: [],
    },
    expected_revision: 23,
    idempotency_key: 'cmd-research-1',
  })
  assert.deepEqual(command.payload.attachment_input_ids, [])
  assert.throws(
    () => buildResearchResolveCommand('cmd', -1, 'need', []),
    /expectedRevision must be a non-negative integer/,
  )
})

test('workspace snapshot normalization produces stable V3 component inputs', () => {
  const normalized = normalizeV3WorkspaceSnapshot({
    snapshot: {
      workspace_revision: '9',
      inputs: { inputs: [{ input_id: 'tender-1', active: true }] },
      content_units: null,
      quality: { report: null },
      materials: { summary: null, items: 'invalid' },
      evidence_needs: [{ need_id: 'need-1' }],
    },
  })

  assert.equal(normalized.workspace_revision, 9)
  assert.deepEqual(normalized.inputs.inputs, [{ input_id: 'tender-1', active: true }])
  assert.deepEqual(normalized.document, {})
  assert.deepEqual(normalized.content_units, [])
  assert.deepEqual(normalized.quality.report, {})
  assert.deepEqual(normalized.materials, { summary: {}, items: [] })
  assert.deepEqual(normalized.evidence_needs, [{ need_id: 'need-1' }])
  assert.deepEqual(normalized.analysis.score_model, {})
  assert.equal(workspaceRevisionFromV3Payload({ snapshot: { workspace_revision: 9 } }), 9)
  assert.equal(workspaceRevisionFromV3Payload({ snapshot: { workspace_revision: -2 } }), 0)
  assert.equal(workspaceRevisionFromV3Payload(null), 0)
})

test('workspace snapshot preserves active registered company inputs for the UI count', () => {
  const normalized = normalizeV3WorkspaceSnapshot({
    snapshot: {
      inputs: {
        inputs: [
          { input_id: 'tender-1', role: 'tender', active: true },
          { input_id: 'company-1', role: 'company', active: true },
          { input_id: 'company-old', role: 'company', active: false },
        ],
      },
    },
  })

  const registeredCompanyInputs = normalized.inputs.inputs.filter(
    item => item.active && item.role === 'company',
  )
  assert.deepEqual(
    registeredCompanyInputs.map(item => item.input_id),
    ['company-1'],
  )
})

test('score-aware planning joins ScorePoint through Duty into the chapter tree', () => {
  const projected = projectV3Planning({
    snapshot: {
      promoted_artifacts: [
        {
          artifact_kind: 'ScoreModel',
          payload: {
            total_points: 30,
            points: [
              { score_point_id: 'SP-1', title: '技术方案', criterion: '技术方案满分20分', max_points: 20 },
              { score_point_id: 'SP-2', title: '服务保障', criterion: '服务保障满分10分', max_points: 10 },
            ],
          },
        },
        {
          artifact_kind: 'ResponseTopicGraph',
          payload: {
            duties: [
              { duty_id: 'D-1', score_point_ids: ['SP-1'] },
              { duty_id: 'D-2', score_point_ids: ['SP-2'] },
            ],
          },
        },
        {
          artifact_kind: 'ChapterBlueprint',
          payload: {
            nodes: [
              { chapter_id: 'C-2', parent_chapter_id: 'C-1', order: 2, title: '服务方案' },
              { chapter_id: 'C-1', parent_chapter_id: null, order: 1, title: '技术部分' },
              { chapter_id: 'C-1-1', parent_chapter_id: 'C-1', order: 1, title: '技术方案' },
            ],
            assignments: [
              { duty_id: 'D-1', chapter_id: 'C-1-1', role: 'primary' },
              { duty_id: 'D-2', chapter_id: 'C-2', role: 'primary' },
              { duty_id: 'D-unknown', chapter_id: 'C-1', role: 'primary' },
            ],
          },
        },
      ],
      document: {},
    },
  })

  assert.deepEqual(projected.summary, {
    total_points: 30,
    score_point_count: 2,
    covered_score_point_count: 2,
    uncovered_score_point_count: 0,
    response_unit_count: 0,
    covered_response_unit_count: 0,
    uncovered_response_unit_count: 0,
    chapter_count: 3,
  })
  assert.equal(projected.outline[0].chapter_id, 'C-1')
  assert.deepEqual(projected.outline[0].score_point_ids, ['SP-1', 'SP-2'])
  assert.deepEqual(
    projected.outline[0].children.map(chapter => [chapter.number, chapter.chapter_id]),
    [['1.1', 'C-1-1'], ['1.2', 'C-2']],
  )
})

test('score-direct planning numbers score-group roots and deep outline paths', () => {
  const projected = projectV3Planning({
    snapshot: {
      promoted_artifacts: [{
        artifact_kind: 'ChapterBlueprint',
        payload: {
          nodes: [
            { chapter_id: 'price', parent_chapter_id: null, order: 0, title: '价格部分' },
            { chapter_id: 'business', parent_chapter_id: null, order: 1, title: '商务部分' },
            { chapter_id: 'technical', parent_chapter_id: null, order: 2, title: '技术部分' },
            { chapter_id: 'method', parent_chapter_id: 'technical', order: 3, title: '技术方法' },
            { chapter_id: 'prepare', parent_chapter_id: 'method', order: 4, title: '核查准备工作' },
            { chapter_id: 'task', parent_chapter_id: 'prepare', order: 5, title: '核查准备任务' },
            { chapter_id: 'condition', parent_chapter_id: 'task', order: 6, title: '数据接收内容' },
          ],
        },
      }],
    },
  })

  assert.deepEqual(
    projected.outline.map(node => [node.number, node.title]),
    [['1', '价格部分'], ['2', '商务部分'], ['3', '技术部分']],
  )
  assert.equal(projected.outline[2].children[0].number, '3.1')
  assert.equal(projected.outline[2].children[0].children[0].number, '3.1.1')
  assert.equal(
    projected.outline[2].children[0].children[0].children[0].children[0].number,
    '3.1.1.1.1',
  )
})

test('score-direct planning projects chapter bindings and response-unit coverage', () => {
  const projected = projectV3Planning({
    snapshot: {
      inputs: {
        inputs: [
          { input_id: 'tender-1', filename: '招标文件.pdf', active: true, role: 'tender' },
        ],
      },
      analysis: {
        requirement_ledger: {
          requirements: [
            {
              requirement_id: 'REQ-1',
              original_text: '实施方案应说明进度安排和质量控制措施。',
              normalized_requirement: '说明进度安排和质量控制措施',
              source_anchor: {
                source_input_id: 'tender-1',
                chunk_id: 'chunk-requirement-1',
                page: 18,
                location: '第五章 采购需求 / 3.2',
              },
            },
          ],
        },
        score_model: {
          total_points: 20,
          points: [
            {
              score_point_id: 'SP-1',
              title: '实施方案',
              criterion: '实施方案完整得20分',
              max_points: 20,
              score_conditions: [
                {
                  condition_id: 'SC-1',
                  text: '方案包括实施路径和进度安排',
                  normalized_condition: '说明实施路径和进度安排',
                  condition_role: 'content',
                  source_excerpt: '方案包括实施路径和进度安排，内容完整可行。',
                  source_anchor: {
                    source_input_id: 'tender-1',
                    chunk_id: 'chunk-score-1',
                    page: 7,
                    location: '第三章 评分办法 / 技术部分',
                  },
                },
                {
                  condition_id: 'SC-2',
                  text: '全文内容前后一致',
                  normalized_condition: '保持全文内容前后一致',
                  condition_role: 'document',
                  source_excerpt: '投标文件内容前后一致。',
                  source_anchor: {
                    source_input_id: 'tender-1',
                    chunk_id: 'chunk-score-1',
                    page: 7,
                    location: '第三章 评分办法 / 技术部分',
                  },
                },
              ],
              response_units: [
                {
                  unit_id: 'RU-1',
                  title: '实施路径',
                  response_scope: 'section',
                  condition_ids: ['SC-1'],
                  linked_requirement_ids: ['REQ-1'],
                },
                {
                  unit_id: 'RU-2',
                  title: '全文一致性',
                  response_scope: 'document',
                  condition_ids: ['SC-2'],
                  linked_requirement_ids: ['REQ-1'],
                },
              ],
            },
          ],
        },
        chapter_blueprint: {
          planning_model: 'score_direct',
          nodes: [
            {
              chapter_id: 'C-1',
              order: 1,
              title: '实施方案',
              primary_response_unit_ids: ['RU-1'],
              supporting_response_unit_ids: [],
              score_point_ids: ['SP-1'],
              score_condition_ids: ['SC-1'],
              requirement_ids: ['REQ-1'],
            },
          ],
          document_quality_gates: [
            {
              gate_id: 'G-1',
              response_unit_ids: ['RU-2'],
              score_point_ids: ['SP-1'],
              score_condition_ids: ['SC-2'],
              requirement_ids: ['REQ-1'],
              criteria: ['全文术语、数据和承诺保持一致'],
              check_items: ['统一术语表', '交叉核对关键数据'],
            },
          ],
        },
      },
    },
  })

  assert.deepEqual(projected.outline[0].direct_score_point_ids, ['SP-1'])
  assert.deepEqual(projected.outline[0].primary_response_unit_ids, ['RU-1'])
  assert.equal(projected.summary.response_unit_count, 2)
  assert.equal(projected.summary.covered_response_unit_count, 2)
  assert.equal(projected.summary.uncovered_response_unit_count, 0)
  assert.deepEqual(projected.uncovered_response_units, [])
  assert.equal(projected.score_conditions.length, 2)
  assert.equal(projected.score_conditions[0].normalized_condition, '说明实施路径和进度安排')
  assert.equal(projected.score_conditions[0].condition_role, 'content')
  assert.equal(
    projected.score_conditions[0].source_location.label,
    '招标文件.pdf · 第 7 页 · 第三章 评分办法 / 技术部分',
  )
  assert.deepEqual(
    projected.score_conditions[0].response_units.map(unit => unit.unit_id),
    ['RU-1'],
  )
  assert.deepEqual(projected.score_conditions[0].destinations, [
    { type: 'chapter', chapter_id: 'C-1', title: '实施方案' },
  ])
  assert.deepEqual(projected.score_conditions[1].destinations, [
    {
      type: 'document_quality_gate',
      gate_id: 'G-1',
      title: '全文术语、数据和承诺保持一致',
    },
  ])
  assert.deepEqual(
    projected.quality_gates[0].requirements.map(item => item.requirement_id),
    ['REQ-1'],
  )
  assert.equal(projected.outline[0].score_conditions[0].condition_id, 'SC-1')
  assert.equal(
    projected.outline[0].requirements[0].original_text,
    '实施方案应说明进度安排和质量控制措施。',
  )
  assert.equal(projected.quality_gates[0].response_units[0].unit_id, 'RU-2')
  assert.equal(projected.quality_gates[0].score_conditions[0].condition_id, 'SC-2')
})

test('outdated analysis hides old score points, chapter outline, and confirmed planning result', () => {
  const projected = projectV3Planning({
    snapshot: {
      planning: { status: 'confirmed', receipt_id: 'old-h1' },
      analysis: {
        status: 'failed',
        stale: true,
        latest_operation: {
          kind: 'document.prepare_outline',
          status: 'failed',
          result_outdated: true,
        },
        score_model: {
          points: [
            { score_point_id: 'SP-old', title: '旧评分点', criterion: '旧规则', max_points: 10 },
          ],
        },
        topic_graph: {
          duties: [{ duty_id: 'D-old', score_point_ids: ['SP-old'] }],
        },
        chapter_blueprint: {
          nodes: [{ chapter_id: 'C-old', order: 1, title: '旧目录' }],
          assignments: [{ duty_id: 'D-old', chapter_id: 'C-old', role: 'primary' }],
        },
      },
    },
  })

  assert.equal(projected.outdated, true)
  assert.deepEqual(projected.score_points, [])
  assert.deepEqual(projected.outline, [])
  assert.deepEqual(projected.summary, {
    total_points: 0,
    score_point_count: 0,
    covered_score_point_count: 0,
    uncovered_score_point_count: 0,
    response_unit_count: 0,
    covered_response_unit_count: 0,
    uncovered_response_unit_count: 0,
    chapter_count: 0,
  })
})
