<template>
  <Teleport to="body">
    <div v-if="visible" class="dialog-overlay" @click.self="$emit('close')">
      <div class="dialog settings-dialog">
        <div class="dialog-header">
          <h2>模型与流程设置</h2>
          <button class="btn btn-icon" @click="$emit('close')">&times;</button>
        </div>
        <div class="settings-dialog-body">
        <div class="settings-tabs" role="tablist" aria-label="设置分类">
          <button class="btn btn-sm" :class="{ 'btn-primary': activeTab === 'model' }" role="tab" :aria-selected="activeTab === 'model'" @click="activeTab = 'model'">模型设置</button>
          <button class="btn btn-sm" :class="{ 'btn-primary': activeTab === 'flow' }" role="tab" :aria-selected="activeTab === 'flow'" @click="activeTab = 'flow'">流程设置</button>
        </div>
        <div v-if="activeTab === 'model'" class="settings-layout">
          <div class="settings-list">
            <button class="btn btn-sm btn-block settings-add-btn" @click="startNewModel">
              + 添加模型
            </button>
            <div
              v-for="m in models"
              :key="m.id"
              class="settings-list-item"
              :class="{
                active: m.id === activeId,
                editing: m.id === editingId,
              }"
              @click="selectModel(m.id)"
            >
              <div class="settings-list-item-main">
                <div class="settings-list-item-name">
                  {{ m.name || '未命名' }}
                  <span v-if="m.id === activeId" class="settings-active-badge">使用中</span>
                </div>
                <div class="settings-list-item-meta">{{ (m.provider || 'openai') + ' · ' + (m.model || '—') }}</div>
              </div>
              <button
                class="settings-list-item-delete"
                title="删除"
                @click.stop="handleDelete(m.id)"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </button>
            </div>
            <div v-if="models.length === 0" class="settings-list-empty">暂无模型，点击上方添加</div>
          </div>

          <form class="settings-form" @submit.prevent="handleSave">
            <div class="settings-form-title">
              {{ isNew ? '新增模型' : '编辑模型' }}
              <span v-if="!isNew && form.id === activeId" class="settings-active-badge">使用中</span>
            </div>
            <div class="form-group">
              <label for="llm-name">模型别名 <span class="required">*</span></label>
              <input
                id="llm-name"
                v-model="form.name"
                type="text"
                maxlength="48"
                placeholder="例如：默认模型 / GLM / GPT"
                required
              />
            </div>
            <div class="form-group">
              <label for="llm-provider">API 格式 <span class="required">*</span></label>
              <select id="llm-provider" v-model="form.provider" required @change="onProviderChange">
                <option value="openai">OpenAI 兼容（/chat/completions）</option>
                <option value="anthropic">Anthropic（/v1/messages）</option>
              </select>
              <p class="field-hint">{{ providerHint }}</p>
            </div>
            <div class="form-group">
              <label for="llm-base-url">Base URL <span class="required">*</span></label>
              <input
                id="llm-base-url"
                v-model="form.base_url"
                type="text"
                placeholder="https://api.openai.com/v1"
                required
              />
            </div>
            <div class="form-group">
              <label for="llm-api-key">API Key <span class="required">*</span></label>
              <div class="input-with-action">
                <input
                  id="llm-api-key"
                  v-model="form.api_key"
                  :type="showApiKey ? 'text' : 'password'"
                  :placeholder="form.has_stored_api_key ? '已配置；留空表示不修改' : 'sk-...'"
                  :required="isNew || !form.has_stored_api_key"
                />
                <button type="button" class="btn btn-sm" @click="showApiKey = !showApiKey">
                  {{ showApiKey ? '隐藏' : '显示' }}
                </button>
              </div>
              <p v-if="form.has_stored_api_key && !form.api_key" class="field-hint">
                已配置 API Key。出于安全原因不会回显；留空保存会保留原值。
              </p>
            </div>
            <div class="form-group">
              <label for="llm-model">模型 ID <span class="required">*</span></label>
              <input
                id="llm-model"
                v-model="form.model"
                type="text"
                placeholder="gpt-4.1-mini"
                required
              />
            </div>
            <div v-if="form.provider === 'openai'" class="form-group">
              <label for="llm-reasoning-effort">思考等级（reasoning_effort）</label>
              <select id="llm-reasoning-effort" v-model="form.reasoning_effort">
                <option value="">跟随模型默认（不传）</option>
                <option value="none">无（none）</option>
                <option value="low">低（low）</option>
                <option value="medium">中（medium）</option>
                <option value="high">高（high）</option>
                <option value="xhigh">超高（xhigh）</option>
                <option value="max">最高（max）</option>
              </select>
              <p class="field-hint">仅用于支持该参数的 OpenAI 兼容模型；CPA 的 Luna 可在此选择思考等级。</p>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label for="llm-timeout">超时（秒）</label>
                <input id="llm-timeout" v-model.number="form.timeout" type="number" min="1" placeholder="300" />
              </div>
              <div class="form-group">
                <label for="llm-retries">最大重试次数</label>
                <input id="llm-retries" v-model.number="form.max_retries" type="number" min="1" placeholder="3" />
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label for="llm-retry-initial">重试初始延迟（秒）</label>
                <input id="llm-retry-initial" v-model.number="form.retry_initial_delay" type="number" min="0" step="0.1" placeholder="2" />
              </div>
              <div class="form-group">
                <label for="llm-retry-max">重试最大延迟（秒）</label>
                <input id="llm-retry-max" v-model.number="form.retry_max_delay" type="number" min="0" step="0.1" placeholder="30" />
              </div>
            </div>
            <div class="form-group form-check-group">
              <label class="form-check">
                <input v-model="form.stream" type="checkbox" />
                <span>流式输出（stream）</span>
              </label>
              <label class="form-check">
                <input v-model="form.verify_ssl" type="checkbox" />
                <span>校验 TLS 证书（verify_ssl）</span>
              </label>
              <p class="field-hint">
                中转站出现 SSLError / ASN1 NOT_ENOUGH_DATA 时，可取消勾选后保存再测。
              </p>
              <label class="form-check" style="display:none">
              </label>
            </div>
            <p class="settings-hint">
              修改「使用中」模型后会实时生效；所有工作空间后续发起的大模型请求都会使用新配置。
            </p>
            <p v-if="testResult" class="settings-test-result" :class="{ ok: testOk === true, bad: testOk === false }">{{ testResult }}</p>
            <p v-if="error" class="form-error">{{ error }}</p>
            <p v-if="success" class="form-success">{{ success }}</p>
            <div class="settings-form-footer">
              <button type="button" class="btn" @click="$emit('close')">关闭</button>
              <div class="settings-form-footer-right">
                <button
                  type="button"
                  class="btn"
                  :disabled="testing || submitting"
                  @click="handleTest"
                >
                  {{ testing ? '测试中...' : '测试连接' }}
                </button>
                <button
                  v-if="!isNew && form.id !== activeId"
                  type="button"
                  class="btn"
                  :disabled="!form.id"
                  @click="handleActivate"
                >
                  设为使用中
                </button>
                <button type="submit" class="btn btn-primary" :disabled="submitting">
                  {{ submitting ? '保存中...' : (isNew ? '添加' : '保存') }}
                </button>
                <button type="button" class="btn btn-primary" :disabled="submitting" @click="handleSave(true)">
                  保存并使用
                </button>
              </div>
            </div>
          </form>
        </div>
        <form v-else class="settings-form flow-settings-form" @submit.prevent="saveFlow">
          <div class="settings-form-title">系统参数</div>
          <p class="settings-hint">修改后只作用于之后启动的任务；流水线启动时会冻结设置快照，运行中修改不会改变当前任务。</p>
          <div class="form-group">
            <label for="flow-research-provider">联网搜索 Provider</label>
            <select id="flow-research-provider" v-model="flowForm.research_provider">
              <option value="tavily">Tavily API</option>
              <option value="disabled">不联网搜索</option>
            </select>
            <p class="field-hint">保存后，后续启动的章节写作在缺公开依据时只会调用 Tavily API。</p>
          </div>
          <div v-if="flowForm.research_provider === 'tavily'" class="form-group">
            <label for="flow-tavily-key">Tavily API Key</label>
            <input id="flow-tavily-key" v-model="flowForm.tavily_api_key" type="password" autocomplete="new-password" :placeholder="flowForm.has_tavily_api_key ? '已保存；留空保持不变' : 'tvly-…'" />
            <p class="field-hint" :class="{ 'form-error': !flowForm.tavily_runtime_status?.ready }">
              {{ flowForm.tavily_runtime_status?.ready ? 'Tavily 运行时已就绪' : 'Tavily 未就绪：缺少 API Key' }}
            </p>
          </div>
          <div class="settings-form-title">Deep Research</div>
          <div class="form-check-group">
            <label class="form-check"><input v-model="flowForm.deep_research_enabled" type="checkbox" /><span>启用多轮深度研究</span></label>
          </div>
          <div class="form-row">
            <div class="form-group"><label for="flow-dr-search">最大搜索次数</label><input id="flow-dr-search" v-model.number="flowForm.deep_research_max_search_calls" type="number" min="1" max="20" /></div>
            <div class="form-group"><label for="flow-dr-iterations">最大研究轮次</label><input id="flow-dr-iterations" v-model.number="flowForm.deep_research_max_supervisor_iterations" type="number" min="1" max="10" /></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label for="flow-dr-extract-round">每轮提取 URL</label><input id="flow-dr-extract-round" v-model.number="flowForm.deep_research_max_extract_urls_per_round" type="number" min="1" max="10" /></div>
            <div class="form-group"><label for="flow-dr-extract-total">总提取 URL</label><input id="flow-dr-extract-total" v-model.number="flowForm.deep_research_max_total_extract_urls" type="number" min="1" max="30" /></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label for="flow-workers">章节并发数</label><input id="flow-workers" v-model.number="flowForm.workers" type="number" min="1" max="10" /></div>
            <div class="form-group"><label for="flow-llm">模型并发数</label><input id="flow-llm" v-model.number="flowForm.llm_concurrency" type="number" min="1" max="32" /></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label for="flow-retries">写作批次重试</label><input id="flow-retries" v-model.number="flowForm.write_batch_retries" type="number" min="0" max="20" /></div>
            <div class="form-group"><label for="flow-repair">最大修复轮次</label><input id="flow-repair" v-model.number="flowForm.max_repair_rounds" type="number" min="0" max="10" /></div>
          </div>
          <div class="settings-form-title">审核</div>
          <div class="form-check-group">
            <label class="form-check"><input v-model="flowForm.chapter_review_enabled" type="checkbox" /><span>启用审核</span></label>
          </div>
          <p class="settings-hint">开启时，每章生成后在“生成章节”内部完成自审和按需改稿，并执行全文审核；关闭时直接生成第一版，不执行审核相关阶段。</p>
          <div class="settings-form-title">失败处理</div>
          <p class="settings-hint">
            任一必需校验失败都会停止当前任务并显示具体错误；不会使用旧正文或旧目录继续交付。
          </p>
          <p v-if="flowError" class="form-error" role="alert">{{ flowError }}</p>
          <p v-if="flowSuccess" class="form-success">{{ flowSuccess }}</p>
          <div class="settings-form-footer"><button type="button" class="btn" @click="$emit('close')">关闭</button><button type="submit" class="btn btn-primary" :disabled="flowSaving">{{ flowSaving ? '保存中...' : '保存流程设置' }}</button></div>
        </form>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { ref, reactive, watch, computed } from 'vue'
