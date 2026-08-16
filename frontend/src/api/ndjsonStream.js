export async function readNdjsonStream(response, onEvent = () => {}) {
  if (!response?.body?.getReader) {
    throw new Error('当前浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const consume = (line) => {
    let text = String(line || '').trim()
    if (!text || text.startsWith(':')) return
    if (text.startsWith('data:')) text = text.slice(5).trim()
    if (!text || text === '[DONE]') return
    let event
    try {
      event = JSON.parse(text)
    } catch (cause) {
      const error = new Error('流式响应包含无效 NDJSON')
      error.cause = cause
      error.line = text
      throw error
    }
    onEvent(event)
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() || ''
      for (const line of lines) consume(line)
      if (done) break
    }
    if (buffer.trim()) consume(buffer)
  } catch (cause) {
    await reader.cancel(cause).catch(() => {})
    throw cause
  } finally {
    reader.releaseLock()
  }
}
