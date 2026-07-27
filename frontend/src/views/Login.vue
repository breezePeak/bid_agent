<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <h1>标书 Agent</h1>
        <p>智能投标文件生成系统</p>
      </div>
      <form class="login-form" @submit.prevent="handleLogin">
        <div class="form-group">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            type="text"
            placeholder="请输入用户名"
            autocomplete="username"
          />
        </div>
        <div class="form-group">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            placeholder="本机可留空后直接登录"
            autocomplete="current-password"
          />
        </div>
        <button type="submit" class="btn btn-primary btn-block" :disabled="loading">
          {{ loading ? '登录中...' : '登 录' }}
        </button>
        <p v-if="error" class="login-error">{{ error }}</p>
        <p class="login-hint">账号由服务端环境变量 BID_AGENT_AUTH_USER / BID_AGENT_AUTH_PASSWORD 配置。</p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'

const router = useRouter()

const username = ref('admin')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  error.value = ''
  if (!username.value.trim() || !password.value.trim()) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    await login(username.value.trim(), password.value)
    await router.push('/business')
  } catch (requestError) {
    error.value = requestError?.response?.data?.message || requestError?.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>