import { fetchLlmSettings, fetchFlowSettings, saveFlowSettings,
  saveLlmModel,
  activateLlmModel,
  deleteLlmModel, testLlmModel } from '../api'
import {
  llmModelFormFromApi,
  llmModelKeyIsReady,
  llmModelPayload,
  researchSettingsFormFromApi,
  researchSettingsPayload,
} from '../api/settingsContracts.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'saved'])

const models = ref([])
const activeId = ref('')
const editingId = ref('')
const isNew = ref(false)
const showApiKey = ref(false)
const submitting = ref(false)
const testing = ref(false)
const form = reactive(llmModelFormFromApi())

const providerHint = computed(() => {
  if (form.provider === 'anthropic') {
    return 'Anthropic：Base URL 例 https://api.anthropic.com ；模型例 claude-3-5-sonnet-latest；Key 一般为 sk-ant-…'
  }
  return 'OpenAI 兼容：Base URL 例 https://api.openai.com/v1 或任意 /v1 网关；走 /chat/completions'
})
function onProviderChange() {
  if (form.provider === 'anthropic') {
    if (!form.base_url || form.base_url.includes('openai.com') || form.base_url.includes('/v1/chat')) {
      form.base_url = 'https://api.anthropic.com'
    }
  } else if (form.provider === 'openai') {
    if (!form.base_url || form.base_url.includes('anthropic.com')) {
      form.base_url = 'https://api.openai.com/v1'
    }
  }
}

