<template>
  <div class="workbench" :style="workbenchStyle" :class="{ 'is-dragging': isDragging }">
    <!-- 左：目录结构 -->
    <aside class="pane pane-tree">
      <header class="pane-header">
        <div>
          <p class="kicker">目录结构</p>
          <h3>章节目录</h3>
        </div>
        <div class="pane-header-actions">
          <button type="button" class="btn btn-sm back-assistant-btn" @click="backToAssistant">
            <svg aria-hidden="true" viewBox="0 0 24 24">
              <path d="m15 18-6-6 6-6M9 12h10" />
            </svg>
            返回助手
          </button>
          <button type="button" class="btn btn-sm" :disabled="busy" @click="reloadAll">刷新</button>
        </div>
      </header>

      <div class="tree-toolbar">
        <button type="button" class="btn btn-sm btn-primary" :disabled="busy" @click="openCreateModal">
          + 新建章节
        </button>
        <button
          v-if="!isSelectingChapters"
          type="button"
          class="btn btn-sm"
          :disabled="busy || !writableLeafChapters.length"
          @click="beginChapterSelection"
        >
          选择章节编写
        </button>
        <button
          v-else
          type="button"
          class="btn btn-sm btn-primary"
          :disabled="busy || !selectedWritingChapterIds.length"
          @click="writeSelectedChapters"
        >
          编写 {{ selectedWritingChapterIds.length }} 章
        </button>
        <button
          v-if="isSelectingChapters"
          type="button"
          class="btn btn-sm"
          :disabled="busy || !writableLeafChapters.length"
          @click="selectAllWritingChapters"
        >
          全选叶子章节
        </button>
        <button
          v-if="isSelectingChapters && selectedWritingChapterIds.length"
          type="button"
          class="btn btn-sm"
          :disabled="busy"
          @click="clearWritingChapterSelection"
        >
          清空已选
        </button>
        <button v-if="isSelectingChapters" type="button" class="btn btn-sm" :disabled="busy" @click="cancelChapterSelection">取消选择</button>
        <button v-if="!isSelectingChapters" type="button" class="btn btn-sm btn-primary" :disabled="busy || !selectedIsLeaf" @click="writeCurrentChapter">
          一键编写
        </button>
        <button type="button" class="btn btn-sm" :disabled="busy" @click="composeCheck">检查组装</button>
        <button type="button" class="btn btn-sm" :disabled="busy || !treeItems.length" @click="exportMarkdownOutline">
          导出 MD
        </button>
        <button type="button" class="btn btn-sm btn-primary" :disabled="busy" @click="exportCurrentWord">
          导出 Word
        </button>
      </div>

      <div v-if="listError" class="banner error">{{ listError }}</div>

      <div class="tree-stats">
        <span>{{ items.length }} 章</span>
        <span>{{ materializedCount }} 已物化</span>
        <span>{{ formalCount }} 正式</span>
      </div>

      <nav class="tree-list" aria-label="章节目录">
        <div
          v-for="(item, idx) in treeItems"
          :key="item.chapter_id"
          class="tree-item"
          :class="{
            active: item.chapter_id === selectedId,
            archived: item.status === 'archived',
            editing: editingChapterId === item.chapter_id,
            'writing-selecting': isSelectingChapters && item.status !== 'archived',
          }"
          :style="{ '--tree-depth': item.depth || 0 }"
          @click="selectChapter(item.chapter_id)"
        >
          <span class="tree-indent" aria-hidden="true" />
          <input
            v-if="isSelectingChapters && item.status !== 'archived'"
            class="tree-write-checkbox"
            type="checkbox"
            :checked="isChapterWritingSelected(item)"
            :aria-label="`选择编写 ${item.title || item.chapter_id}`"
            @click.stop
            @change.stop="toggleChapterWritingSelection(item, $event)"
          />
          <span class="tree-dot" :class="[statusClass(item), batchChapterStatusClass(item)]" />

          <template v-if="editingChapterId === item.chapter_id">
            <input
              ref="editInputRef"
              v-model="editingTitle"
              class="tree-item-input"
              @keydown.enter.prevent="saveRenameChapter(item)"
              @keydown.esc.prevent="cancelRename"
              @click.stop
              @blur="saveRenameChapter(item)"
            />
          </template>
          <template v-else>
            <span class="tree-title-row">
              <span class="tree-title" :title="item.title || item.chapter_id" @dblclick="startRenameChapter(item, $event)">
                {{ item.title || item.chapter_id }}
              </span>
              <small v-if="isMultiChapterQueued(item)" class="tree-queue-label">队列中</small>
            </span>
            <span v-if="isLeafChapter(item)" class="tree-meta">{{ shortStatus(item) }}</span>
            <div class="tree-item-actions" @click.stop>
              <button
                type="button"
                class="icon-action-btn"
                title="修改标题"
                @click="startRenameChapter(item, $event)"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10zM14 7l3 3" /></svg>
              </button>
              <button
                type="button"
                class="icon-action-btn"
                title="向上移动"
                :disabled="idx === 0"
                @click="handleMoveChapter(item, 'up', $event)"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m7 11 5-5 5 5M12 6v12" /></svg>
              </button>
              <button
                type="button"
                class="icon-action-btn"
                title="向下移动"
                :disabled="idx === treeItems.length - 1"
                @click="handleMoveChapter(item, 'down', $event)"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m7 13 5 5 5-5M12 18V6" /></svg>
              </button>
              <button
                type="button"
                class="icon-action-btn danger"
                title="归档/删除章节"
                @click="handleArchiveChapter(item, $event)"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5" /></svg>
              </button>
            </div>
          </template>
        </div>
        <p v-if="!items.length && !busy" class="empty-hint">
          暂无目录。请先在「流水线」完成规划并晋级 Blueprint 或使用上方「+ 新建章节」。
        </p>
      </nav>

      <div v-if="composeResult" class="compose-box" :class="{ ok: composeResult.export_allowed }">
        <strong>{{ composeResult.export_allowed ? '可导出正式稿' : '仅草稿预览' }}</strong>
        <small>{{ composeResult.document_hash?.slice(0, 16) }}…</small>
        <small v-if="composeResult.pending_chapters?.length">
          待确认 {{ composeResult.pending_chapters.length }} 章
        </small>
      </div>
    </aside>

    <!-- 左拖拽分割线 -->
    <div class="resizer resizer-left" title="拖动调整目录栏宽度" @mousedown="startDragLeft">
      <div class="resizer-handle"></div>
    </div>

    <!-- 中：文档生成 -->
    <main class="pane pane-doc">
      <header class="pane-header doc-header">
        <div>
          <p class="kicker">文档生成</p>
          <h3>{{ selectedChapter?.title || selectedId || '选择左侧章节' }}</h3>
          <div v-if="selectedChapter" class="doc-sub">
            <span v-if="selectedIsLeaf" class="pill" :class="statusClass(selectedChapter)">{{ shortStatus(selectedChapter) }}</span>
            <span>rev {{ chapterDetail?.chapter_revision || selectedChapter.chapter_revision || 0 }}</span>
            <span>head {{ chapterDetail?.head_content_revision || selectedChapter.head_content_revision || 0 }}</span>
            <span>formal {{ chapterDetail?.formal_content_revision || selectedChapter.formal_content_revision || 0 }}</span>
          </div>
        </div>
        <div class="doc-actions">
          <button type="button" class="btn btn-sm" :disabled="busy || !selectedId" @click="showRevisions = true">
            版本
          </button>
        </div>
      </header>

      <div v-if="actionError" class="banner error">{{ actionError }}</div>
      <div v-if="researchGapConfirmation" class="banner warning">
        {{ researchGapConfirmation.message }}
        <details v-if="researchGapConfirmation.candidates?.length" class="research-gap-candidates">
          <summary>查看本次检索到但未采用的资料（{{ researchGapConfirmation.candidates.length }} 条）</summary>
          <ol>
            <li v-for="item in researchGapConfirmation.candidates" :key="`${item.index}-${item.source_url}-${item.title}`">
              <a v-if="item.source_url" :href="item.source_url" target="_blank" rel="noopener noreferrer">{{ item.title }}</a>
              <span v-else>{{ item.title }}</span>
              <small>（{{ item.reason || '与本章无可用信息' }}）</small>
            </li>
          </ol>
        </details>
        <button type="button" class="btn btn-sm" :disabled="busy" @click="confirmResearchGapAndGenerate">
          确认使用现有资料继续写作
        </button>
        <button type="button" class="btn btn-sm" :disabled="busy" @click="researchGapConfirmation = null">
          取消
        </button>
      </div>
      <div v-if="actionMessage" class="banner ok">{{ actionMessage }}</div>
      <div v-if="batchWritingProgress" class="banner ok">
        批量编写：正在处理《{{ batchWritingProgress.current_title || '准备选中章节' }}》
        <span>；第 {{ batchWritingProgress.current_index || 1 }}/{{ batchWritingProgress.total }} 章</span>
        <span>；已完成 {{ batchWritingProgress.completed_count }} 章</span>
      </div>

      <div ref="docBodyEl" class="chapter-doc-body">
        <div class="document-stage">
          <article
            class="document-paper"
            :aria-busy="detailLoading"
            aria-label="A4 正文编辑页"
          >
            <header v-if="selectedId" class="paper-heading">
              <h1>{{ selectedChapter?.title || selectedId || '未命名章节' }}</h1>
            </header>

            <div v-if="!selectedId" class="document-state">
              <h4>从左侧选择章节</h4>
              <p>中间区域用于生成与编辑正文；右侧是本章专属对话与上下文（各章历史互不混用）。</p>
            </div>
            <div
              v-else-if="detailLoading"
              class="document-state document-loading"
              role="status"
              aria-live="polite"
            >
              <span class="document-loading-mark" aria-hidden="true" />
              <h4>正在加载章节</h4>
              <p>正在把本章内容放入当前 Word 页面…</p>
            </div>
            <div v-else-if="detailError" class="document-state document-error" role="alert">
              <h4>章节加载失败</h4>
              <p>{{ detailError }}</p>
              <button type="button" class="btn" :disabled="busy" @click="loadChapterDetail({ force: true })">重新加载</button>
            </div>
            <div v-else-if="!selectedIsLeaf" class="document-state">
              <h4>该章节是目录父节点</h4>
              <p>父节点只保留标题和层级，不写正文。请从左侧选择其下级叶子章节生成内容。</p>
            </div>
            <div v-else-if="!chapterDetail?.materialized" class="document-state">
              <h4>正在准备章节工作台</h4>
              <p>目录确认后会自动打开本章，无需手动物化。</p>
            </div>
            <template v-else>
              <ContentBlockEditor
                ref="editorRef"
                :blocks="editorBlocks"
                :busy="busy"
                :streaming="streamingDraft"
                :stream-text="streamText"
                :remote-hint="remoteHint"
                @save="onSaveBlocks"
              />
            </template>
          </article>
        </div>
      </div>
    </main>

    <!-- 右拖拽分割线 -->
    <div class="resizer resizer-right" title="拖动调整对话栏宽度" @mousedown="startDragRight">
      <div class="resizer-handle"></div>
    </div>

    <!-- 右：聊天 + 上下文 -->
    <aside class="pane pane-chat">
      <header class="pane-header">
        <div>
          <p class="kicker">Agent · 本章专属</p>
          <h3>聊天与上下文</h3>
          <p v-if="selectedChapter" class="chat-chapter-label">
            {{ selectedChapter.title || selectedId }}
          </p>
          <p v-else class="chat-chapter-label muted">请先选择左侧章节</p>
        </div>
        <div class="chat-header-actions">
          <button
            type="button"
            class="btn btn-sm danger-outline-btn"
            :disabled="!selectedId || asking || chatLoading || !chatTurns.length"
            @click="clearChatHistory"
          >
            一键清空对话
          </button>
        </div>
      </header>

      <div class="chat-tabs">
        <button type="button" class="tab" :class="{ active: rightTab === 'chat' }" @click="rightTab = 'chat'">本章对话</button>
        <button type="button" class="tab" :class="{ active: rightTab === 'context' }" @click="rightTab = 'context'">上下文</button>
      </div>
      <div v-show="rightTab === 'chat'" class="chat-authority">
        <span>权限</span>
        <button
          v-for="item in authorityModes"
          :key="item.id"
          type="button"
          class="authority-chip"
          :class="{ active: chatAuthority.mode === item.id }"
          :disabled="!selectedId || asking"
          @click="setChatAuthority(item.id)"
        >
          {{ item.label }}
        </button>
      </div>

      <div v-show="rightTab === 'context'" class="context-panel">
        <section class="context-section shared-context">
          <header class="context-section-header">
            <div>
              <strong>公共项目事实</strong>
              <small>所有章节继承 · 只读</small>
            </div>
            <span v-if="globalProjectContext.global_context_revision" class="context-version">
              r{{ globalProjectContext.global_context_revision }}
            </span>
          </header>
          <div v-if="!globalContextReady" class="context-warning">
            公共项目事实尚未就绪，系统会阻止生成，不能以空上下文继续写作。
          </div>
          <template v-else>
            <dl class="identity-grid">
              <template v-for="item in globalIdentityRows" :key="item.label">
                <dt>{{ item.label }}</dt>
                <dd>{{ item.value }}</dd>
              </template>
            </dl>
            <details v-for="group in globalFactGroups" :key="group.key" class="fact-group" :open="group.key === 'scope' || group.key === 'work_packages'">
              <summary>{{ group.label }} <span>{{ group.items.length }}</span></summary>
              <ul>
                <li v-for="(fact, index) in group.items" :key="`${group.key}-${index}`">{{ fact }}</li>
              </ul>
            </details>
            <p class="context-note">
              已确认详细事实 {{ globalProjectContext.confirmed_facts?.length || 0 }} 条。共同事实只能在项目级统一更新，本章不能覆盖。
            </p>
            <button type="button" class="context-project-link" @click="openProjectContextSource">
              修改公共事实：返回项目流水线
            </button>
          </template>
        </section>

        <section v-if="writingOrientation" class="context-section writing-orientation">
          <header class="context-section-header">
            <div>
              <strong>本章写作处境</strong>
              <small>目的 · 全书位置 · 章节关系</small>
            </div>
            <span class="context-version">{{ writingOrientation.writing_purpose?.role_label || outlineRoleLabel }}</span>
          </header>
          <p v-if="writingOrientation.writing_purpose?.purpose" class="context-note">
            写作目的：{{ writingOrientation.writing_purpose.purpose }}
          </p>
          <p
            v-if="writingOrientation.writing_purpose?.writing_objectives?.length"
            class="context-note"
          >
            写作目标：{{ writingOrientation.writing_purpose.writing_objectives.join('；') }}
          </p>
          <p v-if="orientationPathLabel" class="context-note outline-path">
            全书位置：{{ orientationPathLabel }}
          </p>
          <ul v-if="orientationRelations.length" class="orientation-relations">
            <li v-for="item in orientationRelations" :key="item.chapter_id || item.title">
              <em>{{ item.relation_label || item.relation }}</em>
              {{ item.title }}
              <span v-if="item.content_status && item.content_status !== 'unknown'">
                · {{ outlineContentLabel(item) }}
              </span>
            </li>
          </ul>
          <p v-if="orientationMaterials" class="context-note">
            已有资料：{{ orientationMaterials }}
          </p>
        </section>

        <section v-if="writingOutlineBlocks.length" class="context-section writing-outline-section">
          <header class="context-section-header">
            <div>
              <strong>本章写作提纲</strong>
              <small>按满分条件展开，不写评分术语</small>
            </div>
            <span class="context-version">{{ writingOutlineBlocks.length }} 块</span>
          </header>
          <ol class="writing-outline">
            <li v-for="block in writingOutlineBlocks" :key="block.block_id">
              <em>{{ outlineKindLabel(block.kind) }}</em>
              {{ block.heading }}
              <span>{{ block.must_answer }}</span>
            </li>
          </ol>
        </section>

        <section class="context-section outline-context">
          <header class="context-section-header">
            <div>
              <strong>整份目录</strong>
              <small>默认标题 · 点开才看详情</small>
            </div>
            <span class="context-version">{{ outlineRoleLabel }}</span>
          </header>
          <p v-if="outlinePathLabel" class="context-note outline-path">
            当前位置：{{ outlinePathLabel }}
          </p>
          <p v-if="outlineGuidance" class="context-note">{{ outlineGuidance }}</p>
          <div v-if="!outlineFlat.length" class="empty-hint">
            目录尚未就绪。
          </div>
          <div v-else class="outline-readonly-list" role="tree">
            <button
              v-for="item in outlineFlat"
              :key="item.chapter_id"
              type="button"
              class="outline-readonly-item"
              :class="{
                current: item.is_current,
                empty: !item.has_content,
                leaf: item.is_leaf,
              }"
              :style="{ paddingLeft: `${10 + (item.depth || 0) * 14}px` }"
              :title="item.is_current ? '当前正在编辑的章节' : '点击只读查看（不可修改）'"
              @click="inspectOutlineChapter(item)"
            >
              <span class="outline-readonly-title">{{ item.title || item.chapter_id }}</span>
              <span class="outline-readonly-meta">
                <em v-if="item.is_current">本章</em>
                <em v-else>{{ outlineContentLabel(item) }}</em>
              </span>
            </button>
          </div>

          <div v-if="readonlyView" class="readonly-chapter-panel">
            <header class="context-section-header">
              <div>
                <strong>他章只读</strong>
                <small>不可修改</small>
              </div>
              <button type="button" class="btn btn-ghost btn-tiny" @click="closeReadonlyView">关闭</button>
            </header>
            <div v-if="readonlyLoading" class="empty-hint">加载只读信息…</div>
            <template v-else>
              <div class="context-kind">
                {{ readonlyView.title || readonlyView.chapter_id }}
                · {{ outlineContentLabel(readonlyView) }}
                · 只读
              </div>
              <div v-if="readonlyView.purpose" class="context-body">目的：{{ readonlyView.purpose }}</div>
              <div
                v-if="readonlyView.writing_objectives?.length"
                class="context-body"
              >
                写作目标：{{ readonlyView.writing_objectives.join('；') }}
              </div>
              <div v-if="readonlyView.summary" class="context-body sibling-summary">
                {{ readonlyView.summary }}
              </div>
              <div v-else class="context-src">该章尚无正文摘要</div>
              <article
                v-for="(item, index) in (readonlyView.context_items || [])"
                :key="`${item.kind}-${index}`"
                class="context-card"
              >
                <div class="context-kind">{{ item.kind || '上下文' }}</div>
                <div class="context-title">{{ item.title }}</div>
                <div class="context-body">{{ item.body }}</div>
              </article>
              <p class="context-note">查看不会切换当前编辑章节，也不能在这里修改他章内容。</p>
            </template>
          </div>
        </section>

        <section class="context-section chapter-only-context">
          <header class="context-section-header">
            <div>
              <strong>本章专属要求</strong>
              <small>追加到公共事实之上</small>
            </div>
            <span class="context-version">r{{ chapterContextRef.chapter_context_revision || 0 }}</span>
          </header>
          <div v-if="!chapterRequirements.length && !chapterScoringRequirements.length && !contextItems.length" class="empty-hint">
            当前章节暂无专属要求。目录确认后会从 Blueprint 自动带入。
          </div>
          <article v-for="item in chapterRequirements" :key="item.requirement_id" class="context-card requirement-card">
            <div class="context-kind">招标要求 · {{ item.requirement_id }}</div>
            <div class="context-body">{{ item.text }}</div>
          </article>
          <article v-for="item in chapterScoringRequirements" :key="item.score_point_id" class="context-card scoring-card">
            <div class="context-kind">评分要求 · {{ item.score_point_id }}</div>
            <div class="context-title">{{ item.title }}</div>
            <div class="context-body">{{ item.response_expectation }}</div>
          </article>
          <article v-for="item in contextItems" :key="item.item_id" class="context-card">
            <div class="context-kind">{{ item.kind }}</div>
            <div class="context-title">{{ item.title }}</div>
            <div class="context-body">{{ item.body }}</div>
            <div class="context-src">{{ item.source }}</div>
          </article>
        </section>
      </div>

      <div v-show="rightTab === 'chat'" class="chat-panel">
        <div class="chat-history" ref="chatHistoryEl">
          <div v-if="!selectedId" class="empty-hint">
            选择左侧章节后，将打开该章独立对话；历史不会与其他章节混用。
          </div>
          <div v-else-if="chatLoading" class="empty-hint">正在加载本章对话…</div>
          <div v-else-if="!chatTurns.length" class="empty-hint">
            这是「{{ selectedChapter?.title || selectedId }}」的专属对话。
            Agent 默认只看目录标题；需要时再按需打开他章只读详情。
          </div>
          <article
            v-for="turn in chatTurns"
            :key="turn.id"
            class="chat-bubble"
            :class="[turn.role, { streaming: turn.streaming, editing: turn.editing }]"
          >
            <!-- 用户气泡：右对齐高对比深色大圆角，悬浮删除 -->
            <template v-if="turn.role === 'user'">
              <button
                v-if="canDeleteChatTurn(turn)"
                type="button"
                class="chat-delete-btn"
                title="删除此条对话"
                aria-label="删除此条对话"
                @click="deleteChatTurn(turn)"
              >×</button>
              <div
                class="chat-content user-content"
                :contenteditable="canEditChatTurn(turn)"
                :data-field="`content:${turn.id}`"
                spellcheck="false"
                @focus="onChatTurnFocus(turn)"
                @blur="onChatTurnBlur(turn, 'content', $event)"
              >{{ turn.content }}</div>
            </template>

            <!-- Agent 气泡：已处理时长 + 细分割线 + 正在思考折叠 + 正文 -->
            <template v-else>
              <div class="chat-agent-turn">
                <div class="chat-processed-bar">
                  <span v-if="turn.streaming">已处理 {{ formatTurnDuration(turn) }}</span>
                  <span v-else-if="turn.elapsed_seconds != null || turn.duration">已处理 {{ formatTurnDuration(turn) }}</span>
                  <span v-else>Agent 回复</span>
                </div>
                <div class="chat-divider" />
                <details
                  v-if="turn.thinking || turn.researchSteps?.length || (turn.streaming && !turn.content)"
                  class="chat-thinking-details"
                  :open="Boolean(turn.thinking)"
                >
                  <summary class="chat-thinking-summary">
                    <span
                      class="thinking-summary-label"
                      :class="{ 'thinking-shimmer': turn.streaming && !turn.content }"
                    >
                      {{ turn.streaming ? '正在分析…' : '分析过程' }}
                    </span>
                    <svg v-if="turn.thinking" class="thinking-chevron" viewBox="0 0 20 20" aria-hidden="true">
                      <path d="m7.5 5 5 5-5 5" />
                    </svg>
                  </summary>
                  <div
                    v-if="turn.thinking"
                    class="thinking-body"
                    :contenteditable="canEditChatTurn(turn)"
                    :data-field="`thinking:${turn.id}`"
                    spellcheck="false"
                    @focus="onChatTurnFocus(turn)"
                    @blur="onChatTurnBlur(turn, 'thinking', $event)"
                  >{{ turn.thinking }}</div>
                  <section v-if="turn.researchSteps?.length" class="research-process" aria-label="公开资料搜索过程">
                    <h4>公开资料搜索过程</h4>
                    <ol>
                      <li v-for="(step, stepIndex) in turn.researchSteps" :key="`${step.status}-${stepIndex}`">
                        <strong>{{ researchStepLabel(step.status) }}</strong>
                        <span>{{ step.message }}</span>
                        <div v-if="step.queries?.length" class="research-queries">
                          <span v-for="query in step.queries" :key="query">搜索词：{{ query }}</span>
                        </div>
                        <ul v-if="step.sources?.length" class="research-source-list">
                          <li v-for="(source, sourceIndex) in step.sources" :key="source.source_url || source.url || sourceIndex">
                            <a v-if="source.source_url || source.url" :href="source.source_url || source.url" target="_blank" rel="noopener noreferrer">
                              {{ source.title || source.name || `来源 ${sourceIndex + 1}` }}
                            </a>
                            <span v-else>{{ source.title || source.name || `来源 ${sourceIndex + 1}` }}</span>
                          </li>
                        </ul>
                      </li>
                    </ol>
                  </section>
                </details>

                <div
                  v-if="turn.content"
                  class="chat-content agent-content"
                  :contenteditable="canEditChatTurn(turn)"
                  :data-field="`content:${turn.id}`"
                  spellcheck="false"
                  @focus="onChatTurnFocus(turn)"
                  @blur="onChatTurnBlur(turn, 'content', $event)"
                >{{ turn.content }}</div>
              </div>
            </template>
          </article>
        </div>
        <div class="chat-compose">
          <textarea
            v-model="chatInput"
            rows="3"
            :disabled="!selectedId || asking"
            :placeholder="selectedId
              ? '回车发送，Shift+回车换行。例如：结合评分要求，这一章应强调哪些交付物？'
              : '请先选择章节'"
            @keydown="onChatComposeKeydown"
          />
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!selectedId || asking || !chatInput.trim()"
            @click="sendChat"
          >
            {{ asking ? '思考中…' : '发送' }}
          </button>
        </div>
      </div>
    </aside>

    <ChapterRevisionDrawer
      :open="showRevisions"
      :revisions="revisions"
      :head-revision="chapterDetail?.head_content_revision || 0"
      :formal-revision="chapterDetail?.formal_content_revision || 0"
      @close="showRevisions = false"
      @restore="onRestore"
      @approve="onApproveRevision"
    />

    <!-- 新建章节 Modal -->
    <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
      <div class="modal-card">
        <header class="modal-header">
          <h3>新建章节</h3>
          <button type="button" class="close-btn" @click="showCreateModal = false">&times;</button>
        </header>
        <div class="modal-body">
          <div class="form-group">
            <label>章节 ID</label>
            <input v-model="newChapterId" type="text" class="form-control" placeholder="如 chapter_03" />
            <small class="form-hint">英文字母、数字或下划线</small>
          </div>
          <div class="form-group">
            <label>章节标题</label>
            <input v-model="newChapterTitle" type="text" class="form-control" placeholder="如 3.1 项目管理方案" />
          </div>
        </div>
        <footer class="modal-footer">
          <button type="button" class="btn" @click="showCreateModal = false">取消</button>
          <button type="button" class="btn btn-primary" :disabled="busy || !newChapterId.trim()" @click="handleCreateChapter">
            确认新建
          </button>
        </footer>
      </div>
    </div>

  </div>
