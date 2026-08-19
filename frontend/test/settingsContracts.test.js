import assert from 'node:assert/strict'
import test from 'node:test'

import {
  llmModelFormFromApi,
  llmModelKeyIsReady,
  llmModelPayload,
  researchSettingsFormFromApi,
  researchSettingsPayload,
} from '../src/api/settingsContracts.js'

test('HTTP model data never hydrates a persisted API key into browser state', () => {
  const form = llmModelFormFromApi({
    id: 'primary',
    name: '主模型',
    provider: 'openai',
    base_url: 'https://example.com/v1',
    api_key: 'must-not-enter-browser-state',
    has_api_key: true,
    api_key_masked: '••••••••',
    model: 'model-a',
  })

  assert.equal(form.api_key, '')
  assert.equal(form.has_stored_api_key, true)
  assert.equal(llmModelKeyIsReady(form, { isNew: false }), true)
  assert.equal(llmModelKeyIsReady(form, { isNew: true }), false)
})

test('Tavily secret is never hydrated and runtime metadata is not submitted', () => {
  const form = researchSettingsFormFromApi({
    research_provider: 'tavily',
    tavily_api_key: 'must-not-enter-browser-state',
    has_tavily_api_key: true,
    tavily_runtime_status: { ready: true },
  })
  assert.equal(form.tavily_api_key, '')
  assert.equal(form.has_tavily_api_key, true)
  form.tavily_api_key = 'new-secret'
  const payload = researchSettingsPayload(form)
  assert.equal(payload.tavily_api_key, 'new-secret')
  assert.equal(Object.hasOwn(payload, 'has_tavily_api_key'), false)
  assert.equal(Object.hasOwn(payload, 'tavily_runtime_status'), false)
})

test('blank key preserves an existing credential but is invalid for a new model', () => {
  const existing = llmModelFormFromApi({
    id: 'primary',
    has_api_key: true,
  })
  const missing = llmModelFormFromApi({
    id: 'missing-key',
    has_api_key: false,
  })

  assert.equal(llmModelKeyIsReady(existing, { isNew: false }), true)
  assert.equal(llmModelKeyIsReady(missing, { isNew: false }), false)
  assert.equal(llmModelKeyIsReady(missing, { isNew: true }), false)

  missing.api_key = 'new-secret'
  assert.equal(llmModelKeyIsReady(missing, { isNew: true }), true)
})

test('model payload sends only a newly entered key and omits credential metadata', () => {
  const form = llmModelFormFromApi({
    id: 'primary',
    name: ' 主模型 ',
    provider: 'openai',
    base_url: ' https://example.com/v1 ',
    has_api_key: true,
    model: ' model-a ',
    reasoning_effort: 'high',
  })
  const payload = llmModelPayload(form, { isNew: false })

  assert.equal(payload.id, 'primary')
  assert.equal(payload.name, '主模型')
  assert.equal(payload.base_url, 'https://example.com/v1')
  assert.equal(payload.model, 'model-a')
  assert.equal(payload.reasoning_effort, 'high')
  assert.equal(payload.api_key, '')
  assert.equal(Object.hasOwn(payload, 'has_stored_api_key'), false)
  assert.equal(Object.hasOwn(payload, 'api_key_masked'), false)
})
