"""计算工具：把公共安全计算（app/utils/safe_calc.py）暴露成 LLM 可调用工具。

原则（"能代码算的绝不让 LLM 算"）：环比/汇总/区间等数值计算让 LLM 调本工具，
避免心算错误；实现复用公共 safe_calc（单一实现，不造轮子）。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.api.monitor import monitor
from app.utils.safe_calc import safe_calc


@tool
def calc(formula: str, values: dict) -> str:
    """安全数值计算：把公式里的变量名用给定数值代入，四则运算求值。

    何时调用：
      - 需要算百分比/环比/汇总/区间/复合指标时（如 "(gmv - gmv_last) / gmv_last"）；
      - 任何你（LLM）不确定心算结果的数值运算——交给本工具，别自己算。
    何时不要调用：
      - 不需要数值运算的文本/逻辑问题。

    参数填写建议：
      - formula：四则表达式，变量名与 values 的键一致，如 "(a-b)/b"；
      - values：变量名 → 数值的字典，如 {"a": 1100, "b": 1000}。
    安全：仅允许四则运算/括号/数字/已提供变量，其他一律拒绝。
    """
    monitor.report_tool("calc", {"formula": formula})
    try:
        return str(safe_calc(formula, values))
    except Exception as e:  # noqa: BLE001 —— 公式错误返回说明，LLM 可据此修正重试
        return f"计算失败：{e}（请检查 formula 与 values 是否匹配）"
