"""中间件：接近 token 上限时自动总结对话历史（防上下文爆掉）。

注意：deepagents 基础栈已**默认挂载** SummarizationMiddleware（graph.py 基础栈顺序固定含它），
重复传给 middleware= 会产生两个 Summarization 实例。因此本文件默认 build() 返回 None（不重复挂载），
仅当需要自定义行为（自定义总结 prompt / 触发阈值）时才启用。

自定义用法（需 backend，deepagents 内部默认 backend 已带默认总结）：
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.sandbox import SandboxBackend
    backend = CompositeBackend([SandboxBackend()])
    mw = create_summarization_middleware(model=llm, backend=backend, trim_tokens_to_summarize=60000)
"""

from __future__ import annotations


def build() -> None:
    """deepagents 基础栈已内置 SummarizationMiddleware，默认不重复挂载。

    返回 None 表示无需额外挂载；如需自定义总结行为，改这里返回
    create_summarization_middleware(model=..., backend=...) 的实例。
    """
    return None
