import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Business from '../views/Business.vue'
import { fetchCurrentUser } from '../api'

const routes = [
  { path: '/', redirect: '/login' },
  { path: '/login', name: 'Login', component: Login },
  { path: '/business', name: 'Business', component: Business },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.name === 'Login') return true
  try {
    await fetchCurrentUser()
    return true
  } catch (_) {
    return { name: 'Login' }
  }
})

export default router
