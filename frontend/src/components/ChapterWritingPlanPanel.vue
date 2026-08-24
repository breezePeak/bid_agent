<template>
  <section class="writing-plan-panel" :aria-busy="loading" aria-label="章节编写逻辑">
    <div v-if="loading" class="plan-state plan-loading" role="status" aria-live="polite">
      <span class="skeleton title" /><span class="skeleton line" /><span class="skeleton cards" />
      <strong>正在加载本章编写逻辑</strong>
    </div>

    <div v-else-if="error" class="plan-state plan-error" role="alert">
      <strong>编写逻辑加载失败</strong>
      <p>{{ error }}</p>
      <button type="button" @click="$emit('retry')">重新加载</button>
      <small>正文编辑器和本地未保存内容不受影响。</small>
    </div>

    <div v-else-if="!projection?.plan" class="plan-state plan-empty">
      <strong>当前章节仍使用现有内部 WritingPlan</strong>
      <p>新规划视图尚未对本工作空间启用。正文、一键编写和章节对话仍可正常使用。</p>
    </div>

    <template v-else>
      <header class="plan-summary" :class="{ stale: projection.stale }">
        <div>
          <p>只读编写逻辑</p>
          <h2>{{ projection.chapter.title || projection.chapter.chapter_id }}</h2>
          <span>{{ sourceLabel }} · 规划 r{{ projection.plan.plan_revision }}</span>
        </div>
        <div class="plan-summary-badges">
          <span class="readonly-badge">只读</span>
          <span class="status-badge" :class="{ stale: projection.stale }">{{ statusLabel }}</span>
        </div>
      </header>

      <div v-if="projection.stale" class="stale-banner" role="status">
        <strong>规划已陈旧</strong>
        <span>{{ staleReason }}。此提示不会覆盖或修改当前正文。</span>
      </div>

      <div class="plan-graph-scroll">
        <ChapterPlanGraph
          :chapter="projection.chapter"
          :sources="projection.plan.sources"
          :units="projection.plan.content_units"
          :bindings="projection.plan.source_bindings"
          @inspect="openDetail"
        />
      </div>

      <footer class="plan-footnote">
        <span>来源 {{ projection.plan.sources.length }}</span>
        <span>内容块 {{ projection.plan.content_units.length }}</span>
        <span>关系 {{ projection.plan.source_bindings.length }}</span>
        <span>hash {{ projection.plan.plan_hash?.slice(0, 10) }}</span>
      </footer>
    </template>

    <ChapterPlanDetailDrawer :detail="detail" :kind="detailKind" @close="closeDetail" />
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import ChapterPlanDetailDrawer from './ChapterPlanDetailDrawer.vue'
import ChapterPlanGraph from './ChapterPlanGraph.vue'

const props = defineProps({
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  projection: { type: Object, default: null },
})

defineEmits(['retry'])

const detail = ref(null)
const detailKind = ref('')

const statusLabels = {
  current: '当前版本',
  confirmed: '已确认',
  stale_blueprint: '目录已变化',
  stale_global_context: '项目事实已变化',
  stale_chapter_context: '章节资料已变化',
  stale_source: '来源已变化',
  stale_evidence: '证据已变化',
}

const sourceLabels = {
  shadow_builder: '影子规划',
  legacy_projection: 'Legacy 投影',
  agent_proposal: 'Agent 规划',
}

const statusLabel = computed(() => statusLabels[props.projection?.plan?.status] || props.projection?.plan?.status || '未知状态')
const staleReason = computed(() => statusLabels[props.projection?.plan?.status] || '规划依赖已变化')
const sourceLabel = computed(() => sourceLabels[props.projection?.plan?.source] || props.projection?.plan?.source || '章节规划')

function openDetail(payload) {
  detailKind.value = payload?.kind || ''
  detail.value = payload?.value || null
}

function closeDetail() {
  detail.value = null
  detailKind.value = ''
}
</script>

<style scoped>
.writing-plan-panel { min-height: 100%; background: #f8fafc; color: #0f172a; }
.plan-summary { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; padding: 18px 20px 14px; border-bottom: 1px solid #e2e8f0; background: #fff; }
.plan-summary.stale { border-bottom-color: #fecaca; }
.plan-summary p { margin: 0 0 4px; color: #2563eb; font-size: 11px; font-weight: 800; }
.plan-summary h2 { margin: 0; color: #0f172a; font-size: 17px; line-height: 1.45; }
.plan-summary div > span { display: block; margin-top: 4px; color: #64748b; font-size: 11px; }
.plan-summary-badges { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.plan-summary-badges > span { margin: 0; padding: 4px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.readonly-badge { border: 1px solid #cbd5e1; background: #f8fafc; color: #475569 !important; }
.status-badge { border: 1px solid #93c5fd; background: #eff6ff; color: #1d4ed8 !important; }
.status-badge.stale { border-color: #fca5a5; background: #fef2f2; color: #b91c1c !important; }
.stale-banner { display: flex; flex-wrap: wrap; gap: 7px; padding: 10px 20px; border-bottom: 1px solid #fecaca; background: #fef2f2; color: #991b1b; font-size: 12px; line-height: 1.5; }
.plan-graph-scroll { overflow: auto; padding-bottom: 20px; }
.plan-footnote { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 10px 20px; border-top: 1px solid #e2e8f0; background: #fff; color: #64748b; font: 11px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; }
.plan-state { min-height: 620px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 28px; text-align: center; }
.plan-state strong { color: #0f172a; font-size: 15px; }
.plan-state p { max-width: 460px; margin: 0; color: #64748b; font-size: 13px; line-height: 1.6; }
.plan-error strong, .plan-error p { color: #991b1b; }
.plan-error button { min-height: 44px; padding: 8px 16px; border: 1px solid #fca5a5; border-radius: 8px; background: #fff; color: #b91c1c; font-weight: 700; cursor: pointer; }
.plan-error button:focus-visible { outline: 3px solid rgb(185 28 28 / 24%); }
.plan-error small { color: #64748b; font-size: 11px; }
.skeleton { display: block; border-radius: 7px; background: linear-gradient(90deg, #e2e8f0, #f8fafc, #e2e8f0); background-size: 200% 100%; animation: skeleton 1.4s ease-in-out infinite; }
.skeleton.title { width: min(340px, 76%); height: 20px; }
.skeleton.line { width: min(460px, 88%); height: 11px; }
.skeleton.cards { width: min(620px, 96%); height: 180px; margin: 10px 0; }
@keyframes skeleton { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .skeleton { animation: none; } }
</style>
