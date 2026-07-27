<template>
  <section class="v3-workspace">
    <header>
      <div><p class="eyebrow">V3 工作区</p><h2>{{ runId }}</h2><p class="muted">输入、执行与交付状态均来自 V3 控制面。</p></div>
      <div class="actions"><button class="btn" :disabled="loading" @click="refresh">刷新</button><button class="btn btn-primary" :disabled="running || !hasTender" @click="runPipeline">{{ running ? '正在生成…' : '生成文档' }}</button><button class="btn" :disabled="deliveryStatus !== 'ready'" @click="download">下载 Word</button></div>
    </header>
    <p v-if="error" class="message error">{{ error }}</p><p v-else-if="message" class="message">{{ message }}</p>
    <div class="grid">
      <article class="card">
        <h3>上传输入</h3>
        <select v-model="role"><option value="tender">招标文件</option><option value="score">评分文件</option><option value="company">企业材料</option><option value="template">Word 模板</option><option value="reference">参考资料</option><option value="guidance">编制指引</option></select>
        <input type="file" @change="selectedFile = $event.target.files?.[0] || null" />
        <button class="btn btn-primary" :disabled="!selectedFile || uploading" @click="upload">{{ uploading ? '上传中…' : '登记输入' }}</button>
        <ul class="items">
          <li v-for="item in inputs" :key="item.input_id">
            {{ item.filename }} <small>{{ item.role }} · v{{ item.version }}</small>
            <label v-if="deepSeekEligible(item)" class="research-input">
              <input v-model="deepSeekAttachmentIds" type="checkbox" :value="item.input_id" />
              允许本次研究发送给 DeepSeek
            </label>
          </li>
          <li v-if="!inputs.length" class="muted">尚未登记 V3 输入。</li>
        </ul>
        <p class="muted research-notice">只有勾选的活动文件会发送到 DeepSeek；不勾选时只做普通联网搜索。</p>
      </article>
      <article class="card">
        <h3>文档与交付</h3>
        <dl><div><dt>文档模式</dt><dd>{{ document.mode || '待编译' }}</dd></div><div><dt>内容质量</dt><dd>{{ quality.verdict || '待验证' }}</dd></div><div><dt>交付状态</dt><dd>{{ deliveryStatus }}</dd></div><div><dt>内容单元</dt><dd>{{ units.length }}</dd></div></dl>
        <h4>证据缺口</h4>
        <ul class="items">
          <li v-for="need in evidenceNeeds" :key="need.need_id" class="evidence-item">
            <span>{{ need.question }} <small>{{ need.status }}</small></span>
            <button class="btn" :disabled="researchingNeedId === need.need_id || need.status === 'satisfied'" @click="research(need)">
              {{ researchingNeedId === need.need_id ? '检索中…' : 'DeepSeek 检索' }}
            </button>
          </li>
          <li v-if="!evidenceNeeds.length" class="muted">暂无证据缺口。</li>
        </ul>
      </article>
      <article class="card wide"><h3>材料状态</h3><p class="muted">已提供 {{ materials.summary?.provided || 0 }} / {{ materials.summary?.total || 0 }}；缺失 {{ materials.summary?.missing || 0 }}</p><ul class="items"><li v-for="item in materials.items || []" :key="item.requirement_id">{{ item.requirement }} <small>{{ item.status }}</small></li><li v-if="!(materials.items || []).length" class="muted">本项目未识别资格材料条目。</li></ul></article>
      <article class="card wide"><h3>协作对话</h3><p v-if="reply" class="message">{{ reply }}</p><textarea v-model="question" placeholder="直接说要补什么、改什么、查什么。" /><button class="btn btn-primary" :disabled="asking || !question.trim()" @click="ask">{{ asking ? '处理中…' : '发送' }}</button></article>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { chatV3, downloadV3Final, fetchV3WorkspaceSnapshot, resolveV3Research, runV3Pipeline, uploadV3Input } from '../api'
