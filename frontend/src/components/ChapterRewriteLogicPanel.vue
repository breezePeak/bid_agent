<template>
  <section class="logic-panel" aria-label="章节改写逻辑">
    <div v-if="loading" class="state">正在匹配旧投标书原文…</div>
    <div v-else-if="error" class="state error">{{ error }}</div>
    <div v-else-if="!match" class="state">当前章节暂无改写逻辑。</div>
    <template v-else>
      <header class="logic-head"><div><small>只读分析</small><h3>{{ match.chapter_title }}</h3></div><span>匹配 {{ match.summary?.match_count || 0 }} 段</span></header>
      <div class="logic-grid">
        <section><h4>新章节要求</h4><p>{{ match.target?.purpose || '未填写章节目的' }}</p><ul><li v-for="item in match.target?.requirements || []" :key="item.requirement_id || item.text">{{ item.text || item.normalized_requirement }}</li></ul><h4>写作块</h4><article v-for="item in match.coverage" :key="item.writing_block_id" class="coverage"><strong>{{ item.heading }}</strong><span :class="`status ${item.status}`">{{ coverageLabel(item.status) }}</span><p>{{ item.must_answer }}</p><em v-if="item.risk">{{ item.risk }}</em></article></section>
        <section><h4>命中的旧章节 / 原文块</h4><div v-if="!match.matches.length" class="empty">未找到可复用旧文。</div><LegacySectionCard v-for="item in match.matches" :key="`${item.section_id}:${item.block_id}`" :match="item" @preview="preview = $event" /></section>
        <section><h4>建议策略</h4><div class="strategy">{{ strategyLabel(match.recommendation?.strategy) }}</div><p>{{ match.recommendation?.reason }}</p><h4>覆盖摘要</h4><dl><dt>完整覆盖</dt><dd>{{ match.summary?.fully_covered || 0 }}</dd><dt>部分覆盖</dt><dd>{{ match.summary?.partially_covered || 0 }}</dd><dt>未覆盖</dt><dd>{{ match.summary?.not_covered || 0 }}</dd><dt>冲突</dt><dd>{{ match.summary?.conflicted || 0 }}</dd></dl><p class="notice">此页仅提供可追溯建议，不会确认、执行或写入正文。</p></section>
      </div>
    </template>
    <LegacyBlockPreviewDrawer :match="preview" @close="preview = null" />
  </section>
</template>

<script setup>
import { ref, watch } from 'vue'
import LegacyBlockPreviewDrawer from './LegacyBlockPreviewDrawer.vue'
import LegacySectionCard from './LegacySectionCard.vue'
const props = defineProps({ match: { type: Object, default: null }, loading: Boolean, error: { type: String, default: '' } })
const preview = ref(null)
watch(() => props.match?.chapter_id, () => { preview.value = null })
const coverageLabel = value => ({ fully_covered: '完整覆盖', partially_covered: '部分覆盖', not_covered: '未覆盖', conflicted: '冲突' }[value] || value)
const strategyLabel = value => ({ copy: '原文级复用候选', light_edit: '轻量改写', restructure: '重组改写', new_write: '重新撰写' }[value] || value)
</script>

<style scoped>
.logic-panel { flex: 1; min-height: 0; overflow: auto; padding: 20px; background: #f1f5f9; color: #334155; }
.state { display: grid; min-height: 300px; place-items: center; color: #64748b; }.state.error { color: #b91c1c; }
.logic-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; padding: 14px 16px; border-radius: 12px; background: #fff; }.logic-head h3 { margin: 3px 0 0; }.logic-head span { color: #1d4ed8; font-weight: 700; }
.logic-grid { display: grid; grid-template-columns: minmax(230px, .9fr) minmax(300px, 1.25fr) minmax(220px, .75fr); gap: 14px; }.logic-grid>section { min-width: 0; padding: 14px; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; }.logic-grid h4 { margin: 4px 0 10px; color: #0f172a; }.logic-grid ul { padding-left: 18px; font-size: 13px; }
.coverage { position: relative; margin: 8px 0; padding: 10px; border: 1px solid #e2e8f0; border-radius: 8px; }.coverage strong { display: block; padding-right: 70px; }.coverage p,.coverage em { display: block; margin: 5px 0 0; font-size: 12px; }.coverage em { color: #9a3412; }.status { position: absolute; top: 8px; right: 8px; font-size: 10px; color: #64748b; }.status.fully_covered { color: #15803d; }.status.conflicted { color: #b91c1c; }
.logic-grid :deep(.legacy-card) { margin-bottom: 9px; }.strategy { padding: 10px; border-radius: 8px; background: #dbeafe; color: #1d4ed8; font-weight: 800; }.logic-grid dl { display: grid; grid-template-columns: 1fr auto; gap: 7px; }.logic-grid dd { margin: 0; font-weight: 700; }.notice { margin-top: 18px; padding: 10px; background: #fff7ed; color: #9a3412; font-size: 12px; }.empty { color: #94a3b8; }
@media (max-width: 1250px) { .logic-grid { grid-template-columns: 1fr; } }
</style>
