function parseBoolean(value, fallback) {
  if (value === '' || value === null || value === undefined) return fallback
  if (typeof value === 'boolean') return value
  const text = String(value).toLowerCase()
  return Boolean(text) && !['0', 'false', 'no', 'off'].includes(text)
}

export function llmModelFormFromApi(model = {}) {
  return {
    id: model.id || '',
    name: model.name || '',
    provider: model.provider || 'openai',
    base_url: model.base_url || '',
    // A persisted secret must never be hydrated back into browser state.
    api_key: '',
    has_stored_api_key: Boolean(model.has_api_key),
    model: model.model || '',
    reasoning_effort: model.reasoning_effort || '',
    timeout: Number(model.timeout ?? 300),
    max_retries: Number(model.max_retries ?? 3),
    retry_initial_delay: Number(model.retry_initial_delay ?? 2),
    retry_max_delay: Number(model.retry_max_delay ?? 30),
    stream: parseBoolean(model.stream, false),
    verify_ssl: parseBoolean(model.verify_ssl, true),
  }
}

export function llmModelKeyIsReady(form, { isNew = false } = {}) {
  const entered = Boolean(String(form?.api_key || '').trim())
  return entered || (!isNew && Boolean(form?.has_stored_api_key))
}

export function llmModelPayload(form, { isNew = false } = {}) {
  return {
    id: isNew ? '' : String(form?.id || '').trim(),
    name: String(form?.name || '').trim(),
    provider: form?.provider || 'openai',
    base_url: String(form?.base_url || '').trim(),
    // Blank on an existing model is the explicit "keep stored key" contract.
    api_key: String(form?.api_key || '').trim(),
    model: String(form?.model || '').trim(),
    reasoning_effort: String(form?.reasoning_effort || '').trim(),
    timeout: form?.timeout,
    max_retries: form?.max_retries,
    retry_initial_delay: form?.retry_initial_delay,
    retry_max_delay: form?.retry_max_delay,
    stream: Boolean(form?.stream),
    verify_ssl: Boolean(form?.verify_ssl),
  }
}
