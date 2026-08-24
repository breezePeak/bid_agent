<template>
  <Teleport to="body">
    <div v-if="detail" class="plan-drawer-overlay" @click.self="$emit('close')">
      <aside class="plan-drawer" role="dialog" aria-modal="true" aria-labelledby="plan-drawer-title">
        <header>
          <div>
            <p>{{ kindLabel }}</p>
            <h2 id="plan-drawer-title">{{ detailTitle }}</h2>
          </div>
          <button type="button" class="drawer-close" aria-label="关闭详情" @click="$emit('close')">×</button>
        </header>
        <div class="drawer-body">
          <template v-if="kind === 'source'">
            <DetailRow label="来源类型" :value="detail.source_type" />
            <DetailRow label="原始定位" :value="detail.snapshot_ref" />
            <DetailRow label="引用 ID" :value="detail.reference_id" />
            <DetailRow label="内容哈希" :value="detail.content_hash" mono />
            <section v-if="detail.preview"><h3>摘要与证据</h3><p>{{ detail.preview }}</p></section>
          </template>
          <template v-else-if="kind === 'binding'">
            <DetailRow label="来源 ID" :value="detail.source_id" mono />
            <DetailRow label="内容块 ID" :value="detail.content_unit_id" mono />
            <DetailRow label="用途" :value="detail.usage_type" />
            <DetailRow label="必要性" :value="detail.required ? '必需' : '可选'" />
            <section><h3>使用说明</h3><p>{{ detail.instruction }}</p></section>
          </template>
          <template v-else>
            <DetailRow label="内容块 ID" :value="detail.unit_id" mono />
            <DetailRow label="顺序" :value="String(Number(detail.order) + 1)" />
            <section v-if="detail.purpose"><h3>写作目的</h3><p>{{ detail.purpose }}</p></section>
            <section><h3>编写指令</h3><p>{{ detail.instructions }}</p></section>
            <section v-if="detail.must_answer"><h3>必须回答</h3><p>{{ detail.must_answer }}</p></section>
            <section v-if="detail.source_refs?.length"><h3>绑定来源</h3><ul><li v-for="item in detail.source_refs" :key="item">{{ item }}</li></ul></section>
          </template>
          <p class="readonly-note">此视图只读，不会修改规划或正文。</p>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, defineComponent, h } from 'vue'

const props = defineProps({
  detail: { type: Object, default: null },
  kind: { type: String, default: '' },
})

defineEmits(['close'])

const DetailRow = defineComponent({
  props: { label: String, value: String, mono: Boolean },
  setup(rowProps) {
    return () => h('div', { class: 'detail-row' }, [
      h('span', rowProps.label),
      h('strong', { class: rowProps.mono ? 'mono' : '' }, rowProps.value || '—'),
    ])
  },
})

const kindLabel = computed(() => ({ source: '来源详情', binding: '来源用途', unit: '内容块详情' }[props.kind] || '规划详情'))
const detailTitle = computed(() => props.detail?.title || props.detail?.instruction || props.detail?.unit_id || '编写逻辑')
</script>

<style scoped>
.plan-drawer-overlay { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgb(15 23 42 / 48%); }
.plan-drawer { width: min(460px, 92vw); height: 100%; overflow: auto; background: #fff; box-shadow: -18px 0 48px rgb(15 23 42 / 20%); }
.plan-drawer > header { position: sticky; top: 0; z-index: 1; display: flex; justify-content: space-between; gap: 16px; padding: 20px 22px; border-bottom: 1px solid #e2e8f0; background: #fff; }
.plan-drawer header p { margin: 0 0 4px; color: #2563eb; font-size: 11px; font-weight: 700; }
.plan-drawer h2 { margin: 0; color: #0f172a; font-size: 18px; line-height: 1.45; }
.drawer-close { width: 44px; height: 44px; flex: 0 0 auto; border: 1px solid #cbd5e1; border-radius: 10px; background: #fff; color: #475569; font-size: 25px; cursor: pointer; }
.drawer-close:focus-visible { outline: 3px solid rgb(37 99 235 / 28%); }
.drawer-body { padding: 20px 22px 32px; }
:deep(.detail-row) { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 12px; padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-size: 12px; }
:deep(.detail-row span) { color: #64748b; }
:deep(.detail-row strong) { color: #0f172a; overflow-wrap: anywhere; }
:deep(.detail-row .mono), .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
section { margin-top: 20px; }
section h3 { margin: 0 0 7px; color: #0f172a; font-size: 13px; }
section p, section ul { margin: 0; color: #334155; font-size: 13px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
section ul { padding-left: 19px; }
.readonly-note { margin: 24px 0 0; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 8px; background: #f8fafc; color: #475569; font-size: 12px; }
</style>
