"""SmartAnalyst FastAPI 服务：对话 + 澄清断点 + SSE 节点监控 + 前端静态托管。

接口：
  POST /api/chat           首次提问：跑完整图；遇 clarify 中断 → need_clarify=true + prompt
  POST /api/chat/resume    澄清回答：Command(resume) 从断点继续跑到 END
  GET  /api/events         SSE 节点流转（前端轮询 monitor 缓冲，thread_id 维度）
  GET  /                   静态托管 frontend/index.html

关键设计：
  - graph 模块级单例（checkpointer=InMemorySaver 在实例内，resume 必须复用同一实例 + thread_id）
  - astream(stream_mode="updates") 逐节点上报 monitor（deepsearch 式节点监控）
  - 中断检测走 aget_state(config).interrupts（astream 遇 interrupt 提前结束，不报错）
  - 每个会话独立工作目录 doc/output/session_{thread_id}（ContextVar 注入，照 deepsearch）

用法（项目根下）：
  uvicorn app.api.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.context import reset_session_context, set_session_context, set_thread_context
from app.api.monitor import monitor
from app.graph.graph import build_graph

# server.py 位于 app/api/ 下：parents[0]=api, parents[1]=app, parents[2]=项目根
project_root = Path(__file__).resolve().parents[2]
output_root = project_root / "doc" / "output"
# 前端为 Vite 工程：构建产物 frontend/dist（不存在时跳过挂载，避免 404）
frontend_dir = project_root / "frontend" / "dist"

# 图单例：checkpointer 在实例内（InMemorySaver），resume 必须复用同一实例
graph = build_graph()

app = FastAPI(title="SmartAnalyst", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求契约 ──
class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    thread_id: str | None = Field(None, description="续聊会话 ID（首问可空，服务端生成）")


class ResumeRequest(BaseModel):
    thread_id: str = Field(..., description="已中断会话 ID")
    answer: str = Field("", description="用户对澄清问题的回答（clarify 用）")
    decisions: list[dict] | None = Field(None, description="HITL 决策列表：[{'type':'approve'} | {'type':'reject','message':...}]（按序对应 action_requests）")


def _session_setup(thread_id: str) -> tuple[object, object]:
    """会话上下文：独立工作目录 + ContextVar 注入（照 deepsearch run_deep_agent）。"""
    session_dir = output_root / f"session_{thread_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    s_token = set_session_context(str(session_dir).replace("\\", "/"))
    t_token = set_thread_context(thread_id)
    monitor.report_session_dir(str(session_dir).replace("\\", "/"))
    return s_token, t_token


async def _run_graph(inputs: dict, thread_id: str) -> dict:
    """跑图：astream 逐节点上报监控 → 中断检查 → 返回统一结果。"""
    config = {"configurable": {"thread_id": thread_id}}
    async for chunk in graph.astream(inputs, config, stream_mode="updates"):
        for node, update in chunk.items():
            monitor.report_node(node, update)

    snapshot = await graph.aget_state(config)
    if snapshot.interrupts:  # 两种中断：clarify（信息补全）/ human_in_the_loop（HITL 工具确认）
        payload = snapshot.interrupts[0].value
        ptype = payload.get("type", "")
        if ptype == "human_in_the_loop":
            # HITL：把待确认动作透传前端（name/args/description），resume 时前端回传 decisions
            hitl_requests = [
                {"name": r.get("name", ""), "args": r.get("args", {}),
                 "description": r.get("description", "")}
                for r in (payload.get("action_requests") or [])
            ]
            prompt = "AI 请求执行以下操作，请确认：\n" + "\n".join(
                f"- {r.get('name')}: {r.get('args', {})}" for r in hitl_requests
            )
            return {
                "thread_id": thread_id,
                "need_clarify": True,
                "clarify_type": "hitl",
                "prompt": prompt,
                "missing": [],
                "hitl_requests": hitl_requests,
                "report": None,
                "errors": snapshot.values.get("errors") or [],
                "status": snapshot.values.get("status", ""),
            }
        return {
            "thread_id": thread_id,
            "need_clarify": True,
            "clarify_type": "clarify",
            "prompt": payload.get("prompt", "请补充信息"),
            "missing": payload.get("missing", []),
            "hitl_requests": [],
            "report": None,
            "errors": snapshot.values.get("errors") or [],
            "status": snapshot.values.get("status", ""),
        }
    values = snapshot.values
    return {
        "thread_id": thread_id,
        "need_clarify": False,
        "clarify_type": None,
        "prompt": None,
        "missing": [],
        "hitl_requests": [],
        "report": values.get("report"),
        "errors": values.get("errors") or [],
        "status": values.get("status", ""),
    }


# ── 接口 ──
@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="question 不能为空")
    thread_id = req.thread_id or uuid.uuid4().hex[:8]
    s_token, t_token = _session_setup(thread_id)
    try:
        return await _run_graph(
            {"messages": [{"role": "user", "content": req.question}]}, thread_id
        )
    except Exception as e:  # noqa: BLE001 —— 错误是数据，返回降级结果
        return {
            "thread_id": thread_id, "need_clarify": False, "clarify_type": None,
            "prompt": None, "missing": [], "hitl_requests": [],
            "report": None,
            "errors": [{"node": "api", "msg": f"执行失败: {e}"}],
            "status": "failed",
        }
    finally:
        reset_session_context(s_token, t_token)


@app.post("/api/chat/resume")
async def resume(req: ResumeRequest) -> dict:
    s_token, t_token = _session_setup(req.thread_id)
    try:
        # 分流：HITL 决策（decisions 按序对应 action_requests）/ clarify 文本回答
        if req.decisions:
            resume_value: dict = {"decisions": req.decisions}
        else:
            resume_value = req.answer
        return await _run_graph(Command(resume=resume_value), req.thread_id)
    except Exception as e:  # noqa: BLE001
        return {
            "thread_id": req.thread_id, "need_clarify": False, "clarify_type": None,
            "prompt": None, "missing": [], "hitl_requests": [],
            "report": None,
            "errors": [{"node": "api", "msg": f"恢复执行失败: {e}"}],
            "status": "failed",
        }
    finally:
        reset_session_context(s_token, t_token)


@app.get("/api/events")
async def events(thread_id: str = Query(...), after: int = Query(0)):
    """SSE：thread_id 维度的节点流转事件流（前端轮询缓冲增量）。"""

    async def stream():
        idx = after
        idle = 0
        while idle < 60:  # 空闲 60 次（~30s）后关闭，前端可重连
            new_events = monitor.get_events(thread_id, idx)
            if new_events:
                idx = monitor.next_index(thread_id)
                idle = 0
                for ev in new_events:
                    yield f"data: {ev.get('message', '')}\n\n"
            else:
                idle += 1
                await asyncio.sleep(0.5)
        yield "data: [流结束]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# 静态托管前端（最后挂载根路径，/api/* 优先匹配）
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
