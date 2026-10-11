"""monitor SSE 缓冲有界 + 空闲 TTL 清理。

RED：当前 `_buffer: dict[str, list]` 无限 append，长期运行内存无限增长。
期望：单 thread 事件有上限（丢最旧），过期 thread 惰性清理。
"""

from __future__ import annotations

import time

from app.api.monitor import ToolMonitor


def _fresh_monitor() -> ToolMonitor:
    """重置单例内部状态（避免跨测试污染）。"""
    m = ToolMonitor()
    m._buffer = {}
    m._last_access = {}
    m._seq = {}
    m.websocket_manager = None
    return m


def test_buffer_bounded_per_thread(monkeypatch):
    """单 thread 事件数超过上限后丢弃最旧、保留最新。"""
    m = _fresh_monitor()
    monkeypatch.setattr("app.api.monitor.get_thread_context", lambda: "t1")
    total = m._MAX_EVENTS_PER_THREAD + 50
    for i in range(total):
        m._emit("node_update", f"事件{i}", {"node": "x"})
    events = m.get_events("t1")
    assert len(events) <= m._MAX_EVENTS_PER_THREAD
    assert events[-1]["message"] == f"事件{total - 1}"  # 最新保留
    assert events[0]["message"] == f"事件{total - m._MAX_EVENTS_PER_THREAD}"  # 最旧的被丢


def test_expired_thread_cleaned_lazily(monkeypatch):
    """超过 TTL 未访问的 thread，在下次 emit 时被惰性清理。"""
    m = _fresh_monitor()
    monkeypatch.setattr("app.api.monitor.get_thread_context", lambda: "t1")
    m._emit("node_update", "a")
    assert "t1" in m._buffer

    # 伪造 t1 很久未访问，然后另一个 thread 触发 emit → 惰性清理
    m._last_access["t1"] = time.time() - (m._EVENT_TTL_SECONDS + 100)
    monkeypatch.setattr("app.api.monitor.get_thread_context", lambda: "t2")
    m._emit("node_update", "b")
    assert "t1" not in m._buffer
    assert "t2" in m._buffer
