<template>
  <div ref="graphEl" class="plan-graph">
    <svg class="plan-lines" aria-hidden="true">
      <path v-for="line in lines" :key="line.key" :d="line.path" :class="line.kind" />
    </svg>

    <section class="graph-column source-column" aria-labelledby="plan-sources-title">
      <header><span>01</span><h3 id="plan-sources-title">来源材料</h3><small>{{ visibleSources.length }} 项</small></header>
      <div v-for="group in sourceGroups" :key="group.type" class="source-group">
        <button
          type="button"
          class="source-group-toggle"
          :aria-expanded="!collapsedTypes.has(group.type)"
          @click="toggleGroup(group.type)"
        >
          <span>{{ group.label }}</span><small>{{ group.items.length }}</small>
        </button>
        <div v-show="!collapsedTypes.has(group.type)" class="graph-card-list">
          <ChapterPlanSourceCard
            v-for="source in group.items"
            :key="source.source_id"
            :ref="el => setSourceRef(source.source_id, el)"
            :source="source"
            @inspect="$emit('inspect', { kind: 'source', value: $event })"
          />
        </div>
      </div>
      <p v-if="!sources.length" class="column-empty">暂无外部来源，本章仅使用规划约束。</p>
    </section>

    <section class="graph-column unit-column" aria-labelledby="plan-units-title">
      <header><span>02</span><h3 id="plan-units-title">计划内容块</h3><small>{{ units.length }} 项</small></header>
      <div class="graph-card-list">
        <ChapterPlanUnitCard
          v-for="unit in units"
          :key="unit.unit_id"
          :ref="el => setUnitRef(unit.unit_id, el)"
          :unit="unit"
          :bindings="bindingsFor(unit.unit_id)"
          @inspect-unit="$emit('inspect', { kind: 'unit', value: $event })"
          @inspect-binding="$emit('inspect', { kind: 'binding', value: $event })"
        />
      </div>
    </section>

    <section class="graph-column target-column" aria-labelledby="plan-target-title">
      <header><span>03</span><h3 id="plan-target-title">当前章节</h3></header>
      <article ref="targetEl" class="target-card">
        <span>写作目标</span>
        <strong>{{ chapter.title || chapter.chapter_id }}</strong>
        <small>{{ chapter.chapter_id }}</small>
        <p>{{ units.length }} 个内容块将共同组成本章正文</p>
      </article>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import ChapterPlanSourceCard from './ChapterPlanSourceCard.vue'
import ChapterPlanUnitCard from './ChapterPlanUnitCard.vue'

const props = defineProps({
  chapter: { type: Object, required: true },
  sources: { type: Array, default: () => [] },
  units: { type: Array, default: () => [] },
  bindings: { type: Array, default: () => [] },
})

defineEmits(['inspect'])

const graphEl = ref(null)
const targetEl = ref(null)
const lines = ref([])
const collapsedTypes = ref(new Set())
const sourceRefs = new Map()
const unitRefs = new Map()
let resizeObserver = null
let frame = 0

const typeLabels = {
  TENDER_REQUIREMENT: '招标要求',
  SCORE_OBLIGATION: '评分义务',
  GLOBAL_PROJECT_FACT: '项目事实',
  CHAPTER_CONTEXT_ITEM: '章节资料',
  USER_MATERIAL_BLOCK: '用户材料',
  SIBLING_REFERENCE: '他章引用',
  WEB_EVIDENCE: '网页证据',
}

const sourceGroups = computed(() => {
  const groups = new Map()
  for (const source of props.sources) {
    const type = source.source_type || 'OTHER'
    if (!groups.has(type)) groups.set(type, [])
    groups.get(type).push(source)
  }
  return [...groups.entries()].map(([type, items]) => ({ type, label: typeLabels[type] || type, items }))
})

const visibleSources = computed(() => props.sources.filter(item => !collapsedTypes.value.has(item.source_type || 'OTHER')))

function nativeElement(component) {
  return component?.$el || component || null
}

function setSourceRef(id, component) {
  const element = nativeElement(component)
  if (element) sourceRefs.set(id, element)
  else sourceRefs.delete(id)
}

function setUnitRef(id, component) {
  const element = nativeElement(component)
  if (element) unitRefs.set(id, element)
  else unitRefs.delete(id)
}

function bindingsFor(unitId) {
  return props.bindings.filter(item => item.content_unit_id === unitId)
}

