<template>
  <section class="logic-panel" aria-label="章节改写逻辑">
    <div v-if="loading" class="state">正在加载改写方案…</div>
    <div v-else-if="error && !match" class="state error"><p>{{ error }}</p><button v-if="conflict" type="button" @click="$emit('reload')">刷新并恢复</button></div>
    <div v-else-if="!match" class="state">当前章节暂无改写逻辑。</div>
    <template v-else>
      <header class="logic-head">
        <div><small>{{ plan ? '可编辑方案' : '只读分析' }}</small><h3>{{ match.chapter_title }}</h3></div>
        <div class="head-actions"><span v-if="plan">r{{ plan.plan_revision }} · {{ statusLabel(plan.status) }}</span><button v-if="plan" type="button" @click="$emit('history')">版本历史</button></div>
      </header>
      <div v-if="error" class="stale error-banner">{{ error }} <button v-if="conflict" type="button" @click="$emit('reload')">刷新并恢复</button></div>
      <div v-if="plan?.stale" class="stale">方案依赖已变化：{{ plan.stale_reasons.join('、') }}。请重新生成匹配和方案。</div>
      <div class="logic-grid">
        <section>
          <h4>新章节要求</h4><p>{{ match.target?.purpose || '未填写章节目的' }}</p>
          <ul><li v-for="item in match.target?.requirements || []" :key="item.requirement_id || item.text">{{ item.text || item.normalized_requirement }}</li></ul>
          <h4>写作块覆盖</h4>
          <article v-for="item in (plan?.coverage || match.coverage)" :key="item.writing_block_id" class="coverage">
            <strong>{{ item.heading }}</strong><span :class="`status ${item.status}`">{{ coverageLabel(item.status) }}</span><p>{{ item.must_answer }}</p><em v-if="item.risk">{{ item.risk }}</em>
          </article>
        </section>
        <section>
          <h4>旧章节 / 原文块</h4><div v-if="!match.matches.length" class="empty">未找到可复用旧文。</div>
          <div v-for="item in match.matches" :key="`${item.section_id}:${item.block_id}`" class="select-card">
            <label v-if="plan" class="select-row"><input type="checkbox" :checked="isSelected(item.block_id)" :disabled="busy || plan.status === 'confirmed'" @change="toggleBlock(item, $event.target.checked)" />使用此原文块</label>
            <LegacySectionCard :match="item" @preview="preview = $event" />
            <label v-if="plan && isSelected(item.block_id)" class="field">使用方式
              <select :value="selectedUsage(item.block_id)" :disabled="busy || plan.status === 'confirmed'" @change="changeUsage(item.block_id, $event.target.value)">
                <option value="copy">直接复用</option><option value="light_edit">修改复用</option><option value="restructure">重组复用</option><option value="new_write">重新编写</option>
              </select>
            </label>
          </div>
        </section>
        <section>
          <template v-if="plan">
            <h4>方案设置</h4>
            <label class="field">整体策略<select v-model="draftStrategy" :disabled="busy || plan.status === 'confirmed'"><option value="copy">直接复用</option><option value="light_edit">修改复用</option><option value="restructure">重组复用</option><option value="new_write">重新编写</option></select></label>
            <label class="field">改写说明<textarea v-model="draftInstruction" :disabled="busy || plan.status === 'confirmed'" /></label>
            <h4>补写项</h4>
            <article v-for="item in plan.new_content_items" :key="item.item_id" class="new-item">
              <strong>{{ item.instruction }}</strong><small v-if="item.evidence_ids?.length">证据：{{ item.evidence_ids.join('、') }}</small>
              <div class="search-row"><input v-model="searchQueries[item.item_id]" placeholder="公开标准/方法查询目标" :disabled="busy || pendingOps.length > 0" /><button type="button" :disabled="busy || pendingOps.length > 0 || !searchQueries[item.item_id]?.trim()" @click="$emit('search', { itemId: item.item_id, query: searchQueries[item.item_id] })">补充查询</button></div>
              <button type="button" class="danger" :disabled="busy || plan.status === 'confirmed'" @click="queue({ op: 'remove_new_content_item', item_id: item.item_id })">删除补写项</button>
            </article>
            <div class="add-row"><input v-model="newInstruction" placeholder="新增需要补写的内容" /><button type="button" :disabled="busy || !newInstruction.trim()" @click="addNewItem">添加</button></div>
            <h4>污染治理</h4><div v-if="!plan.pollution_findings.length" class="empty ok">未发现旧项目污染。</div>
            <article v-for="finding in plan.pollution_findings" :key="finding.finding_id" class="finding" :class="finding.status">
              <strong>{{ finding.type }}</strong><p>{{ finding.source_text }}</p><small v-if="finding.status === 'resolved'">已替换：{{ finding.replacement_text }}</small>
              <select v-else :disabled="busy || plan.status === 'confirmed'" @change="resolveFinding(finding, $event.target.value)"><option value="">选择已确认事实或新招标原文</option><option v-for="fact in confirmedFacts" :key="fact.fact_id" :value="`fact:${fact.fact_id}`">事实：{{ fact.statement }}</option><option v-for="requirement in plan.target?.requirements || []" :key="requirement.requirement_id" :value="`requirement:${requirement.requirement_id}`">招标：{{ requirement.text || requirement.normalized_requirement }}</option></select>
            </article>
            <div class="plan-actions"><button type="button" :disabled="busy || plan.status === 'confirmed'" @click="saveDraft">保存草稿<span v-if="pendingOps.length">（{{ pendingOps.length }}）</span></button><button v-if="plan.status === 'confirmed'" type="button" :disabled="busy" @click="$emit('reopen')">重新打开</button><button v-else type="button" class="primary" :disabled="busy || pendingOps.length > 0 || plan.stale || unresolvedCount > 0" @click="$emit('confirm')">确认当前方案</button></div>
          </template>
          <template v-else><h4>建议策略</h4><div class="strategy">{{ strategyLabel(match.recommendation?.strategy) }}</div><p>{{ match.recommendation?.reason }}</p></template>
        </section>
      </div>
      <aside v-if="showHistory" class="history"><header><h4>方案版本历史</h4><button type="button" @click="$emit('close-history')">×</button></header><article v-for="item in history" :key="item.plan_revision"><strong>r{{ item.plan_revision }} · {{ statusLabel(item.status) }}</strong><small>{{ item.created_at }}</small><span>{{ strategyLabel(item.strategy) }}</span></article></aside>
    </template>
    <LegacyBlockPreviewDrawer :match="preview" @close="preview = null" />
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import LegacyBlockPreviewDrawer from './LegacyBlockPreviewDrawer.vue'
import LegacySectionCard from './LegacySectionCard.vue'
const props = defineProps({ match: { type: Object, default: null }, plan: { type: Object, default: null }, loading: Boolean, busy: Boolean, error: { type: String, default: '' }, conflict: Boolean, confirmedFacts: { type: Array, default: () => [] }, history: { type: Array, default: () => [] }, showHistory: Boolean })
const emit = defineEmits(['update', 'search', 'confirm', 'reopen', 'reload', 'history', 'close-history'])
const preview = ref(null); const pendingOps = ref([]); const pendingBlockState = ref({}); const pendingUsage = ref({}); const draftStrategy = ref('new_write'); const draftInstruction = ref(''); const newInstruction = ref(''); const searchQueries = ref({})
const unresolvedCount = computed(() => (props.plan?.pollution_findings || []).filter(item => item.status !== 'resolved').length)
watch(() => props.plan?.plan_revision, () => { pendingOps.value = []; pendingBlockState.value = {}; pendingUsage.value = {}; draftStrategy.value = props.plan?.strategy || 'new_write'; draftInstruction.value = props.plan?.instruction || '' })
watch(() => props.match?.chapter_id, () => { preview.value = null; pendingOps.value = []; pendingBlockState.value = {}; pendingUsage.value = {} })
function queue(operation) { pendingOps.value = [...pendingOps.value, operation] }
function isSelected(blockId) { return Object.hasOwn(pendingBlockState.value, blockId) ? pendingBlockState.value[blockId] : (props.plan?.selected_legacy_blocks || []).some(item => item.block_id === blockId) }
function selectedUsage(blockId) { return pendingUsage.value[blockId] || (props.plan?.selected_legacy_blocks || []).find(item => item.block_id === blockId)?.usage || 'light_edit' }
function toggleBlock(item, checked) { pendingBlockState.value = { ...pendingBlockState.value, [item.block_id]: checked }; queue(checked ? { op: 'select_legacy_block', section_id: item.section_id, block_id: item.block_id, content_hash: item.content_hash, usage: 'light_edit' } : { op: 'unselect_legacy_block', block_id: item.block_id }) }
function changeUsage(blockId, usage) { pendingUsage.value = { ...pendingUsage.value, [blockId]: usage }; queue({ op: 'change_block_usage', block_id: blockId, usage }) }
function addNewItem() { queue({ op: 'add_new_content_item', instruction: newInstruction.value.trim() }); newInstruction.value = '' }
function resolveFinding(finding, value) { if (value.startsWith('fact:')) queue({ op: 'resolve_pollution', finding_id: finding.finding_id, replacement_fact_id: value.slice(5) }); else if (value.startsWith('requirement:')) queue({ op: 'resolve_pollution', finding_id: finding.finding_id, replacement_requirement_id: value.slice(12) }) }
function saveDraft() { const ops = [...pendingOps.value]; if (draftStrategy.value !== props.plan?.strategy) ops.push({ op: 'set_strategy', strategy: draftStrategy.value }); if (draftInstruction.value !== (props.plan?.instruction || '')) ops.push({ op: 'update_instruction', instruction: draftInstruction.value }); if (ops.length) emit('update', ops) }
const coverageLabel = value => ({ fully_covered: '完整覆盖', partially_covered: '部分覆盖', not_covered: '未覆盖', conflicted: '冲突' }[value] || value)
const strategyLabel = value => ({ copy: '直接复用', light_edit: '修改复用', restructure: '重组复用', new_write: '重新编写' }[value] || value)
const statusLabel = value => ({ draft: '草稿', confirmed: '已确认', stale: '已过期' }[value] || value)
</script>

