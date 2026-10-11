"""Agent 执行过程监控模块（照 deepsearch 写法）。

统一包装工具调用、子 agent 调用、节点流转、任务结果等事件：
  {type: "monitor_event", event, message, data, timestamp}
通道：WebSocket 定向推送（按 thread_id，可选挂载）+ 控制台保底输出。
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from collections import deque
from typing import Any, Optional

from app.api.context import get_thread_context

logger = logging.getLogger(__name__)


class ToolMonitor:
    """工具 / 子 agent / 节点 / 结果的统一监控入口（单例）。

    业务代码只需导入全局 monitor 并调用 report_* 方法；
    具体是 WebSocket 推送还是控制台输出，由本类统一处理。

    缓冲治理（防 SSE 缓冲无限增长）：
      - 单 thread 事件队列有上限（deque maxlen），超出丢最旧；
      - thread 空闲超过 TTL 被惰性清理；thread 数超限清最久未访问的。
    """

    _instance = None

    _MAX_EVENTS_PER_THREAD = 500   # 单 thread 事件上限：超出丢最旧（防内存无限增长）
    _MAX_THREADS = 1000            # 同时缓存的最大 thread 数：超出清最久未访问的
    _EVENT_TTL_SECONDS = 3600      # thread 空闲保留期：超过后事件缓冲被惰性清理

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.websocket_manager = None
            cls._instance._buffer: dict[str, deque] = {}       # thread_id → 有界事件队列（SSE 轮询用）
            cls._instance._last_access: dict[str, float] = {}  # thread_id → 最近访问时间（TTL 清理用）
            cls._instance._seq: dict[str, int] = {}            # thread_id → 已发序号
        return cls._instance

    def set_websocket_manager(self, manager: "ConnectionManager") -> None:
        """绑定 WebSocket 连接管理器（未绑定则只走控制台）。"""
        self.websocket_manager = manager

    def _touch(self, tid: str) -> None:
        """刷新 thread 最近访问时间（供 TTL 清理判断空闲）。"""
        self._last_access[tid] = time.time()

    def _cleanup(self) -> None:
        """惰性清理：丢弃 TTL 过期 thread；thread 数超限时清最久未访问的。"""
        now = time.time()
        expired = [t for t, last in self._last_access.items() if now - last > self._EVENT_TTL_SECONDS]
        for t in expired:
            self._buffer.pop(t, None)
            self._last_access.pop(t, None)
            self._seq.pop(t, None)
        while len(self._buffer) > self._MAX_THREADS:
            oldest = min(self._last_access.items(), key=lambda kv: kv[1])[0]
            self._buffer.pop(oldest, None)
            self._last_access.pop(oldest, None)
            self._seq.pop(oldest, None)

    def _emit(self, event_type: str, message: str, data: Optional[dict] = None) -> None:
        """构造统一监控事件，推送当前 thread_id 对应前端 + 控制台保底。"""
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat(),
        }

        if self.websocket_manager:
            try:
                thread_id = get_thread_context()
                self.websocket_manager.send_to_thread(payload, thread_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Monitor] WebSocket send failed: %s", e)

        # SSE 缓冲：按 thread_id 追加（deque 有界，超出丢最旧；空闲 thread 惰性清理）
        try:
            tid = get_thread_context() or "default"
            self._cleanup()
            self._buffer.setdefault(tid, deque(maxlen=self._MAX_EVENTS_PER_THREAD)).append(payload)
            self._touch(tid)
        except Exception:  # noqa: BLE001
            self._cleanup()
            self._buffer.setdefault("default", deque(maxlen=self._MAX_EVENTS_PER_THREAD)).append(payload)
            self._touch("default")

        # 控制台保底输出（无前端场景观察执行过程）
        print(f"[Monitor:{event_type}] {message}")
        if data:
            # default=str：update 中可能含 Decimal/日期等非 JSON 类型，仅展示用
            print(f"    {json.dumps(data, ensure_ascii=False, default=str)[:300]}")

    # ── SSE 增量读取（前端 /api/events 轮询用） ──
    def get_events(self, thread_id: str, after_index: int = 0) -> list[dict]:
        """返回 thread_id 的 [after_index:] 事件，供前端轮询增量渲染节点流转。"""
        self._touch(thread_id)
        events = self._buffer.get(thread_id)
        if not events:
            return []
        return list(events)[after_index:]

    def next_index(self, thread_id: str) -> int:
        """当前已缓冲事件数（下次轮询的 after_index 起点）。"""
        return len(self._buffer.get(thread_id) or [])

    # ── 事件上报接口 ──
    def report_tool(self, tool_name: str, args: Optional[dict] = None) -> None:
        """报告开始执行某个工具。"""
        self._emit("tool_start", f"开始执行工具: {tool_name}",
                   {"tool_name": tool_name, "args": args})

    def report_assistant(self, assistant_name: str, args: Optional[dict] = None) -> None:
        """报告正在调用某个子 agent。"""
        self._emit("assistant_call", f"正在调用子 agent: {assistant_name}",
                   {"assistant_name": assistant_name, "args": args})

    def report_node(self, node: str, update: Optional[dict] = None) -> None:
        """报告图节点流转（入口 astream 层调用）。"""
        self._emit("node_update", f"节点执行: {node}", {"node": node, "update": update})

    def report_task_result(self, result: str) -> None:
        """报告任务最终结果。"""
        self._emit("task_result", "任务执行完成", {"result": result[:2000]})

    def report_session_dir(self, path: str) -> None:
        """报告当前任务工作目录。"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})

    def report_error(self, message: str, data: Optional[dict] = None) -> None:
        """报告执行异常/降级。"""
        self._emit("error", message, data)


monitor = ToolMonitor()


class ConnectionManager:
    """WebSocket 连接管理器（thread_id → 连接）。

    本项目暂无前端，接口预留；绑定后监控事件按 thread_id 定向推送。
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, Any] = {}

    async def connect(self, websocket: Any, thread_id: str) -> None:
        await websocket.accept()
        self.active_connections[thread_id] = websocket

    def disconnect(self, websocket: Any, thread_id: str) -> None:
        if self.active_connections.get(thread_id) is websocket:
            del self.active_connections[thread_id]

    def send_to_thread(self, message: dict, thread_id: Optional[str]) -> None:
        """向指定 thread_id 推送 JSON；无连接时静默跳过（控制台已保底）。"""
        if thread_id and thread_id in self.active_connections:
            try:
                ws = self.active_connections[thread_id]
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(ws.send_json(message))
                else:
                    loop.run_until_complete(ws.send_json(message))
            except Exception as e:  # noqa: BLE001
                logger.warning("[Monitor] send_to_thread failed: %s", e)


manager = ConnectionManager()
