import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { installCsrfFetch } from './csrf'
import './assets/styles/main.css'

installCsrfFetch()

const app = createApp(App)
app.use(router)
app.mount('#app')