const testResult = ref('')
const testOk = ref(null)
const error = ref('')
const success = ref('')
const activeTab = ref('model')
const flowSaving = ref(false)
const flowError = ref('')
const flowSuccess = ref('')
const flowForm = reactive({
  workers: 4,
  llm_concurrency: 8,
  write_batch_retries: 5,
  max_repair_rounds: 2,
  research_provider: 'tavily',
  tavily_api_key: '',
  has_tavily_api_key: false,
  tavily_runtime_status: { ready: false, reason: 'TAVILY_API_KEY_MISSING' },
  deep_research_enabled: true,
  deep_research_max_search_calls: 4,
  deep_research_max_supervisor_iterations: 4,
  deep_research_max_extract_urls_per_round: 4,
  deep_research_max_total_extract_urls: 12,
  chapter_review_enabled: true,
  chapter_review_gate: true,
  global_review_gate: true,
  anti_fabrication_gate: true,
  allow_accept_risk: false,
  validation_failure_blocks_pipeline: true,
})

async function loadFlow() {
  try {
    const { data } = await fetchFlowSettings()
    if (data?.ok && data.settings) Object.assign(flowForm, researchSettingsFormFromApi(data.settings))
  } catch (e) { flowError.value = '加载流程设置失败，请检查后端服务' }
}

