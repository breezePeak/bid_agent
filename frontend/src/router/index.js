import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Business from '../views/Business.vue'

const routes = [
  { path: '/', redirect: '/login' },
  { path: '/login', name: 'Login', component: Login },
  { path: '/business', name: 'Business', component: Business },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