</template>

<script setup>
defineOptions({ name: 'ChapterWorkbenchView' })
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchChapterChatHistory,
  clearChapterChatHistory,
  fetchChapterReadonlyView,
  fetchChapter,
  fetchChapterRevisions,
  fetchChapters,
  fetchDocumentCompose,
  downloadV3CurrentWord,
  fetchSnapshot,
  submitV3Command,
  createChapter,
  saveChapterMetadata,
  archiveChapter,
  streamChapterDraft,
  streamChapterChat,
  saveChapterChatTurn,
  appendChapterChatTurn,
  deleteChapterChatTurn,
  saveChapterChatAuthority,
  subscribeV3Workspace,
  createChapterBatchJob,
  fetchCurrentChapterBatchJob,
  fetchChapterBatchJob,
  fetchChapterBatchEvents,
} from '../api'
import {
  hydrateBatchChapterJob,
  initialBatchChapterJobState,
  reduceBatchChapterJobEvents,
} from '../batchChapterJobReducer.js'
import ContentBlockEditor from './ContentBlockEditor.vue'
import ChapterRevisionDrawer from './ChapterRevisionDrawer.vue'

const props = defineProps({
  workspaceId: { type: String, required: true },
  initialChapterId: { type: String, default: '' },
})

const router = useRouter()

const leftWidth = ref(480)
const rightWidth = ref(520)
const isDragging = ref(false)

