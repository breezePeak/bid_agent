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
            placeholder="请输入密码"
            autocomplete="current-password"
          />
        </div>
        <button type="submit" class="btn btn-primary btn-block" :disabled="loading">
          {{ loading ? '登录中...' : '登 录' }}
        </button>
        <p v-if="error" class="login-error">{{ error }}</p>
        <p class="login-hint">默认账号：admin / admin123</p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const DEFAULT_USER = 'admin'
const DEFAULT_PASS = 'admin123'

const username = ref(DEFAULT_USER)
const password = ref(DEFAULT_PASS)
const loading = ref(false)
const error = ref('')

function handleLogin() {
  error.value = ''
  if (!username.value.trim() || !password.value.trim()) {
    error.value = '请输入用户名和密码'
    return
  }
  if (username.value.trim() !== DEFAULT_USER || password.value !== DEFAULT_PASS) {
    error.value = '用户名或密码错误'
    return
  }
  loading.value = true
  setTimeout(() => {
    loading.value = false
    router.push('/business')
  }, 600)
}
</script>
