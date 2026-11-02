import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Login from './views/Login.vue'
import Orders from './views/Orders.vue'
import Evaluations from './views/Evaluations.vue'
import Messages from './views/Messages.vue'
import ApplicationDetail from './views/ApplicationDetail.vue'
import WithdrawalAccounts from './views/WithdrawalAccounts.vue'
import Wallet from './views/Wallet.vue'
import Reports from './views/Reports.vue'
import Reservations from './views/Reservations.vue'
import SupportChat from './views/SupportChat.vue'
import Mine from './views/Mine.vue'
import Profile from './views/Profile.vue'
import Skills from './views/Skills.vue'
import Points from './views/Points.vue'
import Register from './views/Register.vue'
import Audition from './views/Audition.vue'
import Support from './views/Support.vue'
import Pass from './views/Pass.vue'
import { currentUser } from './lib/session'
import './style.css'

const router = createRouter({ history: createWebHashHistory(), routes: [
  { path: '/', redirect: '/orders' },
  { path: '/login', component: Login },
  { path: '/register', component: Register },
  { path: '/audition', alias: '/pages/audition/index', component: Audition },
  { path: '/support', component: Support, meta: { auth: true } },
  { path: '/pass', component: Pass, meta: { auth: true } },
  { path: '/orders', component: Orders, meta: { auth: true } },
  { path: '/evaluations', component: Evaluations, meta: { auth: true } },
  { path: '/messages', component: Messages, meta: { auth: true } },
  { path: '/applications/:orderId', component: ApplicationDetail, meta: { auth: true } },
  { path: '/withdraw-accounts', component: WithdrawalAccounts, meta: { auth: true } },
  { path: '/wallet', component: Wallet, meta: { auth: true } },
  { path: '/reports', component: Reports, meta: { auth: true } },
  { path: '/reservations', component: Reservations, meta: { auth: true } },
  { path: '/support-chat', component: SupportChat, meta: { auth: true } },
  { path: '/mine', component: Mine, meta: { auth: true } },
  { path: '/profile', component: Profile, meta: { auth: true } },
  { path: '/skills', component: Skills, meta: { auth: true } },
  { path: '/points', component: Points, meta: { auth: true } },
] })
router.beforeEach(to => to.meta.auth && !currentUser.value ? '/login' : true)
createApp(App).use(createPinia()).use(router).mount('#app')
