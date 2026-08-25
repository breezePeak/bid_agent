<template>
  <Teleport to="body">
    <div v-if="match" class="drawer-mask" @click.self="$emit('close')">
      <aside class="drawer" aria-label="旧投标书原文预览">
        <header><div><small>旧投标书原文 · 只读</small><h3>{{ match.section_title }}</h3></div><button type="button" @click="$emit('close')">×</button></header>
        <dl><dt>section_id</dt><dd>{{ match.section_id }}</dd><dt>block_id</dt><dd>{{ match.block_id }}</dd><dt>content_hash</dt><dd>{{ match.content_hash }}</dd></dl>
        <article>{{ match.content }}</article>
        <p>{{ match.reason }}</p>
        <p class="risk">{{ match.risk }}</p>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
defineProps({ match: { type: Object, default: null } })
defineEmits(['close'])
</script>

<style scoped>
.drawer-mask { position: fixed; inset: 0; z-index: 80; background: rgb(15 23 42 / 30%); }
.drawer { position: absolute; top: 0; right: 0; width: min(600px, 92vw); height: 100%; padding: 22px; overflow: auto; background: #fff; box-shadow: -12px 0 35px rgb(15 23 42 / 18%); }
header { display: flex; justify-content: space-between; gap: 20px; border-bottom: 1px solid #e2e8f0; padding-bottom: 14px; }
h3 { margin: 4px 0 0; } button { border: 0; background: transparent; font-size: 26px; cursor: pointer; }
dl { display: grid; grid-template-columns: 100px 1fr; gap: 7px; font-size: 12px; } dt { color: #64748b; } dd { margin: 0; overflow-wrap: anywhere; }
article { margin-top: 20px; padding: 20px; background: #f8fafc; white-space: pre-wrap; line-height: 1.8; }
p { color: #1d4ed8; font-size: 13px; } .risk { color: #9a3412; }
</style>