async function saveFlow() {
  flowSaving.value = true; flowError.value = ''; flowSuccess.value = ''
  try {
    const { data } = await saveFlowSettings(researchSettingsPayload(flowForm))
    if (!data?.ok) throw new Error(data?.message || '保存失败')
    Object.assign(flowForm, researchSettingsFormFromApi(data.settings || {}))
    flowSuccess.value = '流程设置已保存，后续启动的阶段会使用新参数。'
  } catch (e) { flowError.value = e.response?.data?.message || e.message || '保存失败' } finally { flowSaving.value = false }
}


function emptyForm() {
  Object.assign(form, llmModelFormFromApi())
  showApiKey.value = false
}

function fillForm(m) {
  Object.assign(form, llmModelFormFromApi(m))
  showApiKey.value = false
}

async function loadModels(keepEditing = false) {
  error.value = ''
  try {
    const { data } = await fetchLlmSettings()
    if (data.ok) {
      models.value = data.models || []
      activeId.value = data.active_id || ''
      if (!keepEditing) {
        if (models.value.length) {
          const target = models.value.find((m) => m.id === activeId.value) || models.value[0]
          selectModel(target.id)
        } else {
          startNewModel()
        }
      }
    }
  } catch (e) {
    error.value = '加载配置失败，请检查后端服务'
  }
}

function selectModel(id) {
  const m = models.value.find((item) => item.id === id)
  if (!m) return
  editingId.value = id
  isNew.value = false
  fillForm(m)
  error.value = ''
  success.value = ''
}

function startNewModel() {
  editingId.value = ''
  isNew.value = true
  emptyForm()
  error.value = ''
  success.value = ''
}