const workbenchStyle = computed(() => ({
  gridTemplateColumns: `${leftWidth.value}px 6px minmax(0, 1fr) 6px ${rightWidth.value}px`,
}))

function startDragLeft(e) {
  e.preventDefault()
  isDragging.value = true
  const startX = e.clientX
  const startWidth = leftWidth.value

  const onMouseMove = (moveEvent) => {
    const deltaX = moveEvent.clientX - startX
    const newWidth = Math.min(Math.max(startWidth + deltaX, 160), 480)
    leftWidth.value = newWidth
  }

  const onMouseUp = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

function startDragRight(e) {
  e.preventDefault()
  isDragging.value = true
  const startX = e.clientX
  const startWidth = rightWidth.value

  const onMouseMove = (moveEvent) => {
    const deltaX = startX - moveEvent.clientX
    const newWidth = Math.min(Math.max(startWidth + deltaX, 220), 520)
    rightWidth.value = newWidth
  }

  const onMouseUp = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

const items = ref([])
const selectedId = ref('')
const chapterDetail = ref(null)
const revisions = ref([])
const composeResult = ref(null)
const workspaceRevision = ref(0)
const globalProjectContext = ref({})
const chapterWriteJob = ref(null)
const batchWritingProgress = ref(null)
const batchJobState = ref(initialBatchChapterJobState())
let batchPollTimer = null
let batchPolling = false
let batchAutoOpenedJobId = ''
let batchAutoOpenedChapterId = ''

const batchJobItems = computed(() => {
  const jobItems = chapterWriteJob.value?.items
  if (Array.isArray(jobItems)) return jobItems
  return Object.values(batchJobState.value.items || {})
})
const busy = ref(false)
const busyAction = ref('')
const detailLoading = ref(false)
const detailError = ref('')
const listError = ref('')
const actionError = ref('')
const actionMessage = ref('')
const researchGapConfirmation = ref(null)
const remoteHint = ref('')
const showRevisions = ref(false)
const streamingDraft = ref(false)
const streamText = ref('')
const streamOperationId = ref('')
const researchStatus = ref('')
const researchSources = ref([])
const docBodyEl = ref(null)
let draftAbortController = null

const showCreateModal = ref(false)
const newChapterId = ref('')
const newChapterTitle = ref('')
const isSelectingChapters = ref(false)
const selectedWritingChapterIds = ref([])

const editingChapterId = ref('')
const editingTitle = ref('')
const editInputRef = ref(null)

function openCreateModal() {
  const nextNum = (items.value.length + 1).toString().padStart(2, '0')
  newChapterId.value = `chapter_${nextNum}`
  newChapterTitle.value = `${items.value.length + 1}.1 新增章节`
  showCreateModal.value = true
}

async function handleCreateChapter() {
  const cid = newChapterId.value.trim()
  const title = newChapterTitle.value.trim()
  if (!cid) {
    actionError.value = '章节ID不能为空'
    return
  }
  busy.value = true
  actionError.value = ''
  try {
    const { data } = await createChapter(props.workspaceId, cid, title)
    if (!data.ok) throw new Error(data.message || '创建章节失败')
    showCreateModal.value = false
    await loadChapterList()
    if (isSelectingChapters.value && isLeafChapter(items.value.find(item => item.chapter_id === cid))) {
      selectedWritingChapterIds.value = [...new Set([...selectedWritingChapterIds.value, cid])]
    }
    selectChapter(cid)
    actionMessage.value = `章节 ${cid} 创建成功`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

function startRenameChapter(item, e) {
  if (e) e.stopPropagation()
  editingChapterId.value = item.chapter_id
  editingTitle.value = item.title || item.chapter_id
  nextTick(() => {
    if (editInputRef.value) {
      if (Array.isArray(editInputRef.value)) {
        editInputRef.value[0]?.focus()
      } else {
        editInputRef.value?.focus()
      }
    }
  })
}

async function saveRenameChapter(item) {
  if (!editingChapterId.value) return
  const title = editingTitle.value.trim()
  if (!title) {
    cancelRename()
    return
  }
  const cid = item.chapter_id
  editingChapterId.value = ''
  if (title === (item.title || item.chapter_id)) return

  busy.value = true
  try {
    const { data } = await saveChapterMetadata(props.workspaceId, cid, { title })
    if (!data.ok) throw new Error(data.message || '修改章节标题失败')
    await loadChapterList()
    if (selectedId.value === cid) {
      await loadChapterDetail({ force: true })
    }
    actionMessage.value = `章节标题已更新`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

function cancelRename() {
  editingChapterId.value = ''
  editingTitle.value = ''
}

async function handleArchiveChapter(item, e) {
  if (e) e.stopPropagation()
  const name = item.title || item.chapter_id
  if (!confirm(`确定要归档/删除章节 "${name}" 吗？`)) return
  busy.value = true
  try {
    const { data } = await archiveChapter(props.workspaceId, item.chapter_id)
    if (!data.ok) throw new Error(data.message || '归档章节失败')
    await loadChapterList()
    reconcileWritingChapterSelection()
    if (selectedId.value === item.chapter_id) {
      const remaining = items.value.find(i => i.status !== 'archived')
      if (remaining) selectChapter(remaining.chapter_id)
    }
    actionMessage.value = `章节 ${name} 已归档`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

async function handleMoveChapter(item, direction, e) {
  if (e) e.stopPropagation()
  const currentIndex = items.value.findIndex(i => i.chapter_id === item.chapter_id)
  if (currentIndex === -1) return
  const targetIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
  if (targetIndex < 0 || targetIndex >= items.value.length) return

  const currentOrder = Number(item.order || currentIndex + 1)
  const targetItem = items.value[targetIndex]
  const targetOrder = Number(targetItem.order || targetIndex + 1)

  busy.value = true
  try {
    await saveChapterMetadata(props.workspaceId, item.chapter_id, { order: targetOrder })
    await saveChapterMetadata(props.workspaceId, targetItem.chapter_id, { order: currentOrder })
    await loadChapterList()
  } catch (e) {
    actionError.value = '调整排序失败: ' + (e.message || String(e))
  } finally {
    busy.value = false
  }
}

function exportMarkdownOutline() {
  if (!treeItems.value || !treeItems.value.length) return
  let mdText = '# 章节目录\n\n'
  for (const item of treeItems.value) {
    const depth = item.depth || 0
    const indent = '  '.repeat(depth)
    const title = item.title || item.chapter_id
    mdText += `${indent}- ${title}\n`
  }
  const blob = new Blob([mdText], { type: 'text/markdown;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `章节目录_${props.workspaceId || 'outline'}.md`
  a.click()
  URL.revokeObjectURL(url)
}

function exportCurrentWord() {
  downloadV3CurrentWord(props.workspaceId)
}

const rightTab = ref('chat')
const chatInput = ref('')
const chatTurns = ref([])
const chatLoading = ref(false)
const asking = ref(false)
const authorityModes = [
  { id: 'human_review', label: '用户审核' },
  { id: 'delegate_review', label: '替我审核' },
  { id: 'full_authority', label: '完全权限' },
]
const chatAuthority = ref({
  mode: 'human_review',
  review_status: 'idle',
  mode_label: '用户审核',
})
function applyChatAuthority(payload) {
  const next = payload && typeof payload === 'object' ? payload : {}
  chatAuthority.value = {
    mode: String(next.mode || 'human_review'),
    review_status: String(next.review_status || 'idle'),
    mode_label: String(next.mode_label || '用户审核'),
    outline_hash: String(next.outline_hash || ''),
  }
}
const chatHistoryEl = ref(null)
const editorRef = ref(null)
/** In-session cache of chapter dialogue; server history remains source of truth. */
const chatByChapter = new Map()
let chatLoadToken = 0

let closeWorkspaceStream = null

const selectedChapter = computed(() =>
  items.value.find(item => item.chapter_id === selectedId.value) || null,
)
const selectedIsLeaf = computed(() => {
  if (!selectedId.value) return false
  if (typeof chapterDetail.value?.is_leaf === 'boolean') return chapterDetail.value.is_leaf
  if (typeof selectedChapter.value?.is_leaf === 'boolean') return selectedChapter.value.is_leaf
  return !items.value.some(item => item.parent_chapter_id === selectedId.value)
})
const editorBlocks = computed(() => chapterDetail.value?.content?.blocks || [])
const contextItems = computed(() => chapterDetail.value?.context?.items || [])
const chapterRequirements = computed(() => chapterDetail.value?.chapter_requirements || [])
const chapterScoringRequirements = computed(() => chapterDetail.value?.chapter_scoring_requirements || [])
const chapterContextRef = computed(() => chapterDetail.value?.chapter_context_ref || {})
const documentOutlineContext = computed(() => chapterDetail.value?.document_outline_context || null)
const writingOrientation = computed(() => chapterDetail.value?.writing_orientation || null)
const writingOutlineBlocks = computed(() => {
  const rows = chapterDetail.value?.writing_outline?.blocks
  return Array.isArray(rows) ? rows : []
})
function outlineKindLabel(kind) {
  return {
    response: '做法',
    evidence: '证据',
    constraint: '约束',
    quality: '质控',
  }[String(kind || '')] || '要点'
}
const orientationPathLabel = computed(() => String(
  writingOrientation.value?.document_position?.path_label || outlinePathLabel.value || '',
).trim())
const orientationRelations = computed(() => {
  const rows = writingOrientation.value?.chapter_relations?.items
  return Array.isArray(rows) ? rows : []
})
const orientationMaterials = computed(() => {
  const notes = writingOrientation.value?.existing_materials?.notes
  return Array.isArray(notes) && notes.length ? notes.join('；') : ''
})
const outlineFlat = computed(() => {
  const rows = documentOutlineContext.value?.outline
  return Array.isArray(rows) ? rows : []
})
const outlinePathLabel = computed(() => String(documentOutlineContext.value?.position?.path_label || '').trim())
const outlineGuidance = computed(() => {
  const policy = documentOutlineContext.value?.writing_policy
  return String(policy?.guidance || '').trim()
})
const outlineRoleLabel = computed(() => {
  const role = String(documentOutlineContext.value?.current_role || '')
  if (role === 'visual') return '图示/路线图'
  if (role === 'method') return '方法细则'
  if (role === 'overview') return '总体骨架'
  return role || '目录'
})
const readonlyView = ref(null)
const readonlyLoading = ref(false)
const readonlyTargetId = ref('')
const globalContextReady = computed(() => Boolean(
  globalProjectContext.value?.global_context_id
  && Number(globalProjectContext.value?.global_context_revision || 0) > 0
  && globalProjectContext.value?.global_context_hash
  && Object.keys(globalProjectContext.value?.identity || {}).length,
))
const globalIdentityRows = computed(() => {
  const identity = globalProjectContext.value?.identity || {}
  const aliases = [
    ['项目名称', ['project_name', '项目名称', 'project', '项目']],
    ['项目编号', ['project_no', 'project_number', '项目编号', '采购编号', '招标编号']],
    ['采购人', ['purchaser', 'procurer', 'buyer', '采购人', '招标人', '采购单位']],
    ['标包', ['package', 'lot', '标包', '包号', '包件', '标段']],
    ['项目地点', ['location', 'project_location', '项目地点', '服务地点']],
  ]
  return aliases
    .map(([label, keys]) => ({ label, value: keys.map(key => identity[key]).find(Boolean) || '' }))
    .filter(item => item.value)
})
const globalFactGroups = computed(() => [
  ['background', '项目背景'],
  ['goals', '建设目标'],
  ['scope', '采购范围'],
  ['work_packages', '核心工作任务'],
  ['inputs', '输入数据'],
  ['processing', '处理任务'],
  ['outputs', '输出成果'],
  ['deliverables', '交付物'],
  ['acceptance_conditions', '验收条件'],
  ['milestones', '服务期限与进度'],
  ['constraints', '共同约束'],
].map(([key, label]) => ({
  key,
  label,
  items: Array.isArray(globalProjectContext.value?.[key]) ? globalProjectContext.value[key] : [],
})).filter(group => group.items.length))
const materializedCount = computed(() =>
  items.value.filter(item => item.materialized || item.status === 'active').length,
)
const formalCount = computed(() =>
  items.value.filter(item => Number(item.formal_content_revision || 0) > 0).length,
)
const treeItems = computed(() => {
  const byId = new Map(items.value.map(item => [item.chapter_id, item]))
  const childrenByParent = new Map()
  const roots = []
  const compareChapter = (left, right) => {
    const leftOrder = Number(left.order)
    const rightOrder = Number(right.order)
    if (Number.isFinite(leftOrder) && Number.isFinite(rightOrder) && leftOrder !== rightOrder) {
      return leftOrder - rightOrder
    }
    return String(left.title || left.chapter_id).localeCompare(
      String(right.title || right.chapter_id),
      'zh-CN',
      { numeric: true },
    )
  }

  for (const item of items.value) {
    const parentId = String(item.parent_chapter_id || '')
    if (!parentId || !byId.has(parentId) || parentId === item.chapter_id) {
      roots.push(item)
      continue
    }
    const children = childrenByParent.get(parentId) || []
    children.push(item)
    childrenByParent.set(parentId, children)
  }

  const ordered = []
  const visited = new Set()
  const appendBranch = (item, depth) => {
    if (!item || visited.has(item.chapter_id)) return
    visited.add(item.chapter_id)
    const children = [...(childrenByParent.get(item.chapter_id) || [])].sort(compareChapter)
    ordered.push({ ...item, depth: Math.min(depth, 8), has_children: children.length > 0 })
    children.forEach(child => appendBranch(child, depth + 1))
  }

  roots.sort(compareChapter).forEach(root => appendBranch(root, 0))
  items.value.filter(item => !visited.has(item.chapter_id)).sort(compareChapter)
    .forEach(item => appendBranch(item, 0))
  return ordered
})

function isLeafChapter(item) {
  if (!item) return false
  if (item.has_children) return false
  if (typeof item.is_leaf === 'boolean') return item.is_leaf
  return !items.value.some(candidate => candidate.parent_chapter_id === item.chapter_id)
}

const writableLeafChapters = computed(() => treeItems.value.filter(item => (
  item.status !== 'archived' && isLeafChapter(item)
)))

function beginChapterSelection() {
  selectedWritingChapterIds.value = selectedIsLeaf.value && selectedId.value
    ? [selectedId.value]
    : []
  isSelectingChapters.value = true
}

function selectAllWritingChapters() {
  selectedWritingChapterIds.value = writableLeafChapters.value.map(item => item.chapter_id)
}

function clearWritingChapterSelection() {
  selectedWritingChapterIds.value = []
}

function cancelChapterSelection() {
  isSelectingChapters.value = false
  clearWritingChapterSelection()
}

function reconcileWritingChapterSelection() {
  if (!isSelectingChapters.value) return
  const available = new Set(writableLeafChapters.value.map(item => item.chapter_id))
  selectedWritingChapterIds.value = selectedWritingChapterIds.value.filter(id => available.has(id))
}

function leafIdsForChapter(chapter) {
  if (!chapter || chapter.status === 'archived') return []
  if (isLeafChapter(chapter)) return [chapter.chapter_id]
  const descendants = []
  const queue = [chapter.chapter_id]
  while (queue.length) {
    const parentId = queue.shift()
    const children = items.value.filter(item => (
      item.status !== 'archived' && item.parent_chapter_id === parentId
    ))
    for (const child of children) {
      if (isLeafChapter(child)) descendants.push(child.chapter_id)
      else queue.push(child.chapter_id)
    }
  }
  return descendants
}

function isChapterWritingSelected(chapter) {
  const leafIds = leafIdsForChapter(chapter)
  return leafIds.length > 0 && leafIds.every(id => selectedWritingChapterIds.value.includes(id))
}

function toggleChapterWritingSelection(chapter, event) {
  const leafIds = leafIdsForChapter(chapter)
  const selected = new Set(selectedWritingChapterIds.value)
  if (event.target.checked) leafIds.forEach(id => selected.add(id))
  else leafIds.forEach(id => selected.delete(id))
  selectedWritingChapterIds.value = [...selected]
}

function applyBatchJob(job) {
  if (!job) return
  batchJobState.value = hydrateBatchChapterJob(batchJobState.value, job)
  chapterWriteJob.value = job
  openActiveBatchChapter(job)
}

function openActiveBatchChapter(job) {
  const jobId = String(job?.job_id || '')
  const jobItems = Array.isArray(job?.items) ? job.items : []
  const isWritableLeafId = (chapterId) => isLeafChapter(
    items.value.find(item => item.chapter_id === chapterId),
  )
  const candidates = [
    job?.current_chapter_id,
    ...jobItems
      .filter(item => ['running', 'preflight', 'analyzing', 'researching', 'drafting', 'validating', 'committing', 'queued'].includes(item?.status))
      .map(item => item?.chapter_id),
  ].map(value => String(value || '')).filter(Boolean)
  const chapterId = candidates.find(isWritableLeafId) || ''
  if (!jobId || !chapterId || (batchAutoOpenedJobId === jobId && batchAutoOpenedChapterId === chapterId)) return
  batchAutoOpenedJobId = jobId
  batchAutoOpenedChapterId = chapterId
  rightTab.value = 'chat'
  if (selectedId.value !== chapterId) selectChapter(chapterId)

}

function appendBatchEventToChapterChat(event) {
  const chapterId = String(event?.chapter_id || '')
  if (!chapterId) return
  if (
    event?.type === 'chapter_queued'
    || String(event?.status || '') === 'queued'
    || String(event?.stage || '') === 'queued'
  ) return
  const eventId = String(event?.event_id || `${event.sequence || 0}:${event.type || 'event'}`)
  const turnId = `batch-${eventId}`
  const cached = chatByChapter.get(chapterId) || []
  if (cached.some(turn => turn.id === turnId)) return
  const settled = cached.map(turn => turn.batchEvent && turn.streaming
    ? { ...turn, streaming: false }
    : turn)
  const failed = event.type === 'chapter_failed'
  const committed = event.type === 'chapter_committed'
  const message = String(event?.error?.message || event?.message || event?.delta || event?.data?.delta || event?.data?.text || '').trim()
  const code = String(event?.error?.code || '').trim()
  const stage = String(event?.stage || '').trim()
  const title = String(event?.chapter_title || chapterId)
  const content = failed
    ? `《${title}》在 ${stage || '执行'} 阶段失败：${message || '未知错误'}${code ? ` [${code}]` : ''}`
    : (committed ? message : '')
  const thinking = committed || failed
    ? ''
    : `${stage ? `${stage}：` : ''}${message || '章节 Agent 正在处理。'}`
  const next = [...settled, {
    id: turnId,
    turn_id: '',
    role: 'assistant',
    content,
    thinking,
    thinkingOpen: true,
    streaming: !failed && !committed && !['succeeded', 'paused', 'cancelled'].includes(event.status),
    editing: false,
    batchEvent: true,
  }]
  chatByChapter.set(chapterId, next)
  if (selectedId.value === chapterId) chatTurns.value = next
}

function applyBatchEvents(events) {
  if (!Array.isArray(events) || !events.length) return
  events.forEach(appendBatchEventToChapterChat)
  batchJobState.value = reduceBatchChapterJobEvents(batchJobState.value, events)
}

async function pollBatchJob() {
  const jobId = String(chapterWriteJob.value?.job_id || '')
  if (!jobId || batchPolling) return
  batchPolling = true
  try {
    const [jobResponse, eventsResponse] = await Promise.all([
      fetchChapterBatchJob(props.workspaceId, jobId),
      fetchChapterBatchEvents(props.workspaceId, jobId, batchJobState.value.lastSequence || 0),
    ])
    if (jobResponse.data?.job) applyBatchJob(jobResponse.data.job)
    const events = eventsResponse.data?.events || []
    applyBatchEvents(events)
    if (events.some(event => event.type === 'chapter_committed' && event.chapter_id === selectedId.value)) {
      await loadChapterDetail({ force: false, background: true })
    }
    const status = String(jobResponse.data?.job?.status || '')
    if (status === 'succeeded') {
      actionMessage.value = `批量编写完成，共 ${jobResponse.data.job.completed_count || 0} 章。`
      await loadChapterList()
      if (selectedId.value) await loadChapterDetail({ force: false, background: true })
      stopBatchPolling()
    } else if (['paused', 'failed', 'cancelled'].includes(status)) {
      stopBatchPolling()
    }
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    batchPolling = false
  }
}

function startBatchPolling() {
  stopBatchPolling()
  void pollBatchJob()
  batchPollTimer = window.setInterval(() => { void pollBatchJob() }, 1500)
}

function stopBatchPolling() {
  if (batchPollTimer) window.clearInterval(batchPollTimer)
  batchPollTimer = null
}

async function restoreCurrentBatchJob() {
  try {
    const { data } = await fetchCurrentChapterBatchJob(props.workspaceId)
    if (!data?.job) return
    applyBatchJob(data.job)
    const events = await fetchChapterBatchEvents(props.workspaceId, data.job.job_id, 0)
    applyBatchEvents(events.data?.events || [])
    if (['queued', 'running'].includes(data.job.status)) startBatchPolling()
  } catch (_) {
    // Workspace remains usable if no durable batch exists yet.
  }
}

async function startWritingChapters(chapterIds) {
  // The server is authoritative for expanding selected directories into
  // ordered writable leaves. Keep parent IDs here so they are not silently
  // discarded by a stale client-side outline snapshot.
  const ids = [...new Set(chapterIds.map(id => String(id || '').trim()))].filter(Boolean)
  if (!ids.length) {
    actionError.value = '没有可编写的叶子章节。'
    return
  }
  if (editorRef.value?.dirty) {
    actionError.value = '当前章节有未保存修改，请先保存正文再批量编写。'
    return
  }
  busy.value = true
  busyAction.value = 'batch-draft'
  actionError.value = ''
  actionMessage.value = ''
  try {
    const { data } = await createChapterBatchJob(
      props.workspaceId,
      ids,
      `chapter-batch-${Date.now()}`,
    )
    if (!data?.ok || !data?.job) throw new Error(data?.message || '创建批量编写任务失败')
    applyBatchJob(data.job)
    actionMessage.value = `已提交 ${data.job.items?.length || ids.length} 个叶子章节，章节 Agent 已开始处理。`
    startBatchPolling()
  } catch (e) {
    actionError.value = e?.response?.data?.message || e?.response?.data?.error?.message || e.message || String(e)
  } finally {
    busy.value = false
    busyAction.value = ''
  }
}

async function writeSelectedChapters() {
  const ids = [...selectedWritingChapterIds.value]
  isSelectingChapters.value = false
  if (!ids.length) return
  await startWritingChapters(ids)
}

async function writeCurrentChapter() {
  if (!selectedIsLeaf.value || !selectedId.value) {
    actionError.value = '请先在目录中选择一个叶子章节，或使用“选择章节编写”勾选多个章节。'
    return
  }
  // 单章正文始终由本章 Agent 在当前对话中完成，不能绕开它另起 Writer 会话。
  chatInput.value = chatInput.value.trim() || '开始编写本章正文'
  await sendChat()
}

function shortStatus(item) {
  if (!item) return ''
  if (!isLeafChapter(item)) return ''
  if (item.status === 'archived') return '归档'
  if (item.approval_status === 'approved' || Number(item.formal_content_revision || 0) > 0) return '正式'
  if (Number(item.head_content_revision || 0) > 0) return '草稿'
  if (item.materialized || item.status === 'active') return '已开'
  return '未开'
}

function statusClass(item) {
  if (!item) return ''
  if (item.status === 'archived') return 'archived'
  if (item.approval_status === 'approved' || Number(item.formal_content_revision || 0) > 0) return 'ok'
  if (Number(item.head_content_revision || 0) > 0) return 'draft'
  // 工作台初始化会自动物化章节；“已开”不是批量编写状态，不能显示为蓝色。
  // 只有当前批量任务的状态才覆盖默认灰色圆点。
  if (item.materialized || item.status === 'active') return 'ready'
  return 'projected'
}

function batchChapterStatusClass(item) {
  if (item?.approval_status === 'approved' || Number(item?.formal_content_revision || 0) > 0) {
    return ''
  }
  const status = String(batchJobItems.value.find(candidate => (
    candidate.chapter_id === item?.chapter_id
  ))?.status || '')
  return status ? `batch-${status}` : ''
}

function isMultiChapterQueued(item) {
  if (batchJobItems.value.length <= 1) return false
  const batchItem = batchJobItems.value.find(candidate => (
    (candidate.chapter_id || candidate.chapterId) === item?.chapter_id
  ))
  return String(batchItem?.status || '') === 'queued'
}

function relevanceTierLabel(tier) {
  return {
    project_direct: '本项目资料',
    similar_project: '同类项目资料',
    industry_standard: '行业标准',
  }[String(tier || '')] || '公开线索'
}

function openProjectContextSource() {
  router.push(`/business/${encodeURIComponent(props.workspaceId)}/pipeline`).catch(() => {})
}

function backToAssistant() {
  router.push(`/business/${encodeURIComponent(props.workspaceId)}/pipeline`).catch(() => {})
}

async function refreshSnapshotRevision() {
  const snap = await fetchSnapshot(props.workspaceId)
  if (snap.data?.ok) {
    workspaceRevision.value = Number(snap.data.snapshot?.workspace_revision || 0)
    globalProjectContext.value = snap.data.snapshot?.global_project_context || {}
    const job = snap.data.snapshot?.chapter_write_job || null
    // Blocked and failed jobs are historical results.  Keeping them in this
    // banner makes a later one-click draft look like it was rejected by that
    // old batch operation.
    if (!chapterWriteJob.value?.job_id && ['queued', 'running'].includes(job?.status)) {
      chapterWriteJob.value = job
    }
  }
}

async function loadChapterList() {
  listError.value = ''
  try {
    const { data } = await fetchChapters(props.workspaceId)
    if (!data.ok) throw new Error(data.message || '加载目录失败')
    items.value = data.chapters?.items || []
    await ensureChaptersReady()
    reconcileWritingChapterSelection()
    if (chapterWriteJob.value) openActiveBatchChapter(chapterWriteJob.value)
    if (!selectedId.value) {
      const prefer = props.initialChapterId
        || items.value.find(item => item.materialized)?.chapter_id
        || items.value[0]?.chapter_id
        || ''
      if (prefer) selectedId.value = prefer
    }
  } catch (e) {
    listError.value = e?.response?.data?.message || e.message || String(e)
  }
}

async function ensureChaptersReady() {
  // 批量 Worker 正在管理章节物化和写入；此时不能再发起全量初始化命令，
  // 否则会被工作区互斥锁拒绝，并把正常的任务状态误显示成页面错误。
  if (['queued', 'running'].includes(String(chapterWriteJob.value?.status || ''))) return
  const pending = items.value.filter(item => !item.materialized && item.status === 'projected')
  if (!pending.length) return
  await refreshSnapshotRevision()
  const { data } = await submitV3Command(props.workspaceId, {
    kind: 'chapter.workspace.ensure_all',
    payload: {},
    expected_revision: workspaceRevision.value,
    idempotency_key: `chapter.workspace.ensure_all-${Date.now()}`,
  })
  if (!data?.ok) {
    throw new Error(
      data?.receipt?.error?.message || data?.message || '自动打开章节工作台失败',
    )
  }
  const refreshed = await fetchChapters(props.workspaceId)
  if (refreshed.data?.ok) items.value = refreshed.data.chapters?.items || items.value
}

async function loadChapterDetail(options = {}) {
  const { force = true, background = false } = options
  if (background && streamingDraft.value) return
  if (!selectedId.value) {
    chapterDetail.value = null
    detailError.value = ''
    return
  }
  const dirty = editorRef.value?.dirty
  if (dirty && !force) {
    remoteHint.value = '远端已更新；本地有未保存编辑，未覆盖草稿'
    return
  }
  const requestedChapterId = selectedId.value
  if (!background || !chapterDetail.value) detailLoading.value = true
  if (!background) {
    actionError.value = ''
    detailError.value = ''
  }
  try {
    const { data } = await fetchChapter(props.workspaceId, requestedChapterId)
    if (!data.ok) throw new Error(data.message || '加载章节失败')
    if (requestedChapterId !== selectedId.value) return
    const currentContent = chapterDetail.value?.content
    const nextContent = data.chapter?.content
    const currentSignature = `${currentContent?.content_revision || 0}:${currentContent?.content_hash || ''}`
    const nextSignature = `${nextContent?.content_revision || 0}:${nextContent?.content_hash || ''}`
    chapterDetail.value = currentContent && currentSignature === nextSignature
      ? { ...data.chapter, content: currentContent }
      : data.chapter
    detailError.value = ''
    remoteHint.value = ''
    if (!globalProjectContext.value?.global_context_id) {
      await refreshSnapshotRevision()
    }
  } catch (e) {
    if (!background) detailError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    if (requestedChapterId === selectedId.value) detailLoading.value = false
  }
}

async function reloadAll() {
  busy.value = true
  try {
    await loadChapterList()
    await Promise.all([
      loadChapterDetail({ force: true }),
      selectedId.value ? loadChapterChat(selectedId.value, { force: true }) : Promise.resolve(),
    ])
  } finally {
    busy.value = false
  }
}

function outlineContentLabel(item) {
  const status = String(item?.content_status || '')
  if (status === 'formal') return '正式'
  if (status === 'draft') return `草稿 r${item?.content_revision || 0}`
  if (item?.is_leaf === false) return '结构'
  return '无正文'
}

async function inspectOutlineChapter(item) {
  const targetId = String(item?.chapter_id || '').trim()
  const viewerId = String(selectedId.value || '').trim()
  if (!targetId || !viewerId) return
  if (item?.is_current || targetId === viewerId) {
    readonlyView.value = null
    readonlyTargetId.value = ''
    return
  }
  readonlyLoading.value = true
  readonlyTargetId.value = targetId
  actionError.value = ''
  try {
    const { data } = await fetchChapterReadonlyView(props.workspaceId, viewerId, targetId)
    if (readonlyTargetId.value !== targetId) return
    if (!data?.ok) throw new Error(data?.message || '加载他章只读信息失败')
    readonlyView.value = data.chapter_view || null
    rightTab.value = 'context'
  } catch (e) {
    if (readonlyTargetId.value !== targetId) return
    actionError.value = e?.response?.data?.message || e.message || String(e)
    readonlyView.value = null
  } finally {
    if (readonlyTargetId.value === targetId) readonlyLoading.value = false
  }
}

function closeReadonlyView() {
  readonlyView.value = null
  readonlyTargetId.value = ''
  readonlyLoading.value = false
}

function selectChapter(chapterId) {
  if (selectedId.value === chapterId) return
  if (editorRef.value?.dirty) {
    actionError.value = '当前章节有未保存修改，请先保存正文再切换章节。'
    return
  }
  if (draftAbortController) draftAbortController.abort()
  draftAbortController = null
  streamingDraft.value = false
  busy.value = false
  busyAction.value = ''
  streamText.value = ''
  streamOperationId.value = ''
  researchStatus.value = ''
  researchSources.value = []
  detailError.value = ''
  chapterDetail.value = null
  selectedId.value = chapterId
  router.replace(`/business/${props.workspaceId}/chapters/${encodeURIComponent(chapterId)}`).catch(() => {})
}

async function runCommand(kind, payload, successText = '', action = '') {
  busy.value = true
  busyAction.value = action
  actionError.value = ''
  actionMessage.value = ''
  try {
    await refreshSnapshotRevision()
    if (selectedId.value) {
      const latest = await fetchChapter(props.workspaceId, selectedId.value)
      if (latest.data?.ok) {
        const rev = Number(latest.data.chapter?.chapter_revision || 0)
        payload = { ...payload, expected_chapter_revision: rev }
        if (!editorRef.value?.dirty) chapterDetail.value = latest.data.chapter
      }
    }
    const { data } = await submitV3Command(props.workspaceId, {
      kind,
      payload,
      expected_revision: workspaceRevision.value,
      idempotency_key: `${kind}-${selectedId.value || 'ws'}-${Date.now()}`,
    })
    if (!data.ok) {
      throw new Error(
        data.receipt?.error?.message || data.message || data.receipt?.message || '命令失败',
      )
    }
    await loadChapterList()
    await loadChapterDetail({ force: true })
    if (showRevisions.value) {
      const rev = await fetchChapterRevisions(props.workspaceId, selectedId.value)
      if (rev.data?.ok) revisions.value = rev.data.revisions || []
    }
    actionMessage.value = successText || data.message || data.receipt?.message || '已完成'
    return true
  } catch (e) {
    actionError.value = e?.response?.data?.message
      || e?.response?.data?.error?.message
      || e.message
      || String(e)
    return false
  } finally {
    busy.value = false
    busyAction.value = ''
  }
}

function streamDeltaText(event) {
  const payload = event?.data && typeof event.data === 'object' ? event.data : event
  return String(payload?.text ?? payload?.delta ?? payload?.content ?? '')
}

function shouldFollowDraft() {
  const el = docBodyEl.value
  if (!el) return false
  return el.scrollHeight - el.scrollTop - el.clientHeight <= 80
}

async function appendDraftDelta(text) {
  if (!text) return
  const follow = shouldFollowDraft()
  streamText.value += text
  if (follow) {
    await nextTick()
    const el = docBodyEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
}

async function generateDraft(options = {}) {
  if (!selectedId.value) return
  if (!selectedIsLeaf.value) {
    actionError.value = '目录父节点只保留标题，不生成正文；请选择下级叶子章节。'
    return
  }
  if (editorRef.value?.dirty) {
    actionError.value = '当前章节有未保存修改，请先保存后再生成草稿。'
    return
  }
  const chapterId = selectedId.value
  const allowResearchGap = Boolean(options.allowResearchGap)
  draftAbortController?.abort()
  draftAbortController = new AbortController()
  const controller = draftAbortController
  const operationId = `draft-${chapterId}-${Date.now()}`
  streamOperationId.value = operationId
  streamText.value = ''
  researchStatus.value = ''
  researchSources.value = []
  streamingDraft.value = true
  busy.value = true
  busyAction.value = 'draft'
  actionError.value = ''
  actionMessage.value = ''
  researchGapConfirmation.value = null
  rightTab.value = 'chat'
  const draftStartedAt = Date.now()
  const draftTurnId = `draft-${operationId}`
  const draftTurn = {
    id: draftTurnId,
    turn_id: '',
    role: 'assistant',
    content: '',
    thinking: '',
    thinkingOpen: true,
    streaming: true,
    editing: false,
    started_at: draftStartedAt,
    researchSteps: [],
  }
  startStreamingTimer()
  chatTurns.value = [...chatTurns.value, draftTurn]
  rememberChapterChat(chapterId, chatTurns.value)
  await scrollChatToBottom()
  const patchDraftTurn = (mutator) => {
    const source = selectedId.value === chapterId
      ? chatTurns.value
      : (chatByChapter.get(chapterId) || [])
    const next = source.map((turn) => {
      if (turn.id !== draftTurnId) return turn
      const copy = { ...turn }
      mutator(copy)
      return copy
    })
    rememberChapterChat(chapterId, next)
    if (selectedId.value === chapterId) chatTurns.value = next
  }
  let streamCompleted = false
  let completedChapter = null
  let completedContent = null
  try {
    await refreshSnapshotRevision()
    const globalRef = globalProjectContext.value || {}
    const chapterRef = chapterDetail.value?.chapter_context_ref || {}
    if (!globalContextReady.value) {
      throw new Error('公共项目事实尚未就绪，已阻止生成。请先完成项目理解并晋级。')
    }
    if (!chapterRef.chapter_context_id || !chapterRef.chapter_context_hash) {
      throw new Error('本章上下文版本缺失，已阻止生成。请刷新章节后重试。')
    }
    await streamChapterDraft(props.workspaceId, chapterId, {
      expected_revision: workspaceRevision.value,
      expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
      idempotency_key: operationId,
      overwrite_locked: false,
      global_context_id: globalRef.global_context_id,
      global_context_revision: Number(globalRef.global_context_revision),
      global_context_hash: globalRef.global_context_hash,
      chapter_context_id: chapterRef.chapter_context_id,
      chapter_context_revision: Number(chapterRef.chapter_context_revision || 0),
      chapter_context_hash: chapterRef.chapter_context_hash,
      allow_research_gap: allowResearchGap,
      instruction: String(options.instruction || '').trim(),
    }, {
      signal: controller.signal,
      onEvent: (event) => {
        if (controller.signal.aborted || chapterId !== selectedId.value) return
        const type = String(event?.type || event?.event || '').toLowerCase()
        if (type === 'meta') {
          streamOperationId.value = String(event.operation_id || event?.data?.operation_id || operationId)
        } else if (type === 'thinking_step') {
          const note = normalizeAgentMessage(event.message || event?.data?.message || '')
          if (!note) return
          patchDraftTurn((turn) => {
            turn.thinking = turn.thinking ? `${turn.thinking}\n${note}` : note
            turn.thinkingOpen = true
            turn.streaming = true
          })
          scrollChatToBottom()
        } else if (type === 'research') {
          const payload = event?.data && typeof event.data === 'object' ? event.data : event
          const note = normalizeAgentMessage(payload.message || '正在检索公开资料…')
          researchStatus.value = note
          researchSources.value = Array.isArray(payload.sources) ? payload.sources : []
          if (note) {
            patchDraftTurn((turn) => {
              turn.thinking = turn.thinking ? `${turn.thinking}\n${note}` : note
              turn.researchSteps = [
                ...(turn.researchSteps || []),
                {
                  status: String(payload.status || 'processing'),
                  message: note,
                  queries: Array.isArray(payload.queries) ? payload.queries.filter(Boolean) : [],
                  sources: Array.isArray(payload.sources) ? payload.sources : [],
                },
              ]
              turn.thinkingOpen = true
              turn.streaming = true
            })
            scrollChatToBottom()
          }
        } else if (type === 'thinking_delta') {
          const delta = String(event.delta || event?.data?.delta || '')
          if (!delta) return
          patchDraftTurn((turn) => {
            turn.thinking = `${turn.thinking || ''}${delta}`
            turn.thinkingOpen = true
            turn.streaming = true
          })
          scrollChatToBottom()
        } else if (['delta', 'content_delta', 'token'].includes(type)) {
          appendDraftDelta(streamDeltaText(event))
        } else if (type === 'done') {
          const payload = event?.data && typeof event.data === 'object' ? event.data : event
          streamCompleted = true
          completedChapter = payload?.chapter && typeof payload.chapter === 'object'
            ? payload.chapter
            : null
          completedContent = payload?.content && typeof payload.content === 'object'
            ? payload.content
            : null
          } else if (type === 'error') {
            const payload = event?.data && typeof event.data === 'object' ? event.data : event
            const reason = String(payload?.details?.error || payload?.details?.reason || '').trim()
            const message = String(payload?.message || '流式生成失败')
            const code = String(payload?.code || '').trim()
          if (['CHAPTER_RESEARCH_UNAVAILABLE', 'CHAPTER_RESEARCH_CONFIRMATION_REQUIRED'].includes(code)) {
            researchStatus.value = reason ? `公开资料检索失败：${reason}` : message
          }
          if (String(payload?.code || '') === 'CHAPTER_OUTLINE_REVIEW_REQUIRED') {
            rightTab.value = 'chat'
            applyChatAuthority(payload.authority || { mode: 'human_review', review_status: 'pending' })
            chatInput.value = chatInput.value || '先列出本章要写的内容'
          }
            const detail = draftUserErrorMessage(code, message, reason)
            const error = new Error(detail)
            error.code = code
            error.details = payload?.details || {}
            throw error
          }
      },
    })
    if (controller.signal.aborted || chapterId !== selectedId.value) return
    if (!streamCompleted) throw new Error('流式连接提前结束，未收到完成事件')
    if (completedChapter || completedContent) {
      const current = chapterDetail.value || {}
      chapterDetail.value = {
        ...current,
        ...(completedChapter || {}),
        content: completedContent || completedChapter?.content || current.content,
      }
    }
    streamingDraft.value = false
    streamText.value = ''
    researchStatus.value = ''
    researchSources.value = []
    remoteHint.value = ''
    actionMessage.value = '草稿已生成'
    patchDraftTurn((turn) => {
      turn.streaming = false
      turn.thinkingOpen = true
      turn.elapsed_seconds = Math.max(1, Math.round((Date.now() - draftStartedAt) / 1000))
      if (!turn.content) turn.content = '已生成本章草稿。思考过程见上方，正文已写入中间文档。'
    })
    await loadChapterList()
    await loadChapterDetail({ force: true, background: true })
  } catch (e) {
    if (e?.name !== 'AbortError') {
      actionError.value = e?.message || String(e)
      if (e?.code === 'CHAPTER_RESEARCH_CONFIRMATION_REQUIRED') {
        researchGapConfirmation.value = {
        message: Number(e?.details?.candidate_count || 0) > 0
          ? `检索返回了 ${e.details.candidate_count} 条候选资料，但均未通过本章的关联性和可核验筛选。确认后将只使用现有项目资料继续写作。`
          : '未得到可用于本章的公开资料。确认后将只使用现有项目资料继续写作。',
          details: e.details || {},
          candidates: Array.isArray(e?.details?.candidates) ? e.details.candidates : [],
        }
      }
      remoteHint.value = '流式连接已中断，已保留当前预览；可刷新检查后端是否已完成。'
    }
  } finally {
    if (draftAbortController === controller) draftAbortController = null
    if (chapterId === selectedId.value) {
      streamingDraft.value = false
      busy.value = false
      busyAction.value = ''
    }
    patchDraftTurn((turn) => {
      turn.streaming = false
      turn.elapsed_seconds = Math.max(1, Math.round((Date.now() - draftStartedAt) / 1000))
      if (turn.content) return
      if (controller.signal.aborted) {
        turn.content = '草稿生成已中断。思考过程保留在本条对话中。'
      } else if (actionError.value) {
        turn.content = `草稿未完成：${actionError.value}`
      } else if (turn.thinking) {
        turn.content = '已生成本章草稿。思考过程见上方，正文已写入中间文档。'
      }
    })
    await persistDraftTurn(
      chapterId,
      draftTurnId,
      streamOperationId.value || operationId,
      streamCompleted ? 'succeeded' : (controller.signal.aborted ? 'interrupted' : 'failed'),
    )
    stopStreamingTimer()
  }
}

async function confirmResearchGapAndGenerate() {
  if (!researchGapConfirmation.value) return
  researchGapConfirmation.value = null
  chatInput.value = '确认仅使用现有项目资料继续写正文'
  await sendChat()
}

function onSaveBlocks(operations) {
  return runCommand('chapter.content.apply', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    operations,
  }, '正文已保存')
}

function onRestore(item) {
  return runCommand('chapter.revision.restore', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    from_content_revision: Number(item.content_revision),
  }, `已恢复 r${item.content_revision}`)
}

function onApproveRevision(item) {
  return runCommand('chapter.approval.confirm', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    content_revision: Number(item.content_revision),
    content_hash: item.content_hash,
  }, `正文已确认 r${item.content_revision}`)
}

function approveHead() {
  const content = chapterDetail.value?.content
  if (!content) {
    actionError.value = '当前没有可确认的正文草稿'
    return false
  }
  return onApproveRevision(content)
}

async function composeCheck() {
  busy.value = true
  actionError.value = ''
  try {
    const { data } = await fetchDocumentCompose(props.workspaceId)
    if (!data.ok) throw new Error(data.message || '组装失败')
    composeResult.value = data.document
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

async function openRevisions() {
  showRevisions.value = true
  if (!selectedId.value) return
  const rev = await fetchChapterRevisions(props.workspaceId, selectedId.value)
  if (rev.data?.ok) revisions.value = rev.data.revisions || []
}

watch(showRevisions, (open) => {
  if (open) openRevisions()
})

const currentTimestamp = ref(Date.now())
let streamingTimer = null

function startStreamingTimer() {
  if (streamingTimer) return
  currentTimestamp.value = Date.now()
  streamingTimer = setInterval(() => {
    currentTimestamp.value = Date.now()
  }, 300)
}

function stopStreamingTimer() {
  if (streamingTimer) {
    clearInterval(streamingTimer)
    streamingTimer = null
  }
}

onUnmounted(() => {
  stopStreamingTimer()
})

function mapChatTurns(turns) {
  return (Array.isArray(turns) ? turns : []).map((turn, index) => ({
    id: String(turn.turn_id || `${turn.role || 'turn'}-${turn.created_at || index}-${index}`),
    turn_id: String(turn.turn_id || ''),
    role: turn.role === 'user' ? 'user' : 'assistant',
    content: normalizeDisplayedText(turn.content),
    thinking: normalizeDisplayedText(turn.thinking),
    thinkingOpen: true,
    streaming: false,
    editing: false,
    created_at: turn.created_at || '',
    duration: turn.duration || '',
    elapsed_seconds: turn.elapsed_seconds != null ? Number(turn.elapsed_seconds) : null,
    researchSteps: Array.isArray(turn.researchSteps)
      ? turn.researchSteps
      : (Array.isArray(turn.research_steps) ? turn.research_steps : []),
    operation_id: String(turn.operation_id || ''),
    status: String(turn.status || ''),
  }))
}

function normalizeAgentMessage(message) {
  const text = String(message || '').trim()
  if (/^queued\s*:/i.test(text)) return text.replace(/^queued\s*:/i, '已进入编写队列：')
  return text
}

function normalizeDisplayedText(value) {
  const text = normalizeAgentMessage(value)
  if (text.includes('G4_CONTENT_TOO_SHORT_OR_HOLLOW')) {
    return '草稿未完成：本次生成的正文过短或缺少实质内容，已停止写入。请补充本章写作要点后重试。'
  }
  if (text.includes('CHAPTER_WRITE_REQUEST_INVALID')) {
    return '草稿未完成：正文生成请求未通过校验，请检查本章提纲和上下文后重试。'
  }
  const sanitized = text
    .replace(/\[[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\]/g, '')
    .replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g, '')
    .replace(/^\s*[:：-]\s*/, '')
    .trim()
  return sanitized || (text ? '操作未完成，请重试。' : '')
}

function researchStepLabel(status) {
  return ({
    required: '需要搜索',
    not_required: '无需搜索',
    searching: '正在搜索',
    published: '搜索完成',
    skipped: '已跳过搜索',
    failed: '搜索失败',
    processing: '正在处理',
  })[String(status || '')] || '搜索进度'
}

function draftUserErrorMessage(code, message, reason) {
  const raw = `${message || ''} ${reason || ''}`
  if (raw.includes('G4_CONTENT_TOO_SHORT_OR_HOLLOW')) {
    return '本次生成的正文过短或缺少实质内容，已停止写入草稿。请补充本章写作要点后重试。'
  }
  if (code === 'CHAPTER_WRITE_REQUEST_INVALID') {
    return normalizeAgentMessage(message) || '正文生成请求未通过校验，请检查本章提纲和上下文后重试。'
  }
  if (code === 'CHAPTER_RESEARCH_CONFIRMATION_REQUIRED') {
    return normalizeAgentMessage(message) || '公开资料检索未完成，请确认是否使用现有项目资料继续写作。'
  }
  const detail = reason ? `${message}（${reason}）` : message
  return normalizeAgentMessage(detail || '正文生成失败，请重试。')
}

function formatTurnDuration(turn) {
  if (!turn) return '1秒'
  if (turn.streaming) {
    const started = Number(turn.started_at) || currentTimestamp.value
    const sec = Math.max(1, Math.floor((currentTimestamp.value - started) / 1000))
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return m > 0 ? `${m}分 ${s}秒` : `${s}秒`
  }
  if (turn.duration) return turn.duration
  if (turn.elapsed_seconds != null) {
    const sec = Number(turn.elapsed_seconds) || 0
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return m > 0 ? `${m}分 ${s}秒` : `${s}秒`
  }
  return '1秒'
}

function canEditChatTurn(turn) {
  return Boolean(selectedId.value && turn && !turn.streaming && !asking.value)
}

function canDeleteChatTurn(turn) {
  return Boolean(
    selectedId.value
    && turn
    && !turn.streaming
    && !asking.value
    && (turn.turn_id || turn.created_at),
  )
}

async function deleteChatTurn(turn) {
  const chapterId = String(selectedId.value || '').trim()
  if (!chapterId || !canDeleteChatTurn(turn)) return
  if (!window.confirm('确定删除这条对话吗？删除后无法恢复。')) return
  const currentTurns = chatTurns.value
  try {
    const { data } = await deleteChapterChatTurn(props.workspaceId, chapterId, {
      turn_id: turn.turn_id || '',
      created_at: turn.created_at || '',
      role: turn.role || '',
    })
    if (!data?.ok) throw new Error(data?.message || '删除对话失败')
    const nextTurns = currentTurns.filter(item => item.id !== turn.id)
    chatTurns.value = nextTurns
    rememberChapterChat(chapterId, nextTurns)
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  }
}

async function clearChatHistory() {
  const chapterId = String(selectedId.value || '').trim()
  if (!chapterId || asking.value || chatLoading.value || !chatTurns.value.length) return
  const chapterName = selectedChapter.value?.title || chapterId
  if (!window.confirm(`确定清空「${chapterName}」的全部 Agent 对话吗？删除后无法恢复。`)) return
  try {
    const { data } = await clearChapterChatHistory(props.workspaceId, chapterId)
    if (!data?.ok) throw new Error(data?.message || '清空对话失败')
    applyChatAuthority(data.authority || {
      mode: chatAuthority.mode,
      review_status: 'idle',
      outline_hash: '',
    })
    researchGapConfirmation.value = null
    researchStatus.value = ''
    researchSources.value = []
    const emptyTurns = []
    rememberChapterChat(chapterId, emptyTurns)
    if (selectedId.value === chapterId) chatTurns.value = emptyTurns
    actionMessage.value = `已清空「${chapterName}」的 Agent 对话`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  }
}

function onChatComposeKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) {
    return
  }
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  sendChat()
}

function onChatTurnFocus(turn) {
  if (!turn || turn.streaming) return
  turn.editing = true
}

async function onChatTurnBlur(turn, field, event) {
  if (!turn) return
  turn.editing = false
  const chapterId = String(selectedId.value || '').trim()
  if (!chapterId || turn.streaming || asking.value) return
  const next = String(event?.target?.innerText || '').replace(/\u00a0/g, ' ')
  const current = field === 'thinking' ? String(turn.thinking || '') : String(turn.content || '')
  if (next === current) return
  const previous = { content: turn.content, thinking: turn.thinking }
  if (field === 'thinking') turn.thinking = next
  else turn.content = next
  if (!String(turn.content || '').trim() && !String(turn.thinking || '').trim()) {
    turn.content = previous.content
    turn.thinking = previous.thinking
    if (event?.target) event.target.innerText = current
    actionError.value = '正文和思考过程不能同时为空。'
    return
  }
  rememberChapterChat(chapterId, chatTurns.value)
  try {
    const { data } = await saveChapterChatTurn(props.workspaceId, chapterId, {
      turn_id: turn.turn_id || turn.id,
      created_at: turn.created_at || '',
      role: turn.role,
      content: turn.content,
      thinking: turn.thinking,
    })
    if (!data?.ok) throw new Error(data?.message || '保存对话失败')
    if (data.turn?.turn_id) turn.turn_id = String(data.turn.turn_id)
    rememberChapterChat(chapterId, chatTurns.value)
  } catch (e) {
    turn.content = previous.content
    turn.thinking = previous.thinking
    if (event?.target) event.target.innerText = current
    actionError.value = e?.response?.data?.message || e.message || String(e)
  }
}

async function scrollChatToBottom() {
  await nextTick()
  if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
}

function rememberChapterChat(chapterId, turns) {
  if (!chapterId) return
  chatByChapter.set(chapterId, turns)
}

async function persistDraftTurn(chapterId, draftTurnId, operationId, status) {
  const source = selectedId.value === chapterId
    ? chatTurns.value
    : (chatByChapter.get(chapterId) || [])
  const turn = source.find(item => item.id === draftTurnId)
  if (!turn || turn.turn_id) return
  try {
    const { data } = await appendChapterChatTurn(props.workspaceId, chapterId, {
      role: 'assistant',
      content: turn.content || '本次正文生成未留下结果。',
      thinking: turn.thinking || '',
      research_steps: Array.isArray(turn.researchSteps) ? turn.researchSteps : [],
      elapsed_seconds: turn.elapsed_seconds ?? null,
      operation_id: operationId,
      status,
    })
    if (!data?.ok || !data.turn) return
    const persisted = mapChatTurns([data.turn])[0]
    const next = source.map(item => (
      item.id === draftTurnId
        ? { ...item, ...persisted, researchSteps: turn.researchSteps || [] }
        : item
    ))
    rememberChapterChat(chapterId, next)
    if (selectedId.value === chapterId) chatTurns.value = next
  } catch (_) {
    remoteHint.value = '本次执行现场未能保存；请勿刷新页面，并检查服务连接。'
  }
}

async function appendChatOperationResult(chapterId, message) {
  const source = selectedId.value === chapterId
    ? chatTurns.value
    : (chatByChapter.get(chapterId) || [])
  let targetIndex = -1
  for (let index = source.length - 1; index >= 0; index -= 1) {
    if (source[index]?.role === 'assistant') {
      targetIndex = index
      break
    }
  }
  if (targetIndex < 0) return
  const target = source[targetIndex]
  const content = [String(target.content || '').trim(), String(message || '').trim()]
    .filter(Boolean)
    .join('\n\n')
  const next = source.map((turn, index) => (
    index === targetIndex ? { ...turn, content, streaming: false } : turn
  ))
  rememberChapterChat(chapterId, next)
  if (selectedId.value === chapterId) {
    chatTurns.value = next
    await scrollChatToBottom()
  }
  if (!target.turn_id) return
  try {
    await saveChapterChatTurn(props.workspaceId, chapterId, {
      turn_id: target.turn_id,
      created_at: target.created_at || '',
      role: 'assistant',
      content,
      thinking: target.thinking || '',
    })
  } catch (_) {
    // The operation result remains visible locally even if history persistence fails.
  }
}

async function loadChapterChat(chapterId, { force = false } = {}) {
  const id = String(chapterId || '').trim()
  if (!id) {
    chatTurns.value = []
    chatLoading.value = false
    return
  }
  if (!force && chatByChapter.has(id)) {
    chatTurns.value = chatByChapter.get(id)
    await nextTick()
    if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
    return
  }
  const token = ++chatLoadToken
  chatLoading.value = true
  try {
    const { data } = await fetchChapterChatHistory(props.workspaceId, id)
    if (token !== chatLoadToken || selectedId.value !== id) return
    if (!data?.ok) throw new Error(data?.message || '加载本章对话失败')
    applyChatAuthority(data.authority)
    const turns = mapChatTurns([
      ...(Array.isArray(data.batch_turns) ? data.batch_turns : []),
      ...(Array.isArray(data.turns) ? data.turns : []),
    ])
    rememberChapterChat(id, turns)
    chatTurns.value = turns
    await nextTick()
    if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
  } catch (e) {
    if (token !== chatLoadToken || selectedId.value !== id) return
    // Soft-fail: keep empty local thread so user can still type.
    if (!chatByChapter.has(id)) {
      chatTurns.value = []
    }
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    if (token === chatLoadToken) chatLoading.value = false
  }
}

async function setChatAuthority(mode) {
  const chapterId = String(selectedId.value || '').trim()
  if (!chapterId || asking.value) return
  try {
    const { data } = await saveChapterChatAuthority(props.workspaceId, chapterId, {
      mode,
      scope: 'chapter',
    })
    if (!data?.ok) throw new Error(data?.message || '设置权限失败')
    applyChatAuthority(data.authority)
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  }
}

async function sendChat() {
  const text = chatInput.value.trim()
  const chapterId = String(selectedId.value || '').trim()
  if (!text || asking.value || !chapterId) return
  asking.value = true
  actionError.value = ''
  const startTime = Date.now()
  const userTurn = {
    id: `u-${Date.now()}`,
    turn_id: '',
    role: 'user',
    content: text,
    thinking: '',
    thinkingOpen: true,
    streaming: false,
    editing: false,
    created_at: '',
  }
  const assistantId = `a-${Date.now()}`
  const assistantTurn = {
    id: assistantId,
    turn_id: '',
    role: 'assistant',
    content: '',
    thinking: '',
    thinkingOpen: true,
    streaming: true,
    editing: false,
    started_at: startTime,
    created_at: '',
  }
  const seedTurns = [...chatTurns.value, userTurn, assistantTurn]
  chatTurns.value = seedTurns
  rememberChapterChat(chapterId, seedTurns)
  chatInput.value = ''
  startStreamingTimer()
  await scrollChatToBottom()

  const patchAssistant = (mutator) => {
    const source = selectedId.value === chapterId
      ? chatTurns.value
      : (chatByChapter.get(chapterId) || [])
    const next = source.map((turn) => {
      if (turn.id !== assistantId) return turn
      const copy = { ...turn }
      mutator(copy)
      return copy
    })
    rememberChapterChat(chapterId, next)
    if (selectedId.value === chapterId) chatTurns.value = next
  }

  try {
    let completedTurns = null
    let documentWriteCompleted = false
    let documentApprovalRequested = false
    let completedChapter = null
    let completedContent = null
    await streamChapterChat(props.workspaceId, chapterId, text, {
      onEvent: async (event) => {
        const type = String(event?.type || '').toLowerCase()
        if ([
          'thinking_step',
          'inspect_planning',
          'inspecting',
          'inspect_skipped',
          'research',
          'delegate_reviewing',
          'delegate_fixing',
        ].includes(type)) {
          const note = String(event.message || event.reason || '').trim()
          if (!note) return
          patchAssistant((turn) => {
            turn.thinking = turn.thinking
              ? `${turn.thinking}\n${note}`
              : note
            turn.thinkingOpen = true
            turn.streaming = true
          })
          if (selectedId.value === chapterId) await scrollChatToBottom()
        } else if (type === 'authority') {
          applyChatAuthority(event)
          documentApprovalRequested = documentApprovalRequested || event.document_approval_requested === true
        } else if (type === 'writing_meta') {
          streamText.value = ''
          streamingDraft.value = true
          streamOperationId.value = String(event.operation_id || '')
        } else if (type === 'draft_delta') {
          streamingDraft.value = true
          await appendDraftDelta(String(event.delta || ''))
        } else if (type === 'thinking_delta') {
          const delta = String(event.delta || '')
          if (!delta) return
          patchAssistant((turn) => {
            turn.thinking = `${turn.thinking || ''}${delta}`
            turn.thinkingOpen = true
            turn.streaming = true
          })
          if (selectedId.value === chapterId) await scrollChatToBottom()
        } else if (type === 'content_delta') {
          const delta = String(event.delta || '')
          if (!delta) return
          patchAssistant((turn) => {
            turn.content = `${turn.content || ''}${delta}`
            turn.streaming = true
          })
          if (selectedId.value === chapterId) await scrollChatToBottom()
        } else if (type === 'done') {
          documentWriteCompleted = documentWriteCompleted || event.document_write_completed === true
          documentApprovalRequested = documentApprovalRequested || event.document_approval_requested === true
          completedChapter = event?.chapter && typeof event.chapter === 'object' ? event.chapter : null
          completedContent = event?.content && typeof event.content === 'object' ? event.content : null
          const elapsedSec = Math.max(1, Math.round((Date.now() - startTime) / 1000))
          if (Array.isArray(event.turns) && event.turns.length) {
            completedTurns = mapChatTurns(event.turns)
            if (completedTurns.length) {
              const last = completedTurns[completedTurns.length - 1]
              if (last.role === 'assistant' && last.elapsed_seconds == null) {
                last.elapsed_seconds = elapsedSec
              }
            }
          } else {
            patchAssistant((turn) => {
              turn.content = String(event.reply || turn.content || '（无回复）')
              turn.thinking = String(event.thinking || turn.thinking || '')
              turn.streaming = false
              turn.thinkingOpen = true
              turn.elapsed_seconds = elapsedSec
            })
          }
          if (event.workspace_revision != null && selectedId.value === chapterId) {
            workspaceRevision.value = Number(event.workspace_revision)
          }
        } else if (type === 'error') {
          const error = new Error(event.message || '章节对话失败')
          error.code = String(event.code || '')
          error.details = event.details || {}
          throw error
        }
      },
    })

    const finalElapsed = Math.max(1, Math.round((Date.now() - startTime) / 1000))
    if (completedTurns) {
      rememberChapterChat(chapterId, completedTurns)
      if (selectedId.value === chapterId) {
        chatTurns.value = completedTurns
        await scrollChatToBottom()
      }
    } else {
      patchAssistant((turn) => {
        turn.streaming = false
        turn.elapsed_seconds = turn.elapsed_seconds || finalElapsed
        if (!turn.content) turn.content = '（无回复）'
      })
    }
    if (documentWriteCompleted && selectedId.value === chapterId && selectedIsLeaf.value) {
      const current = chapterDetail.value || {}
      chapterDetail.value = {
        ...current,
        ...(completedChapter || {}),
        content: completedContent || completedChapter?.content || current.content,
      }
      streamText.value = ''
      streamingDraft.value = false
      await loadChapterList()
      await loadChapterDetail({ force: true, background: true })
    } else if (documentApprovalRequested && selectedId.value === chapterId && selectedIsLeaf.value) {
      const approved = await approveHead()
      await appendChatOperationResult(
        chapterId,
        approved
          ? '当前正文已确认，并已更新为正式版本。'
          : `正文确认失败：${normalizeDisplayedText(actionError.value || '请刷新章节后重试。')}`,
      )
    }
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
    if (e?.code === 'CHAPTER_RESEARCH_CONFIRMATION_REQUIRED') {
      researchGapConfirmation.value = {
        message: Number(e?.details?.candidate_count || 0) > 0
          ? `检索返回了 ${e.details.candidate_count} 条候选资料，但均未通过本章筛选。确认后由本章 Agent 仅使用现有项目资料继续写作。`
          : '未得到可用于本章的公开资料。确认后由本章 Agent 仅使用现有项目资料继续写作。',
        details: e.details || {},
        candidates: Array.isArray(e?.details?.candidates) ? e.details.candidates : [],
      }
    }
    const errElapsed = Math.max(1, Math.round((Date.now() - startTime) / 1000))
    if (selectedId.value !== chapterId) {
      const cached = chatByChapter.get(chapterId) || []
      rememberChapterChat(
        chapterId,
        cached.map((turn) => (
          turn.id === assistantId
            ? {
                ...turn,
                streaming: false,
                elapsed_seconds: errElapsed,
                content: turn.content || `请求失败：${actionError.value}`,
              }
            : turn
        )),
      )
      return
    }
    patchAssistant((turn) => {
      turn.streaming = false
      turn.elapsed_seconds = errElapsed
      turn.content = turn.content
        ? `${turn.content}\n\n请求失败：${actionError.value}`
        : `请求失败：${actionError.value}`
    })
  } finally {
    asking.value = false
    stopStreamingTimer()
  }
}

function connectWorkspaceStream() {
  closeWorkspaceStream?.()
  closeWorkspaceStream = subscribeV3Workspace(props.workspaceId, {
    onSnapshot: payload => {
      const snapshot = payload?.snapshot || payload || {}
      const nextItems = snapshot?.chapters?.items
      if (Array.isArray(nextItems)) items.value = nextItems
      if (selectedId.value && !streamingDraft.value) {
        void loadChapterDetail({ force: false, background: true })
      }
    },
  })
}

watch(
  () => selectedId.value,
  async (id, prev) => {
    if (!id || id === prev) return
    chatInput.value = ''
    closeReadonlyView()
    await Promise.all([
      loadChapterDetail({ force: true }),
      loadChapterChat(id),
    ])
  },
)

watch(
  () => props.initialChapterId,
  (id) => {
    if (id && id !== selectedId.value) selectChapter(id)
  },
)

onMounted(async () => {
  if (props.initialChapterId) selectedId.value = props.initialChapterId
  await restoreCurrentBatchJob()
  await reloadAll()
  connectWorkspaceStream()
})

onUnmounted(() => {
  stopBatchPolling()
  closeWorkspaceStream?.()
  closeWorkspaceStream = null
  draftAbortController?.abort()
})
</script>

<style scoped>
.workbench {
  display: grid;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #f1f5f9;
}

.workbench.is-dragging {
  user-select: none !important;
  cursor: col-resize !important;
}

.resizer {
  width: 6px;
  background: #f8fafc;
  border-left: 1px solid #e2e8f0;
  border-right: 1px solid #e2e8f0;
  cursor: col-resize;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, border-color 0.15s;
  z-index: 10;
}

.resizer:hover,
.workbench.is-dragging .resizer {
  background: #2563eb;
  border-color: #2563eb;
}

.resizer-handle {
  width: 2px;
  height: 20px;
  background: #cbd5e1;
  border-radius: 1px;
  transition: background 0.15s;
}

.resizer:hover .resizer-handle,
.workbench.is-dragging .resizer-handle {
  background: #ffffff;
}

.pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  background: #fff;
}
.pane-chat {
  border-left: none;
}
.pane-header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: flex-start;
  padding: 12px 14px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}
.kicker {
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #64748b;
}
.pane-header h3 {
  margin: 2px 0 0;
  font-size: 15px;
  color: #0f172a;
}
.chat-chapter-label {
  margin: 4px 0 0;
  font-size: 12px;
  color: #1d4ed8;
  line-height: 1.4;
  word-break: break-word;
}
.chat-chapter-label.muted {
  color: #94a3b8;
}
.chat-header-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
}
.danger-outline-btn {
  border-color: #fecaca;
  color: #b91c1c;
  background: #fff;
}
.danger-outline-btn:hover:not(:disabled) {
  background: #fef2f2;
  border-color: #fca5a5;
}
.tree-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}
.tree-toolbar > .btn {
  flex: 1 1 calc(50% - 3px);
  min-width: 0;
  white-space: nowrap;
}
.tree-stats {
  display: flex;
  gap: 10px;
  padding: 6px 12px;
  font-size: 11px;
  color: #64748b;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}
.tree-list {
  flex: 1;
  overflow: auto;
  padding: 8px;
}
.pane-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.back-assistant-btn {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.back-assistant-btn svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.tree-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  padding: 8px 10px;
  text-align: left;
  cursor: pointer;
  margin-bottom: 2px;
}
.tree-indent {
  width: calc(var(--tree-depth) * 20px);
  height: 1px;
  flex: 0 0 auto;
}
.tree-item:hover { background: #f8fafc; }
.tree-item.active {
  background: rgba(37, 99, 235, 0.08);
  border-color: rgba(37, 99, 235, 0.2);
}
.tree-item.archived { opacity: 0.55; }
.tree-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
  flex-shrink: 0;
}
.tree-dot.ok { background: #22c55e; }
.tree-dot.draft { background: #f59e0b; }
.tree-dot.ready { background: #3b82f6; }
.tree-dot.projected { background: #cbd5e1; }
.tree-dot.archived { background: #94a3b8; }
.tree-dot.batch-queued { background: #2563eb; }
.tree-dot.batch-preflight,
.tree-dot.batch-analyzing,
.tree-dot.batch-researching,
.tree-dot.batch-drafting,
.tree-dot.batch-validating,
.tree-dot.batch-committing,
.tree-dot.batch-running { background: #2563eb; }
.tree-dot.batch-succeeded { background: #22c55e; }
.tree-dot.batch-failed,
.tree-dot.batch-paused { background: #ef4444; }
.tree-dot.batch-skipped,
.tree-dot.batch-cancelled { background: #94a3b8; }
.tree-title-row {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}
.tree-title {
  min-width: 0;
  font-size: 13px;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-queue-label {
  flex: 0 0 auto;
  padding: 1px 5px;
  border: 1px solid #f59e0b;
  border-radius: 999px;
  background: #fffbeb;
  color: #92400e;
  font-size: 11px;
  font-weight: 700;
  line-height: 16px;
}
.tree-meta {
  font-size: 11px;
  color: #94a3b8;
  flex-shrink: 0;
}
.compose-box {
  margin: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex-shrink: 0;
}
.compose-box.ok {
  background: #ecfdf5;
  border-color: #a7f3d0;
}
.doc-header { align-items: center; }
.doc-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
  align-items: center;
}
.doc-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.chapter-doc-body {
  --word-page-width: 850px;
  --word-page-min-height: 1120px;
  flex: 1;
  display: block;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
  padding: 28px 32px 56px;
  background: #e7ebf0;
}
.document-stage {
  width: var(--word-page-width);
  min-height: var(--word-page-min-height);
  margin: 0 auto;
}
.document-paper {
  box-sizing: border-box;
  width: 100%;
  min-height: var(--word-page-min-height);
  padding: 72px 78px;
  border: 1px solid #d5dbe5;
  background: #fff;
  box-shadow: 0 16px 42px rgb(15 23 42 / 10%);
  color: #111827;
  font-family: "SimSun", "Songti SC", "Noto Serif CJK SC", serif;
}
.paper-heading { margin-bottom: 30px; }
.paper-heading h1 {
  margin: 0;
  color: #111827;
  font-family: "SimHei", "Microsoft YaHei", sans-serif;
  font-size: 28px;
  line-height: 1.45;
  font-weight: 700;
  text-align: center;
}
.research-status {
  margin: 0 0 24px;
  padding: 12px 14px;
  border-left: 3px solid #0f766e;
  background: #f0fdfa;
  color: #115e59;
  font-size: 13px;
  line-height: 1.6;
}
.research-status ul { margin: 8px 0 0; padding-left: 18px; }
.orientation-relations {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #334155;
  font-size: 12px;
  line-height: 1.55;
}
.orientation-relations em {
  font-style: normal;
  color: #0f766e;
  font-weight: 700;
}
.writing-outline {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #334155;
  font-size: 12px;
  line-height: 1.55;
}
.writing-outline em {
  font-style: normal;
  color: #7c3aed;
  font-weight: 700;
  margin-right: 4px;
}
.writing-outline span {
  display: block;
  color: #64748b;
  font-size: 11px;
}
.research-status a { color: #0f766e; text-decoration: underline; }
.source-tier {
  display: inline-flex;
  margin-right: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #475569;
  font-size: 10px;
  font-weight: 700;
}
.source-tier.project_direct { background: #dcfce7; color: #166534; }
.source-tier.similar_project { background: #ffedd5; color: #9a3412; }
.source-tier.industry_standard { background: #dbeafe; color: #1d4ed8; }
.document-state {
  min-height: 820px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
  color: #64748b;
  padding: 24px;
}
.document-state h4 { margin: 0; color: #0f172a; font-family: "Microsoft YaHei", sans-serif; }
.document-state p { margin: 0; max-width: 360px; font-family: "Microsoft YaHei", sans-serif; font-size: 13px; }
.document-error h4 { color: #b91c1c; }
.document-error p { color: #7f1d1d; }
.document-loading-mark {
  width: 28px;
  height: 28px;
  border: 3px solid #dbeafe;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: document-loading-spin 800ms linear infinite;
}
@keyframes document-loading-spin {
  to { transform: rotate(360deg); }
}
.pill {
  display: inline-flex;
  padding: 1px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 11px;
}
.pill.ok { background: #dcfce7; color: #166534; }
.pill.draft { background: #ffedd5; color: #9a3412; }
.pill.ready { background: #dbeafe; color: #1d4ed8; }
.banner {
  padding: 8px 14px;
  font-size: 13px;
  flex-shrink: 0;
}
.banner.error { background: #fef2f2; color: #b91c1c; border-bottom: 1px solid #fecaca; }
.banner.ok { background: #ecfdf5; color: #166534; border-bottom: 1px solid #a7f3d0; }
.banner.warning { background: #fffbeb; color: #92400e; border-bottom: 1px solid #fde68a; }
.banner.warning .btn { margin-left: 8px; }
.batch-job-items { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.batch-job-item { border: 1px solid currentColor; border-radius: 999px; padding: 3px 8px; background: rgb(255 255 255 / 70%); color: inherit; font-size: 11px; cursor: pointer; }
.batch-job-item.succeeded { color: #166534; }
.batch-job-item.failed { color: #b91c1c; font-weight: 700; }
.batch-job-item.running { color: #1d4ed8; font-weight: 700; }
.batch-job-actions { display: flex; gap: 6px; margin-top: 7px; }
.chat-tabs {
  display: flex;
  gap: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}
.tab {
  flex: 1;
  border: 1px solid transparent;
  background: #f8fafc;
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 13px;
  cursor: pointer;
  color: #475569;
}
.tab.active {
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
  border-color: rgba(37, 99, 235, 0.2);
  font-weight: 600;
}
.chat-authority {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 10px 0;
  color: #64748b;
  font-size: 11px;
}
.authority-chip {
  border: 1px solid #cbd5e1;
  background: #fff;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  color: #334155;
  cursor: pointer;
}
.authority-chip.active {
  border-color: #2563eb;
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 700;
}
.context-panel,
.chat-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.context-panel {
  overflow: auto;
  padding: 10px;
  gap: 8px;
}
.context-section {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px;
  background: #fff;
}
.shared-context { border-color: #bfdbfe; background: #f8fbff; }
.chapter-only-context { margin-top: 2px; }
.context-section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 9px;
}
.context-section-header strong { display: block; font-size: 13px; color: #0f172a; }
.context-section-header small { display: block; margin-top: 2px; color: #64748b; font-size: 10px; }
.context-version {
  flex-shrink: 0;
  border-radius: 999px;
  padding: 2px 7px;
  background: #e0e7ff;
  color: #3730a3;
  font-size: 10px;
  font-weight: 700;
}
.context-warning {
  padding: 8px;
  border-radius: 7px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 11px;
  line-height: 1.5;
}
.identity-grid {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  gap: 5px 8px;
  margin: 0 0 9px;
  font-size: 11px;
}
.identity-grid dt { color: #64748b; }
.identity-grid dd { margin: 0; color: #0f172a; overflow-wrap: anywhere; }
.fact-group { border-top: 1px solid #dbeafe; padding: 6px 0; }
.fact-group summary { cursor: pointer; color: #1e3a8a; font-size: 11px; font-weight: 700; }
.fact-group summary span { color: #94a3b8; font-weight: 500; }
.fact-group ul { margin: 6px 0 0; padding-left: 17px; color: #334155; font-size: 11px; line-height: 1.55; }
.context-note { margin: 8px 0 0; color: #64748b; font-size: 10px; line-height: 1.5; }
.context-project-link {
  width: 100%;
  margin-top: 7px;
  border: 1px solid #bfdbfe;
  border-radius: 7px;
  padding: 6px 8px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 11px;
  cursor: pointer;
}
.requirement-card { border-left: 3px solid #2563eb; }
.scoring-card { border-left: 3px solid #f59e0b; }
.outline-path { color: #0f766e; font-weight: 600; }
.outline-readonly-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 280px;
  overflow: auto;
  margin-top: 8px;
  padding-right: 2px;
}
.outline-readonly-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  color: #0f172a;
  padding: 7px 10px;
  text-align: left;
  cursor: pointer;
}
.outline-readonly-item:hover { border-color: #93c5fd; background: #eff6ff; }
.outline-readonly-item.current {
  border-color: #2563eb;
  background: #dbeafe;
  cursor: default;
}
.outline-readonly-item.empty { opacity: 0.85; }
.outline-readonly-title {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  word-break: break-word;
}
.outline-readonly-meta em {
  font-style: normal;
  font-size: 10px;
  color: #64748b;
  white-space: nowrap;
}
.outline-readonly-item.current .outline-readonly-meta em { color: #1d4ed8; font-weight: 700; }
.readonly-chapter-panel {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #cbd5e1;
}
.btn-tiny {
  padding: 2px 8px;
  font-size: 11px;
}
.sibling-summary {
  max-height: 7.5em;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.5;
}
.context-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: #f8fafc;
}
.context-kind {
  font-size: 11px;
  color: #2563eb;
  font-weight: 700;
}
.context-title {
  font-size: 13px;
  font-weight: 600;
  margin: 2px 0;
  color: #0f172a;
}
.context-body {
  font-size: 12px;
  color: #334155;
  white-space: pre-wrap;
}
.context-src {
  margin-top: 4px;
  font-size: 11px;
  color: #94a3b8;
}
.chat-history {
  flex: 1;
  overflow: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-bubble {
  position: relative;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.45;
}
.chat-delete-btn {
  position: absolute;
  top: 6px;
  right: 7px;
  display: none;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 4px;
  background: #fee2e2;
  color: #b91c1c;
  font-size: 18px;
  line-height: 18px;
  cursor: pointer;
}
.chat-bubble:hover .chat-delete-btn,
.chat-delete-btn:focus-visible { display: block; }
.chat-delete-btn:hover { background: #fecaca; }
.chat-bubble.user {
  background: #27272a;
  border: none;
  align-self: flex-end;
  max-width: 88%;
  border-radius: 16px;
  color: #ffffff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  padding: 12px 18px;
}
.chat-bubble.user .chat-content {
  color: #ffffff;
}
.chat-bubble.assistant {
  background: transparent;
  border: none;
  align-self: flex-start;
  max-width: 100%;
  padding: 4px 0;
  box-shadow: none;
}
.chat-processed-bar {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 8px;
  font-weight: 500;
}
.chat-divider {
  height: 1px;
  background: #e2e8f0;
  margin: 6px 0 10px;
  width: 100%;
}
.chat-thinking-details {
  margin-bottom: 12px;
}
.chat-thinking-summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
  user-select: none;
  list-style: none;
  padding: 2px 0;
}
.chat-thinking-summary::-webkit-details-marker { display: none; }
.chat-thinking-summary:hover { color: #334155; }
.thinking-summary-label {
  transition: color 0.2s ease;
}
.thinking-summary-label.thinking-shimmer {
  display: inline-block;
  background: linear-gradient(
    90deg,
    #64748b 0%,
    #2563eb 25%,
    #60a5fa 50%,
    #2563eb 75%,
    #64748b 100%
  );
  background-size: 200% 100%;
  color: transparent !important;
  -webkit-background-clip: text;
  background-clip: text;
  animation: thinkingSweep 2s infinite linear;
  font-weight: 600;
}
@keyframes thinkingSweep {
  0% {
    background-position: 100% 0;
  }
  100% {
    background-position: -100% 0;
  }
}
.thinking-chevron {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  transition: transform 0.2s ease;
}
.chat-thinking-details[open] .thinking-chevron {
  transform: rotate(90deg);
}
.thinking-body {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: #475569;
  font-size: 12.5px;
  line-height: 1.6;
  max-height: 280px;
  overflow: auto;
  font-family: inherit;
  outline: none;
  background: #f8fafc;
  border: 1px solid #f1f5f9;
  border-radius: 8px;
  padding: 10px 14px;
}
.research-process {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  background: #f8fbff;
  color: #334155;
}
.research-process h4 { margin: 0 0 8px; color: #1e3a8a; font-size: 12.5px; }
.research-process ol { margin: 0; padding-left: 20px; }
.research-process ol > li { margin: 0 0 8px; padding-left: 2px; }
.research-process ol > li:last-child { margin-bottom: 0; }
.research-process strong { display: block; margin-bottom: 2px; color: #1d4ed8; font-size: 12px; }
.research-process span { display: block; font-size: 12px; line-height: 1.55; }
.research-queries { margin-top: 4px; color: #475569; }
.research-source-list { margin: 5px 0 0; padding-left: 16px; }
.research-source-list li { margin: 2px 0; }
.research-source-list a { color: #1d4ed8; overflow-wrap: anywhere; }
.chat-content.agent-content {
  color: #1e293b;
  font-size: 14px;
  line-height: 1.65;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 14px 18px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.chat-streaming-hint {
  color: #64748b !important;
  font-style: italic;
  font-size: 13px;
  margin: 6px 0 0;
}
.chat-compose {
  border-top: 1px solid #e2e8f0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
  background: #fff;
}
.chat-compose textarea {
  width: 100%;
  resize: vertical;
  min-height: 72px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 8px;
  font: inherit;
}
.empty-hint {
  color: #94a3b8;
  font-size: 12px;
  padding: 12px 4px;
  line-height: 1.5;
}

/* 侧边栏菜单手动修改样式扩展 */
.tree-item {
  position: relative;
  display: grid;
  grid-template-columns: calc(var(--tree-depth) * 20px) 10px minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: #334155;
  transition: all 0.15s ease;
}
.tree-item > .tree-indent { width: auto; grid-column: 1; }
.tree-item > .tree-dot { grid-column: 2; }
.tree-item > .tree-title,
.tree-item > .tree-item-input { grid-column: 3; }
.tree-item > .tree-meta,
.tree-item > .tree-item-actions { grid-column: 4; justify-self: end; }
.tree-item.writing-selecting {
  grid-template-columns: calc(var(--tree-depth) * 20px) 16px 10px minmax(0, 1fr) auto;
}
.tree-item.writing-selecting > .tree-write-checkbox { grid-column: 2; }
.tree-item.writing-selecting > .tree-dot { grid-column: 3; }
.tree-item.writing-selecting > .tree-title,
.tree-item.writing-selecting > .tree-item-input { grid-column: 4; }
.tree-item.writing-selecting > .tree-meta,
.tree-item.writing-selecting > .tree-item-actions { grid-column: 5; }
.tree-write-checkbox {
  width: 14px;
  height: 14px;
  margin: 0;
  cursor: pointer;
}
.tree-item:hover {
  background: #f1f5f9;
}
.tree-item.active {
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
  font-weight: 600;
}
.tree-item-input {
  flex: 1;
  min-width: 0;
  padding: 2px 6px;
  font-size: 12px;
  border: 1px solid #2563eb;
  border-radius: 4px;
  outline: none;
  background: #fff;
}
.tree-item-actions {
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}
.tree-item:hover .tree-item-actions {
  display: flex;
}
.tree-item:hover .tree-meta {
  display: none;
}
.icon-action-btn {
  background: transparent;
  border: none;
  padding: 2px 4px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  opacity: 0.7;
  transition: opacity 0.15s, background 0.15s;
}
.icon-action-btn svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.icon-action-btn:hover:not(:disabled) {
  opacity: 1;
  background: #e2e8f0;
}
.icon-action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.icon-action-btn.danger:hover:not(:disabled) {
  background: #fee2e2;
}

/* Modal 弹窗样式 */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}
.modal-card {
  background: #fff;
  border-radius: 12px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid #e2e8f0;
}
.modal-header h3 { margin: 0; font-size: 16px; color: #0f172a; }
.close-btn { background: none; border: none; font-size: 20px; color: #64748b; cursor: pointer; }
.modal-body { padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-group label { font-size: 13px; font-weight: 600; color: #334155; }
.form-control { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; outline: none; }
.form-control:focus { border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15); }
.form-hint { font-size: 11px; color: #94a3b8; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; background: #f8fafc; border-top: 1px solid #e2e8f0; }

@media (max-width: 1100px) {
  .workbench {
    /* inline style still drives columns; keep media hook for future tweaks */
  }
}
@media (max-width: 720px) {
  .chapter-doc-body { padding: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .document-loading-mark { animation: none; }
}
</style>
