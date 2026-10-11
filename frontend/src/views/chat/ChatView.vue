<template>
  <!-- 左右分栏页面：右侧对话区 + 左侧监控栏（对齐智汇购 AIChat 主体布局） -->
  <div class="chat-page">
    <div class="chat-area">
      <div class="float-body" id="chat-body">
        <!-- 空状态 + 快捷问题 -->
        <div v-if="messages.length === 0" class="float-empty">
          <div class="empty-icon">🤖</div>
          <p>你好！我是 SmartAnalyst 经营分析助手，可以用自然语言查数并自动生成报告。</p>
          <div class="quick-questions">
            <span
              v-for="q in QUICK_QUESTIONS"
              :key="q"
              class="quick-tag"
              @click="input = q"
            >{{ q }}</span>
          </div>
        </div>

        <!-- 消息列表 -->
        <div v-for="(m, i) in messages" :key="i" :class="['float-msg', m.role]">
          <div class="msg-avatar">{{ m.role === 'user' ? '👤' : '🤖' }}</div>
          <div class="msg-content">
            <div class="msg-header">
              <span class="msg-role-name">{{ m.role === 'user' ? '我' : 'AI 助手' }}</span>
            </div>
            <div class="msg-bubble" v-html="m.html"></div>
          </div>
        </div>

        <!-- 澄清卡：clarify（信息补全输入） / HITL（工具执行确认：批准/拒绝） -->
        <div class="clarify" v-if="clarifying">
          <div class="q">🔍 {{ clarifying.prompt }}</div>
          <template v-if="clarifying.type === 'hitl'">
            <div class="hitl-actions">
              <button class="hitl-btn approve" :disabled="clarifying.done" @click="submitHITL(true)">✅ 批准执行</button>
              <button class="hitl-btn reject" :disabled="clarifying.done" @click="submitHITL(false)">✕ 拒绝</button>
            </div>
          </template>
          <template v-else>
            <input
              v-model="clarifyValue"
              :disabled="clarifying.done"
              placeholder="输入回答后回车…"
              @keydown.enter="submitClarify"
            >
          </template>
        </div>

        <div v-if="typing" class="typing">分析中，正在走节点链路…</div>
      </div>

      <div class="composer">
        <input
          v-model="input"
          placeholder="输入问题，回车发送…"
          autocomplete="off"
          :disabled="!!clarifying"
          @keydown.enter="send"
        >
        <button :disabled="busy || !!clarifying" @click="send">发送</button>
      </div>
    </div>

    <!-- 左侧监控栏：节点链路 + 事件日志 -->
    <div class="monitor-panel monitor-col">
      <div class="panel-tabs">
        <div class="tab" :class="{ active: tab === 'chain' }" @click="tab = 'chain'">节点链路</div>
        <div class="tab" :class="{ active: tab === 'logs' }" @click="tab = 'logs'">事件日志</div>
      </div>

      <div class="panel-body" v-show="tab === 'chain'">
        <div class="chain">
          <template v-for="(n, i) in NODE_LIST" :key="n">
            <div class="node" :class="nodes[n]">
              <div class="dot"></div>
              <div class="name">{{ n }}</div>
              <div class="tag">{{ NODE_TAGS[n] || '' }}</div>
            </div>
            <div v-if="i < NODE_LIST.length - 1" class="link"></div>
          </template>
        </div>
      </div>

      <div class="panel-body" v-show="tab === 'logs'">
        <div class="logs">
          <div v-for="(l, i) in logs" :key="i" class="l" :class="l.cls">
            <time>{{ l.time }}</time>{{ l.msg }}
          </div>
          <div v-if="logs.length === 0" class="empty">暂无事件，发一个问题试试</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'

/* ── 请求基座 ── */
interface ApiError {
  node: string
  msg: string
  step?: string
}

