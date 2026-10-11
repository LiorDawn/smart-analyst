<script setup lang="ts">
/** 主布局：顶部品牌栏 + 内容区（对齐智汇购 layouts/AdminLayout.vue 的外壳职责） */
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="header-left">
        <div class="logo">SA</div>
        <div class="title-group">
          <h1>SmartAnalyst</h1>
          <div class="sub">经营分析多智能体 · 自然语言问数 → 自动报告</div>
        </div>
      </div>
      <div class="status-dot" title="服务连接状态"></div>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell { height: 100%; display: flex; flex-direction: column; }
.app-header { display: flex; align-items: center; padding: 12px 20px; border-bottom: 1px solid #26303d; background: #161c24; }
.header-left { display: flex; align-items: center; gap: 12px; }
.logo { width: 30px; height: 30px; border-radius: 8px; background: linear-gradient(135deg, #3dd6c8, #58a6ff); display: flex; align-items: center; justify-content: center; font-weight: 700; color: #0b1218; }
.title-group h1 { font-size: 16px; font-weight: 600; }
.title-group .sub { font-size: 12px; color: #8b98a5; }
.status-dot { margin-left: auto; width: 8px; height: 8px; border-radius: 50%; background: #3fb950; box-shadow: 0 0 6px #3fb950; }
.app-main { flex: 1; display: flex; min-height: 0; }
</style>
