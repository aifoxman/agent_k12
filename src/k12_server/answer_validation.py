"""Small, deterministic checks; unsupported mathematics remains unverified."""
from __future__ import annotations

import ast
import re
from fractions import Fraction


def arithmetic_value(expression: str) -> Fraction:
    """Evaluate bounded decimal arithmetic without eval, names, powers or calls."""
    expression = expression.strip().translate(str.maketrans({"×": "*", "÷": "/", "−": "-"}))
    expression = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expression)
    if len(expression) > 160 or not re.fullmatch(r"[\d.+*/()\s-]+", expression):
        raise ValueError("unsupported arithmetic")
    tree = ast.parse(expression.strip(), mode="eval")
    if len(list(ast.walk(tree))) > 64:
        raise ValueError("oversized arithmetic")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            literal = ast.get_source_segment(expression, node)
            if not literal or len(literal) > 16:
                raise ValueError("oversized literal")
            return Fraction(literal)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("unsupported arithmetic")

    return visit(tree.body)


def arithmetic_conflict(*texts: object) -> bool:
    """Reject false standalone exact equations, never infer a full word problem."""
    pieces = [part for text in texts for part in (text[:16] if isinstance(text, list) else [text])]
    for text in pieces:
        if not isinstance(text, str):
            continue
        for line in re.split(r"[\n。；;：:]", text[:8000]):
            equation = line.strip().rstrip("，,.")
            if ("=" not in equation or len(equation) > 240
                    or not re.fullmatch(r"[\d.+*/()×÷−%=\s-]+", equation)):
                continue
            try:
                parts = [arithmetic_value(part) for part in equation.split("=")]
            except ZeroDivisionError:
                return True
            except (ValueError, SyntaxError, RecursionError):
                continue
            if any(value != parts[0] for value in parts[1:]):
                return True
    return False


def choice_letter(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(?:答案\s*[:：]?\s*)?(?:(?:应)?选(?:择)?|是|为)?\s*[(（]?([A-H])[）).。\s]*", value)
    return match[1] if match else None


def validated_choice_evaluation(item: dict) -> dict | None:
    """Bind a model comparison to the observed answer, reference and literal ask."""
    value = item.get("choice_evaluation")
    if not isinstance(value, dict) or item.get("question_complete") is not True:
        return None
    selected, expected = choice_letter(item.get("answer")), choice_letter(item.get("reference_answer"))
    requirement = value.get("requirement")
    visible = re.sub(r"\s+", "", str(item.get("visible_question_text") or ""))
    if (not selected or not expected or value.get("selected") != selected
            or value.get("expected") != expected or not isinstance(requirement, str)
            or not 2 <= len(requirement.strip()) <= 100
            or re.sub(r"\s+", "", requirement) not in visible
            or type(value.get("selection_matches")) is not bool
            or value["selection_matches"] != (selected == expected)):
        return None
    return value


def false_option_is_requested(item: dict) -> bool:
    value = validated_choice_evaluation(item)
    return bool(value and value["selection_matches"]
                and value.get("selected_statement_truth") == "false"
                and re.search(r"不正确|不恰当|不属于|不符合|有误|错误|不是", value["requirement"]))