function connectionPath(from, to, container) {
  const fromRect = from.getBoundingClientRect()
  const toRect = to.getBoundingClientRect()
  const base = container.getBoundingClientRect()
  const x1 = fromRect.right - base.left
  const y1 = fromRect.top + (fromRect.height / 2) - base.top
  const x2 = toRect.left - base.left
  const y2 = toRect.top + (toRect.height / 2) - base.top
  const bend = Math.max(24, (x2 - x1) * 0.46)
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
}

function drawLines() {
  const container = graphEl.value
  const target = targetEl.value
  if (!container || !target) return
  const next = []
  for (const binding of props.bindings) {
    const source = sourceRefs.get(binding.source_id)
    const unit = unitRefs.get(binding.content_unit_id)
    if (source?.offsetParent && unit?.offsetParent) {
      next.push({
        key: `source:${binding.source_id}:${binding.content_unit_id}:${binding.usage_type}`,
        kind: binding.required ? 'required' : 'optional',
        path: connectionPath(source, unit, container),
      })
    }
  }
  for (const unit of props.units) {
    const element = unitRefs.get(unit.unit_id)
    if (element?.offsetParent) {
      next.push({ key: `target:${unit.unit_id}`, kind: 'target', path: connectionPath(element, target, container) })
    }
  }
  lines.value = next
}

function scheduleDraw() {
  cancelAnimationFrame(frame)
  nextTick(() => { frame = requestAnimationFrame(drawLines) })
}

function toggleGroup(type) {
  const next = new Set(collapsedTypes.value)
  if (next.has(type)) next.delete(type)
  else next.add(type)
  collapsedTypes.value = next
  scheduleDraw()
}

watch(() => [props.sources, props.units, props.bindings, props.chapter.chapter_id], scheduleDraw, { deep: true })

onMounted(() => {
  resizeObserver = new ResizeObserver(scheduleDraw)
  if (graphEl.value) resizeObserver.observe(graphEl.value)
  window.addEventListener('resize', scheduleDraw)
  scheduleDraw()
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', scheduleDraw)
  cancelAnimationFrame(frame)
})
</script>

<style scoped>
.plan-graph { position: relative; display: grid; grid-template-columns: minmax(190px, .9fr) minmax(220px, 1fr) minmax(170px, .72fr); gap: 52px; align-items: start; min-width: 720px; padding: 18px; }
.plan-lines { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
.plan-lines path { fill: none; stroke: #94a3b8; stroke-width: 1.4; opacity: .75; }
.plan-lines path.required { stroke: #f59e0b; stroke-width: 1.8; }
.plan-lines path.target { stroke: #60a5fa; }
.graph-column { position: relative; z-index: 1; min-width: 0; }
.graph-column > header { display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; gap: 7px; align-items: center; min-height: 34px; margin-bottom: 10px; }
.graph-column > header > span { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 7px; background: #e2e8f0; color: #475569; font-size: 10px; font-weight: 800; }
.graph-column h3 { margin: 0; color: #0f172a; font-size: 13px; }
.graph-column header small { color: #64748b; font-size: 11px; }
.source-group + .source-group { margin-top: 10px; }
.source-group-toggle { width: 100%; min-height: 44px; display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 7px; padding: 7px 9px; border: 1px solid #e2e8f0; border-radius: 8px; background: #f8fafc; color: #334155; font-size: 11px; font-weight: 700; cursor: pointer; }
.source-group-toggle:focus-visible { outline: 3px solid rgb(37 99 235 / 28%); outline-offset: 2px; }
.source-group-toggle small { min-width: 22px; padding: 1px 6px; border-radius: 999px; background: #e2e8f0; text-align: center; }
.graph-card-list { display: flex; flex-direction: column; gap: 10px; }
.target-card { display: flex; flex-direction: column; gap: 8px; min-height: 142px; padding: 15px; border: 1px solid #86efac; border-radius: 12px; background: #f0fdf4; box-shadow: 0 1px 2px rgb(15 23 42 / 4%); }
.target-card > span { color: #15803d; font-size: 11px; font-weight: 700; }
.target-card strong { color: #14532d; font-size: 14px; line-height: 1.5; }
.target-card small { color: #64748b; font: 11px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; }
.target-card p { margin: auto 0 0; color: #166534; font-size: 12px; line-height: 1.5; }
.column-empty { padding: 16px 12px; border: 1px dashed #cbd5e1; border-radius: 10px; color: #64748b; font-size: 12px; line-height: 1.5; }
@media (max-width: 900px) {
  .plan-graph { grid-template-columns: 1fr; min-width: 0; gap: 18px; }
  .plan-lines { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .plan-source-card, .source-group-toggle { scroll-behavior: auto; }
}
</style>
