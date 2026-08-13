import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


const workbench = await readFile(
  new URL('../src/components/ChapterWorkbenchView.vue', import.meta.url),
  'utf8',
)
const blockEditor = await readFile(
  new URL('../src/components/ContentBlockEditor.vue', import.meta.url),
  'utf8',
)

test('chapter drafting renders accumulated stream text inside the final editor host', () => {
  assert.match(workbench, /<ContentBlockEditor[\s\S]*:streaming="streamingDraft"[\s\S]*:stream-text="streamText"/)
  assert.doesNotMatch(workbench, /class="stream-preview"/)
  assert.match(blockEditor, /class="live-draft" aria-live="polite"/)
  assert.match(blockEditor, /const showLiveDraft = computed/)
  assert.match(blockEditor, /split\(\/\\n\\s\*\\n\/\)/)
  assert.match(blockEditor, /class="block-body"/)
  assert.match(blockEditor, /:contenteditable="!\(readonly \|\| busy\)"/)
  assert.doesNotMatch(blockEditor, /<textarea/)
  assert.match(workbench, /streamText\.value \+= text/)
  assert.match(workbench, /await streamChapterDraft\(/)
  assert.match(workbench, /\['delta', 'content_delta', 'token'\]\.includes\(type\)/)
  assert.match(workbench, /if \(!streamCompleted\) throw new Error\('流式连接提前结束，未收到完成事件'\)/)
})

test('loading, empty, streaming, and loaded states keep one Word page shell', () => {
  assert.match(workbench, /class="chapter-doc-body"[\s\S]*class="document-stage"[\s\S]*class="document-paper"/)
  assert.match(workbench, /v-else-if="detailLoading"[\s\S]*class="document-state document-loading"/)
  assert.match(workbench, /v-else-if="detailError"[\s\S]*class="document-state document-error"[\s\S]*重新加载/)
  assert.doesNotMatch(workbench, /v-else-if="detailLoading" class="placeholder"/)
  assert.doesNotMatch(workbench, /class="doc-body"/)
  assert.match(workbench, /--word-page-width:\s*850px/)
  assert.match(workbench, /--word-page-min-height:\s*1120px/)
  assert.match(workbench, /\.chapter-doc-body\s*\{[\s\S]*display:\s*block/)
  assert.match(workbench, /\.document-paper\s*\{[\s\S]*padding:\s*72px 78px[\s\S]*box-shadow:/)
})

test('draft completion applies returned content before clearing the live preview', () => {
  assert.match(workbench, /completedContent = payload\?\.content/)
  assert.match(workbench, /chapterDetail\.value = \{[\s\S]*content: completedContent/)
  assert.match(workbench, /chapterDetail\.value = \{[\s\S]*\}\s*streamingDraft\.value = false\s*streamText\.value = ''/)
})

test('background polling does not replace the editor with a loading placeholder', () => {
  assert.match(workbench, /const \{ force = true, background = false \} = options/)
  assert.match(workbench, /if \(!background \|\| !chapterDetail\.value\) detailLoading\.value = true/)
  assert.match(workbench, /const \{ data \} = await fetchChapters\(props\.workspaceId\)/)
  assert.match(workbench, /currentSignature === nextSignature[\s\S]*content: currentContent/)
})

test('switching or unmounting aborts an active draft stream', () => {
  assert.match(workbench, /draftAbortController\?\.abort\(\)/)
  assert.match(workbench, /onUnmounted\(\(\) => \{[\s\S]*draftAbortController\?\.abort\(\)/)
})

test('chapter workbench shows writing purpose, document position and chapter relations', () => {
  assert.match(workbench, />本章写作处境</)
  assert.match(workbench, />目的 · 全书位置 · 章节关系</)
  assert.match(workbench, /writingOrientation/)
  assert.match(workbench, /orientationRelations/)
})

test('chapter workbench separates shared facts from chapter-only requirements', () => {
  assert.match(workbench, />公共项目事实</)
  assert.match(workbench, />所有章节继承 · 只读</)
  assert.match(workbench, />本章专属要求</)
  assert.match(workbench, /globalProjectContext\.value = snap\.data\.snapshot\?\.global_project_context/)
  assert.match(workbench, /chapterRequirements/)
  assert.match(workbench, /chapterScoringRequirements/)
})

test('draft stream carries both global and chapter context versions', () => {
  assert.match(workbench, /global_context_id: globalRef\.global_context_id/)
  assert.match(workbench, /global_context_revision: Number\(globalRef\.global_context_revision\)/)
  assert.match(workbench, /global_context_hash: globalRef\.global_context_hash/)
  assert.match(workbench, /chapter_context_id: chapterRef\.chapter_context_id/)
  assert.match(workbench, /chapter_context_revision: Number\(chapterRef\.chapter_context_revision \|\| 0\)/)
  assert.match(workbench, /chapter_context_hash: chapterRef\.chapter_context_hash/)
})

test('only leaf chapters expose body generation and approval', () => {
  assert.match(workbench, /const selectedIsLeaf = computed/)
  assert.match(workbench, /:disabled="busy \|\| !selectedId \|\| !selectedIsLeaf"/)
  assert.match(workbench, /ensureChaptersReady/)
  assert.match(workbench, /chapter.workspace.ensure_all/)
  assert.match(workbench, /父节点只保留标题和层级，不写正文/)
  assert.match(workbench, /if \(!selectedIsLeaf\.value\)/)
})

test('chapter workbench defaults to the chapter chat tab', () => {
  assert.match(workbench, /const rightTab = ref\('chat'\)/)
  assert.doesNotMatch(
    workbench,
    /rightTab\.value = \(globalContextReady\.value \|\| contextItems\.value\.length\) \? 'context' : 'chat'/,
  )
})

test('draft thinking is routed to the right chat pane instead of the Word page', () => {
  assert.doesNotMatch(workbench, /class="document-paper"[\s\S]*class="research-status"/)
  assert.match(workbench, /rightTab\.value = 'chat'/)
  assert.match(workbench, /type === 'thinking_delta'/)
  assert.match(workbench, /patchDraftTurn/)
  assert.match(workbench, /turn\.thinking = `\$\{turn\.thinking \|\| ''\}\$\{delta\}`/)
})

test('chapter chat shows thinking, sends on enter, and lets history be edited', () => {
  assert.match(workbench, /class="chat-thinking"/)
  assert.match(workbench, /思考过程/)
  assert.match(workbench, /<div\s+v-if="turn.role === 'assistant' \|\| turn.thinking"\s+class="chat-thinking"/)
  assert.doesNotMatch(workbench, /<details[^>]*class="chat-thinking"/)
  assert.match(workbench, /@keydown="onChatComposeKeydown"/)
  assert.match(workbench, /function onChatComposeKeydown/)
  assert.match(workbench, /event\.key !== 'Enter'/)
  assert.match(workbench, /event\.shiftKey/)
  assert.match(workbench, /sendChat\(\)/)
  assert.match(workbench, /:contenteditable="canEditChatTurn\(turn\)"/)
  assert.match(workbench, /onChatTurnBlur/)
  assert.match(workbench, /saveChapterChatTurn/)
  assert.match(workbench, /回车发送，Shift\+回车换行/)
})

test('research results expose project, similar-project, and standard labels', () => {
  assert.match(workbench, /本项目资料/)
  assert.match(workbench, /同类项目资料/)
  assert.match(workbench, /行业标准/)
})