async function handleSave(forceActive = false) {
  error.value = ''
  success.value = ''
  if (!form.name.trim()) {
    error.value = '请填写模型别名'
    return
  }
  if (
    !form.base_url.trim()
    || !form.model.trim()
    || !llmModelKeyIsReady(form, { isNew: isNew.value })
  ) {
    error.value = 'Base URL、API Key、模型 ID 均为必填项'
    return
  }
  submitting.value = true
  try {
    const payload = llmModelPayload(form, { isNew: isNew.value })
    const isEditingActive = !isNew.value && form.id && form.id === activeId.value
    const setActivate = forceActive === true || (isNew.value && models.value.length === 0) || isEditingActive
    const { data } = await saveLlmModel(payload, setActivate)
    if (data.ok) {
      models.value = data.models || []
      activeId.value = data.active_id || ''
      const savedId = data.saved_id || (isNew.value ? '' : form.id)
      if (savedId) {
        editingId.value = savedId
        isNew.value = false
        const saved = models.value.find((m) => m.id === savedId)
        if (saved) fillForm(saved)
      }
      success.value = data.applied_live
        ? (setActivate ? '已添加并设为使用中，所有工作空间实时生效' : '保存成功，所有工作空间后续请求实时生效')
        : '保存成功；该配置尚未设为使用中'
      emit('saved', { models: models.value, activeId: activeId.value })
    } else {
      error.value = data.message || '保存失败'
    }
  } catch (e) {
    error.value = e.response?.data?.message || '保存失败，请检查后端服务'
  } finally {
    submitting.value = false
  }
}


async function handleTest() {
  error.value = ''
  success.value = ''
  testResult.value = ''
  testOk.value = null
  if (
    !form.base_url.trim()
    || !form.model.trim()
    || !llmModelKeyIsReady(form, { isNew: isNew.value })
  ) {
    error.value = '请先填写 Base URL、API Key 和模型 ID 再测试'
    return
  }
  testing.value = true
  try {
    const payload = llmModelPayload(form, { isNew: isNew.value })
    payload.name ||= 'test'
    payload.timeout ||= 60
    payload.max_retries = 1
    const { data } = await testLlmModel(payload, { useActive: false })
    if (data && data.ok) {
      testOk.value = true
      testResult.value = `连接成功（${data.elapsed_ms || '?'} ms）
模型: ${data.model || payload.model}
回复: ${data.reply || ''}`
      success.value = '大模型连接测试通过'
    } else {
      testOk.value = false
      testResult.value = (data && data.message) || '连接失败'
      error.value = (data && data.message) || '连接失败'
    }
  } catch (e) {
    testOk.value = false
    const status = e?.response?.status
    const data = e?.response?.data
    const serverMsg = data?.message || data?.error?.message || data?.detail
    let msg = serverMsg || e.message || '测试请求失败'
    if (!serverMsg && status) {
      msg = `测试请求失败（HTTP ${status}）。请确认后端已重启并查看服务日志。`
    }
    // Avoid showing bare axios strings like "Request failed with status code 500".
    if (/^Request failed with status code \d+$/i.test(String(msg))) {
      msg = `测试请求失败（HTTP ${status || '?'}）。后端未返回可读错误，请重启后端后再试。`
    }
    testResult.value = msg
    error.value = msg
  } finally {
    testing.value = false
  }
}

async function handleActivate() {
  error.value = ''
  success.value = ''
  if (!form.id) {
    error.value = '请先保存模型'
    return
  }
  try {
    const { data } = await activateLlmModel(form.id)
    if (data.ok) {
      models.value = data.models || []
      activeId.value = data.active_id || ''
      success.value = '已设为使用中，所有工作空间后续请求实时生效'
      emit('saved', { models: models.value, activeId: activeId.value })
    } else {
      error.value = data.message || '设置失败'
    }
  } catch (e) {
    error.value = e.response?.data?.message || '设置失败，请检查后端服务'
  }
}

async function handleDelete(id) {
  error.value = ''
  success.value = ''
  const target = models.value.find((m) => m.id === id)
  if (!target) return
  if (!confirm(`确定要删除「${target.name || '该模型'}」吗？`)) return
  try {
    const { data } = await deleteLlmModel(id)
    if (data.ok) {
      models.value = data.models || []
      activeId.value = data.active_id || ''
      if (editingId.value === id) {
        if (models.value.length) {
          selectModel(models.value[0].id)
        } else {
          startNewModel()
        }
      }
      success.value = '已删除'
      emit('saved', { models: models.value, activeId: activeId.value })
    } else {
      error.value = data.message || '删除失败'
    }
  } catch (e) {
    error.value = e.response?.data?.message || '删除失败，请检查后端服务'
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      loadModels()
      loadFlow()
    } else {
      error.value = ''
      success.value = ''
    }
  },
  { immediate: true }
)
</script>