import { isDeepSeekEligibleInput, normalizeV3WorkspaceSnapshot, selectDeepSeekAttachmentIds } from '../api/v3Contracts.js'
const props = defineProps({ runId: { type: String, required: true } })
const snapshot = ref({}); const loading = ref(false); const running = ref(false); const uploading = ref(false); const asking = ref(false); const researchingNeedId = ref(''); const error = ref(''); const message = ref(''); const reply = ref(''); const question = ref(''); const role = ref('tender'); const selectedFile = ref(null); const deepSeekAttachmentIds = ref([]); let timer = null
const document = computed(() => snapshot.value.document || {}); const inputs = computed(() => snapshot.value.inputs?.inputs || []); const units = computed(() => snapshot.value.content_units || []); const quality = computed(() => snapshot.value.quality?.report || {}); const materials = computed(() => snapshot.value.materials || {}); const evidenceNeeds = computed(() => snapshot.value.evidence_needs || []); const deliveryStatus = computed(() => document.value.delivery?.status || 'new'); const hasTender = computed(() => inputs.value.some(item => item.active && item.role === 'tender'))
async function refresh () { if (!props.runId) return; loading.value = true; try { const { data } = await fetchV3WorkspaceSnapshot(props.runId); snapshot.value = normalizeV3WorkspaceSnapshot(data); error.value = '' } catch (e) { error.value = e.response?.data?.message || 'V3 工作区状态读取失败。' } finally { loading.value = false } }
async function upload () { uploading.value = true; try { await uploadV3Input(props.runId, role.value, selectedFile.value); selectedFile.value = null; message.value = '输入已登记。'; await refresh() } catch (e) { error.value = e.response?.data?.message || '输入上传失败。' } finally { uploading.value = false } }
async function runPipeline () { running.value = true; try { const { data } = await runV3Pipeline(props.runId); message.value = data.message || 'V3 文档已生成。'; await refresh() } catch (e) { error.value = e.response?.data?.message || 'V3 Pipeline 执行失败。' } finally { running.value = false } }
function deepSeekEligible (item) { return isDeepSeekEligibleInput(item) }
async function research (need) {
  researchingNeedId.value = need.need_id
  error.value = ''
  try {
    const attachments = selectDeepSeekAttachmentIds(inputs.value, deepSeekAttachmentIds.value)
    const { data } = await resolveV3Research(props.runId, need.need_id, attachments)
    if (!data.ok) throw new Error(data.message || data.receipt?.message || 'DeepSeek 检索失败。')
    message.value = data.message || data.receipt?.message || 'DeepSeek 检索完成。'
    deepSeekAttachmentIds.value = []
    await refresh()
  } catch (e) {
    error.value = e.response?.data?.message || e.message || 'DeepSeek 检索失败。'
  } finally {
    researchingNeedId.value = ''
  }
}
function download () { downloadV3Final(props.runId) }
async function ask () { asking.value = true; try { const { data } = await chatV3(props.runId, question.value); reply.value = data.reply || ''; question.value = '' } catch (e) { error.value = e.response?.data?.message || '对话处理失败。' } finally { asking.value = false } }
watch(() => props.runId, refresh); onMounted(() => { refresh(); timer = window.setInterval(refresh, 3000) }); onUnmounted(() => window.clearInterval(timer))
</script>

<style scoped>
.v3-workspace{padding:28px;max-width:1200px;margin:0 auto}.v3-workspace header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}.eyebrow{color:#0b6e4f;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:0}.v3-workspace h2{margin:4px 0}.muted{color:#6b7280}.actions{display:flex;gap:8px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:20px}.card{border:1px solid #dbe3e8;border-radius:12px;background:#fff;padding:18px}.wide{grid-column:1/-1}.card h3{margin-top:0}.card input,.card select{display:block;width:100%;margin-bottom:10px}.items{padding-left:18px;margin:14px 0}.items li{margin:8px 0}.items small{color:#6b7280;margin-left:6px}.research-input{display:flex;align-items:center;gap:6px;margin-top:6px;font-size:12px;color:#0b6e4f}.card .research-input input{display:inline-block;width:auto;margin:0}.research-notice{font-size:12px}.evidence-item{display:flex;align-items:center;justify-content:space-between;gap:12px}.evidence-item .btn{flex:0 0 auto}dl div{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #eef2f4}dt{color:#6b7280}.message{margin:16px 0;padding:10px;border-radius:8px;background:#ecfdf5}.error{background:#fef2f2;color:#991b1b}@media(max-width:760px){.v3-workspace header{flex-direction:column}.grid{grid-template-columns:1fr}.wide{grid-column:auto}.evidence-item{align-items:flex-start;flex-direction:column}}
</style>
