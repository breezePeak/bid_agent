<template>
  <article class="plan-unit-card">
    <button
      type="button"
      class="unit-main"
      :aria-label="`查看计划内容块详情：${unit.title}`"
      @click="$emit('inspect-unit', unit)"
    >
      <span class="unit-order">内容块 {{ unit.order + 1 }}</span>
      <strong>{{ unit.title }}</strong>
      <span v-if="unit.purpose" class="unit-purpose">{{ unit.purpose }}</span>
      <span v-if="unit.must_answer" class="unit-question">必须回答：{{ unit.must_answer }}</span>
    </button>
    <div v-if="bindings.length" class="unit-bindings" aria-label="来源用途">
      <button
        v-for="binding in bindings"
        :key="`${binding.source_id}:${binding.usage_type}`"
        type="button"
        class="binding-chip"
        :class="{ required: binding.required }"
        @click="$emit('inspect-binding', binding)"
      >
        {{ usageLabel(binding.usage_type) }}<span v-if="binding.required"> · 必需</span>
      </button>
    </div>
  </article>
</template>

<script setup>
defineProps({
  unit: { type: Object, required: true },
  bindings: { type: Array, default: () => [] },
})

defineEmits(['inspect-unit', 'inspect-binding'])

const labels = {
  constraint: '约束',
  base_fact: '基础事实',
  support: '支撑',
  supplement: '补充',
  evidence: '证据',
  cross_reference: '交叉引用',
}

function usageLabel(value) {
  return labels[value] || value
}
</script>

<style scoped>
.plan-unit-card { border: 1px solid #bfdbfe; border-radius: 10px; background: #f8fbff; overflow: hidden; }
.unit-main { width: 100%; display: flex; flex-direction: column; gap: 6px; padding: 12px; border: 0; background: transparent; color: #0f172a; text-align: left; cursor: pointer; }
.unit-main:hover { background: #eff6ff; }
.unit-main:focus-visible, .binding-chip:focus-visible { outline: 3px solid rgb(37 99 235 / 28%); outline-offset: -2px; }
.unit-order { color: #2563eb; font-size: 11px; font-weight: 700; }
.unit-main strong { font-size: 13px; line-height: 1.45; }
.unit-purpose { color: #475569; font-size: 12px; line-height: 1.45; }
.unit-question { color: #713f12; font-size: 11px; line-height: 1.45; }
.unit-bindings { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 12px 12px; }
.binding-chip { min-height: 44px; padding: 4px 11px; border: 1px solid #cbd5e1; border-radius: 999px; background: #fff; color: #475569; font-size: 11px; cursor: pointer; }
.binding-chip.required { border-color: #fdba74; background: #fff7ed; color: #9a3412; }
</style>