async function post<T>(url: string, body: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (e) {
    throw new Error(`网络错误：无法连接后端（${url}）`)
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

/* ── 契约类型与接口 ── */
interface HitlRequest {
  name: string
  args: Record<string, unknown>
  description?: string
}

interface ChatResponse {
  thread_id: string
  need_clarify: boolean
  clarify_type: 'clarify' | 'hitl' | null
  prompt: string | null
  missing: string[]
  hitl_requests: HitlRequest[]
  report: string | null
  errors: ApiError[]
  status: string
}

/** 首次提问（或续聊）：跑完整图；遇 clarify/HITL 中断返回 need_clarify=true + prompt */
async function chat(question: string, threadId?: string): Promise<ChatResponse> {
  return post<ChatResponse>('/api/chat', { question, thread_id: threadId })
}

/** 澄清回答 / HITL 决策：Command(resume) 从断点继续跑到 END */
async function resume(
  threadId: string,
  answer: string,
  decisions?: { type: string; message?: string }[],
): Promise<ChatResponse> {
  return post<ChatResponse>(
    '/api/chat/resume',
    decisions ? { thread_id: threadId, decisions } : { thread_id: threadId, answer },
  )
}

/**
 * SSE 节点监控
 * 事件行格式：`data: <message>`，结束标记 `data: [流结束]`
 */
function openEvents(
  threadId: string,
  onMessage: (msg: string) => void,
  onDone: () => void,
): () => void {
  const es = new EventSource(`/api/events?thread_id=${threadId}&after=0`)
  es.onmessage = (e) => {
    if (e.data === '[流结束]') {
      es.close()
      onDone()
      return
    }
    onMessage(e.data)
  }
  es.onerror = () => {
    es.close()
    onDone()
  }
  return () => es.close()
}

/* ── Markdown 渲染 ── */
function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderMd(md: string): string {
  const lines = escapeHtml(md).split('\n')
  let html = ''
  let list: 'ul' | 'ol' | null = null
  let inCode = false
  let tbl: string[][] = []

  const closeList = () => {
    if (list) { html += `</${list}>`; list = null }
  }
  const flushTbl = () => {
    if (!tbl.length) return
    html += '<table><tr>' + tbl[0].map((c) => `<th>${c}</th>`).join('') + '</tr>'
    html += tbl.slice(1).map((r) => '<tr>' + r.map((c) => `<td>${c}</td>`).join('') + '</tr>').join('')
    html += '</table>'
    tbl = []
  }

  for (const line of lines) {
    if (line.startsWith('```')) {
      closeList(); flushTbl(); inCode = !inCode
      html += inCode ? '<code>' : '</code>'
      continue
    }
    if (inCode) { html += escapeHtml(line) + '<br>'; continue }
    if (/^\|/.test(line)) { tbl.push(line.split('|').slice(1, -1).map((c) => c.trim())); continue }
    flushTbl()

    if (/^#{1,3} /.test(line)) {
      closeList()
      const level = line.match(/^#+/)?.[0].length ?? 1
      html += `<h${level}>${line.replace(/^#+\s*/, '')}</h${level}>`
    } else if (line.match(/^[-*] /)) {
      if (list !== 'ul') { closeList(); list = 'ul'; html += '<ul>' }
      html += `<li>${line.slice(2)}</li>`
    } else if (/^\d+\. /.test(line)) {
      if (list !== 'ol') { closeList(); list = 'ol'; html += '<ol>' }
      html += `<li>${line.replace(/^\d+\.\s*/, '')}</li>`
    } else if (/^&gt; /.test(line)) {
      closeList(); html += `<blockquote>${line.slice(5)}</blockquote>`
    } else if (line.trim() === '') {
      closeList()
    } else {
      closeList(); html += `<p>${line}</p>`
    }
  }
  closeList(); flushTbl()
  return html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

/* ── 对话状态 ── */
interface MessageItem {
  role: 'user' | 'assistant'
  html: string
}

const threadId = ref('')
const busy = ref(false)
const typing = ref(false)
const messages = ref<MessageItem[]>([])
interface ClarifyState {
  type: 'clarify' | 'hitl'
  prompt: string
  value: string
  done: boolean
  hitlRequests: HitlRequest[]
}
const clarifying = ref<ClarifyState | null>(null)

/* ── 消息操作 ── */
function pushMessage(role: MessageItem['role'], html: string) {
  messages.value.push({ role, html })
}

function showTyping() { typing.value = true }
function removeTyping() { typing.value = false }

function showClarify(prompt: string, type: 'clarify' | 'hitl' = 'clarify', hitlRequests: HitlRequest[] = []) {
  clarifying.value = { type, prompt, value: '', done: false, hitlRequests }
}

function clearClarify() { clarifying.value = null }

function renderReport(report: string | null) {
  if (!report) {
    pushMessage('assistant', '<span class="hint">本次未产出报告（可能指标缺失或全部降级，见左侧日志）。</span>')
    return
  }
  pushMessage('assistant', `<div class="md">${renderMd(report)}</div>`)
}

function renderErrors(errors: { node: string; msg: string }[]) {
  if (errors.length) {
    pushMessage('assistant', `<span class="hint">降级提示：${escapeHtml(errors.map((e) => e.msg).join('；'))}</span>`)
  }
}

/** 统一处理 /api/chat 与 /api/chat/resume 响应 */
function handleResponse(res: ChatResponse) {
  if (res.need_clarify) {
    removeTyping()
    showClarify(
      res.prompt ?? '请补充信息',
      res.clarify_type === 'hitl' ? 'hitl' : 'clarify',
      res.hitl_requests ?? [],
    )
  } else {
    removeTyping()
    renderReport(res.report)
    renderErrors(res.errors ?? [])
  }
}

async function sendQuestion(question: string) {
  if (busy.value) return
  busy.value = true
  pushMessage('user', escapeHtml(question))
  showTyping()
  try {
    const res = await chat(question, threadId.value || undefined)
    threadId.value = res.thread_id
    handleResponse(res)
  } catch (e) {
    removeTyping()
    pushMessage('assistant', `<span class="hint">请求失败：${escapeHtml(String(e))}</span>`)
  } finally {
    busy.value = false
  }
}

async function resumeChat(answer: string, decisions?: { type: string; message?: string }[]) {
  busy.value = true
  showTyping()
  try {
    handleResponse(await resume(threadId.value, answer, decisions))
  } catch (e) {
    removeTyping()
    pushMessage('assistant', `<span class="hint">恢复失败：${escapeHtml(String(e))}</span>`)
  } finally {
    clearClarify()
    busy.value = false
  }
}

/* ── SSE 监控 ── */
type NodeState = 'wait' | 'run' | 'done' | 'err'

const NODE_LIST = [
  'task_parser', 'clarify_interrupt', 'quick_answer', 'planner', 'query_executor',
  'join', 'compute', 'supervisor', 'writer', 'reviewer', 'fixer', 'deliver',
] as const

const NODE_TAGS: Record<string, string> = {
  task_parser: '任务解析', clarify_interrupt: '澄清中断', quick_answer: '快捷问答',
  planner: '查询规划', query_executor: 'SQL执行', join: '结果汇聚', compute: '指标计算',
  supervisor: '分析编排', writer: '报告撰写', reviewer: '质量校验', fixer: '修正标注', deliver: '交付落盘',
}

interface LogEntry {
  time: string
  msg: string
  cls: '' | 'tool' | 'agent' | 'node' | 'err'
}

const nodes = ref<Record<string, NodeState>>(
  Object.fromEntries(NODE_LIST.map((n) => [n, 'wait'])) as Record<string, NodeState>,
)
const logs = ref<LogEntry[]>([])
let close: (() => void) | null = null

function setNode(node: string, state: NodeState) {
  if (node in nodes.value) nodes.value[node] = state
}

function addLog(msg: string, cls: LogEntry['cls'] = '') {
  logs.value.push({
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    msg,
    cls,
  })
}

/** 解析服务端事件行 → 更新节点状态 / 日志 */
function handleEvent(msg: string) {
  addLog(msg)
  const m = msg.match(/节点执行:\s*(\w+)/)
  if (m) setNode(m[1], 'done')
  const mm = msg.match(/正在调用子 agent:\s*(\w+)/)
  if (mm) {
    setNode('supervisor', 'run')
    addLog(`→ ${mm[1]} 已唤起`, 'agent')
  }
}

function start(threadId: string) {
  stop()
  close = openEvents(threadId, handleEvent, () => { close = null })
}

function stop() {
  if (close) { close(); close = null }
}

onUnmounted(stop)

/* ── 页面交互 ── */
const input = ref('')
const clarifyValue = ref('')
const tab = ref<'chain' | 'logs'>('chain')

const QUICK_QUESTIONS = [
  '查一下本周的GMV和订单量，按品类看',
  '上周销售额环比上周怎么样',
  '访客数和转化率近7天如何',
]

function send() {
  const q = input.value.trim()
  if (!q || busy.value || clarifying.value) return
  input.value = ''
  sendQuestion(q)
}

function submitClarify() {
  if (clarifying.value?.done) return
  const answer = clarifyValue.value.trim()
  if (!answer) return
  clarifying.value!.done = true
  resumeChat(answer)
}

/** HITL 确认：批准 → approve 决策；拒绝 → reject 决策（按序对应后端 action_requests） */
function submitHITL(approve: boolean) {
  if (clarifying.value?.done) return
  clarifying.value!.done = true
  resumeChat('', approve ? [{ type: 'approve' }] : [{ type: 'reject', message: '用户拒绝执行该操作' }])
}

// 会话建立后启动 SSE 节点监控
watch(
  () => threadId.value,
  (id) => { if (id) start(id) },
)
</script>

<style scoped>
/* ── 页面分栏布局 ── */
.chat-page { flex: 1; display: flex; min-height: 0; }
.chat-area { flex: 1.4; display: flex; flex-direction: column; min-width: 0; border-right: 1px solid #26303d; }
.monitor-col { flex: 1; min-width: 0; }
.float-body { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.float-empty { text-align: center; margin: 60px 20px; color: #8b98a5; }
.float-empty .empty-icon { font-size: 40px; margin-bottom: 12px; }
.float-empty p { font-size: 13px; line-height: 1.8; }
.quick-questions { margin-top: 18px; display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; }
.quick-tag { padding: 6px 14px; border-radius: 16px; background: #161c24; border: 1px solid #26303d; font-size: 12.5px; color: #3dd6c8; cursor: pointer; }
.quick-tag:hover { border-color: #3dd6c8; }
.typing { color: #8b98a5; font-size: 13px; padding: 4px 8px; align-self: flex-start; }
.composer { border-top: 1px solid #26303d; padding: 12px 20px; display: flex; gap: 10px; background: #161c24; }
.composer input { flex: 1; background: #1b232e; border: 1px solid #26303d; color: #e6edf3; border-radius: 10px; padding: 10px 14px; font-size: 14px; }
.composer input:focus { outline: 1px solid #3dd6c8; }
.composer button { background: #3dd6c8; color: #0b1218; border: none; border-radius: 10px; padding: 0 18px; font-weight: 600; cursor: pointer; }
.composer button:disabled { opacity: .45; cursor: not-allowed; }

/* ── 消息 ── */
.float-msg { display: flex; gap: 10px; max-width: 88%; }
.float-msg.user { flex-direction: row-reverse; align-self: flex-end; }
.msg-avatar { width: 34px; height: 34px; border-radius: 50%; background: #1b232e; border: 1px solid #26303d; display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.msg-content { min-width: 0; }
.msg-header { display: flex; gap: 8px; align-items: baseline; margin-bottom: 4px; }
.msg-role-name { font-size: 12px; color: #8b98a5; }
.msg-bubble { padding: 10px 14px; border-radius: 12px; background: #1b232e; border: 1px solid #26303d; font-size: 14px; line-height: 1.65; word-break: break-word; }
.float-msg.user .msg-bubble { background: #1d3a4a; border-color: #26506b; }

/* Markdown 报告样式 */
.msg-bubble :deep(.md h1) { font-size: 18px; margin: 10px 0 6px; color: #58a6ff; }
.msg-bubble :deep(.md h2) { font-size: 15px; margin: 12px 0 6px; color: #3dd6c8; }
.msg-bubble :deep(.md h3) { font-size: 13.5px; margin: 8px 0 4px; }
.msg-bubble :deep(.md p) { margin: 6px 0; }
.msg-bubble :deep(.md ul), .msg-bubble :deep(.md ol) { margin: 6px 0 6px 20px; }
.msg-bubble :deep(.md strong) { color: #fff; }
.msg-bubble :deep(.md table) { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
.msg-bubble :deep(.md th), .msg-bubble :deep(.md td) { border: 1px solid #26303d; padding: 5px 10px; text-align: left; }
.msg-bubble :deep(.md th) { background: #161c24; color: #3dd6c8; }
.msg-bubble :deep(.md blockquote) { border-left: 3px solid #26303d; margin: 8px 0; padding: 4px 12px; color: #8b98a5; }
.msg-bubble :deep(.md code) { background: #0f1419; padding: 1px 6px; border-radius: 4px; font-size: 12.5px; color: #58a6ff; }
.msg-bubble :deep(.hint) { color: #8b98a5; font-size: 12px; }

/* ── 澄清卡 ── */
.clarify { align-self: flex-start; max-width: 88%; background: #2a2412; border: 1px solid #5c4a15; border-radius: 12px; padding: 12px 16px; }
.clarify .q { font-size: 14px; margin-bottom: 8px; }
.clarify input { width: 100%; background: #161c24; border: 1px solid #26303d; color: #e6edf3; border-radius: 8px; padding: 8px 12px; font-size: 13.5px; }
.clarify input:focus { outline: 1px solid #3dd6c8; }
.clarify input:disabled { opacity: .5; }
.clarify .hitl-actions { display: flex; gap: 10px; margin-top: 6px; }
.hitl-btn { flex: 1; border-radius: 8px; padding: 9px 0; font-size: 13.5px; font-weight: 600; cursor: pointer; border: 1px solid transparent; }
.hitl-btn:disabled { opacity: .5; cursor: not-allowed; }
.hitl-btn.approve { background: #1d3a2a; color: #3fb950; border-color: #2d5a3e; }
.hitl-btn.approve:hover:not(:disabled) { background: #24513a; }
.hitl-btn.reject { background: #3a1d1d; color: #f85149; border-color: #5a2d2d; }
.hitl-btn.reject:hover:not(:disabled) { background: #512424; }

/* ── 监控面板 ── */
.monitor-panel { display: flex; flex-direction: column; height: 100%; }
.panel-tabs { display: flex; padding: 10px 14px 0; gap: 6px; }
.panel-tabs .tab { padding: 6px 12px; border-radius: 8px 8px 0 0; font-size: 13px; color: #8b98a5; cursor: pointer; border: 1px solid transparent; border-bottom: none; }
.panel-tabs .tab.active { background: #1b232e; color: #e6edf3; border-color: #26303d; }
.panel-body { flex: 1; overflow-y: auto; padding: 12px 14px; background: #1b232e; }
.chain { display: flex; flex-direction: column; }
.node { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: 8px; font-size: 13px; }
.node .dot { width: 9px; height: 9px; border-radius: 50%; background: #26303d; flex-shrink: 0; }
.node.wait .dot { background: #26303d; }
.node.run .dot { background: #d29922; box-shadow: 0 0 6px #d29922; }
.node.done .dot { background: #3fb950; }
.node.err .dot { background: #f85149; }
.node .name { font-weight: 600; }
.node .tag { margin-left: auto; font-size: 11px; color: #8b98a5; }
.node .link { width: 1px; height: 12px; background: #26303d; margin-left: 14px; }
.logs { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; font-family: Consolas, monospace; }
.logs .l { padding: 4px 8px; border-left: 2px solid #26303d; color: #8b98a5; word-break: break-all; }
.logs .l.tool { border-color: #58a6ff; }
.logs .l.agent { border-color: #3dd6c8; }
.logs .l.node { border-color: #3fb950; }
.logs .l.err { border-color: #f85149; color: #f85149; }
.logs .l time { color: #4a5563; margin-right: 8px; }
.logs .empty { color: #4a5563; font-size: 12.5px; text-align: center; padding: 30px 0; }
</style>