<style scoped>
.logic-panel{position:relative;flex:1;min-height:0;overflow:auto;padding:20px;background:#f1f5f9;color:#334155}.state{display:grid;min-height:300px;place-items:center;color:#64748b}.state.error{color:#b91c1c}.logic-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding:14px 16px;border-radius:12px;background:#fff}.logic-head h3{margin:3px 0 0}.head-actions{display:flex;align-items:center;gap:8px}.stale{margin-bottom:12px;padding:10px;background:#fff7ed;color:#9a3412;border-radius:8px}.error-banner{background:#fef2f2;color:#b91c1c}.logic-grid{display:grid;grid-template-columns:minmax(230px,.9fr) minmax(300px,1.25fr) minmax(250px,.8fr);gap:14px}.logic-grid>section{min-width:0;padding:14px;border:1px solid #e2e8f0;border-radius:12px;background:#fff}.logic-grid h4{margin:10px 0;color:#0f172a}.logic-grid ul{padding-left:18px;font-size:13px}.coverage{position:relative;margin:8px 0;padding:10px;border:1px solid #e2e8f0;border-radius:8px}.coverage strong{display:block;padding-right:70px}.coverage p,.coverage em{display:block;margin:5px 0 0;font-size:12px}.coverage em{color:#9a3412}.status{position:absolute;top:8px;right:8px;font-size:10px}.status.fully_covered{color:#15803d}.status.conflicted{color:#b91c1c}.select-card{margin-bottom:12px;padding:8px;border:1px solid #e2e8f0;border-radius:10px}.select-row{display:flex;gap:7px;margin-bottom:7px;font-size:12px;font-weight:700}.field{display:flex;flex-direction:column;gap:5px;margin:10px 0;font-size:12px}.field select,.field textarea,.add-row input,.search-row input,.finding select{padding:7px;border:1px solid #cbd5e1;border-radius:7px}.field textarea{min-height:70px;resize:vertical}.new-item,.finding{margin:8px 0;padding:9px;border:1px solid #e2e8f0;border-radius:8px}.new-item small,.finding small{display:block;margin:5px 0;color:#15803d}.finding.unresolved{border-color:#fdba74;background:#fff7ed}.finding p{font-size:12px}.search-row,.add-row,.plan-actions{display:flex;gap:6px;margin-top:8px}.search-row input,.add-row input{min-width:0;flex:1}.danger{color:#b91c1c}.primary,.strategy{background:#dbeafe;color:#1d4ed8;font-weight:700}.plan-actions{position:sticky;bottom:0;padding-top:10px;background:#fff}.empty{color:#94a3b8}.empty.ok{color:#15803d}.history{position:absolute;z-index:4;inset:70px 20px 20px auto;width:min(380px,80%);padding:14px;overflow:auto;background:#fff;border:1px solid #cbd5e1;border-radius:12px;box-shadow:0 14px 40px rgb(15 23 42 / 18%)}.history header{display:flex;justify-content:space-between}.history article{display:flex;flex-direction:column;gap:3px;padding:9px;border-bottom:1px solid #e2e8f0}.history small{color:#64748b}@media(max-width:1250px){.logic-grid{grid-template-columns:1fr}}
</style>
