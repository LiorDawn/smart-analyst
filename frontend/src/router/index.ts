/**
 * 路由（对齐智汇购 router/index.ts：hash 历史 + 懒加载 + 嵌套布局）
 * 单页应用：/ → MainLayout 外壳 → ChatView 对话页
 */
import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: () => import('../layouts/MainLayout.vue'),
      children: [
        {
          path: '',
          name: 'Chat',
          component: () => import('../views/chat/ChatView.vue'),
        },
      ],
    },
  ],
})

export default router
