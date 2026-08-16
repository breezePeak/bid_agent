import assert from 'node:assert/strict'
import test from 'node:test'

import { readNdjsonStream } from '../src/api/ndjsonStream.js'

function responseFromByteChunks(chunks) {
  let index = 0
  return {
    body: new ReadableStream({
      pull(controller) {
        if (index >= chunks.length) {
          controller.close()
          return
        }
        controller.enqueue(chunks[index])
        index += 1
      },
    }),
  }
}

test('NDJSON reader preserves Chinese text split across UTF-8 byte chunks', async () => {
  const encoder = new TextEncoder()
  const bytes = encoder.encode([
    JSON.stringify({ type: 'meta', chapter_id: 'chapter-1' }),
    JSON.stringify({ type: 'delta', delta: '项目实施方案' }),
    JSON.stringify({ type: 'done', text: '项目实施方案' }),
    '',
  ].join('\n'))
  const firstChineseByte = bytes.indexOf(0xe9)
  const chunks = [
    bytes.slice(0, firstChineseByte + 1),
    bytes.slice(firstChineseByte + 1, firstChineseByte + 2),
    bytes.slice(firstChineseByte + 2, bytes.length - 3),
    bytes.slice(bytes.length - 3),
  ]
  const events = []

  await readNdjsonStream(responseFromByteChunks(chunks), event => events.push(event))

  assert.deepEqual(events.map(event => event.type), ['meta', 'delta', 'done'])
  assert.equal(events[1].delta, '项目实施方案')
  assert.equal(events[2].text, '项目实施方案')
})

test('NDJSON reader delivers terminal error events without treating them as transport failures', async () => {
  const encoder = new TextEncoder()
  const events = []
  const response = responseFromByteChunks([
    encoder.encode('{"type":"delta","delta":"已生成"}\r\n'),
    encoder.encode('{"type":"error","code":"WRITER_FAILED","message":"模型中断"}'),
  ])

  await readNdjsonStream(response, event => events.push(event))

  assert.equal(events.length, 2)
  assert.deepEqual(events[1], {
    type: 'error',
    code: 'WRITER_FAILED',
    message: '模型中断',
  })
})

test('NDJSON reader rejects malformed non-empty lines', async () => {
  const encoder = new TextEncoder()
  const response = responseFromByteChunks([
    encoder.encode('{"type":"delta","delta":"ok"}\nnot-json\n'),
  ])

  await assert.rejects(
    readNdjsonStream(response),
    error => error.message === '流式响应包含无效 NDJSON' && error.line === 'not-json',
  )
})
