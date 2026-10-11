"""公共安全计算：白名单四则求值（单一实现，analysis 节点与 LLM 工具共用）。

来源：从 app/graph/nodes/analysis.py 抽取（原来 `_safe_calc` 私有），
     统一收敛到 utils，杜绝两处实现漂移。

安全：只允许 变量名∈values 键 + 四则运算 + 括号 + 一元负号 + 数字，
     基于 ast 白名单（不 eval），杜绝注入。
"""

from __future__ import annotations

import ast
import operator

_ALLOWED_BINOP = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def safe_calc(formula: str, values: dict[str, float]) -> float:
    """安全四则求值：仅允许变量名 ∈ values 键 + 四则/括号/一元负号/数字。

    formula 是受控资产（metric_dict 注册），此处仍做白名单防御（防未来注入）。
    """
    tree = ast.parse(formula, mode="eval")

    def eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOP:
            return _ALLOWED_BINOP[type(node.op)](eval_node(node.left), eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -eval_node(node.operand)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"公式变量未提供: {node.id}（formula: {formula}）")
            return values[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"公式含不允许的表达式: {formula}")

    return eval_node(tree.body)
