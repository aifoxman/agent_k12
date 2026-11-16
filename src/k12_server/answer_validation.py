"""Small, deterministic checks; unsupported mathematics remains unverified."""
from __future__ import annotations

import ast
import re
from fractions import Fraction

OPERATORS = str.maketrans({"×": "*", "÷": "/", "−": "-", "－": "-", "＝": "=",
                           "＋": "+", "／": "/", "＊": "*", "（": "(", "）": ")"})


def arithmetic_value(expression: str) -> Fraction:
    """Evaluate bounded decimal arithmetic without eval, names, powers or calls."""
    expression = expression.strip().translate(OPERATORS)
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
        quoted_next = False
        for line in re.split(r"[\n。；;]", text[:8000]):
            line = line.strip()
            if not line:
                continue
            cited, quoted_next = quoted_next, False
            parts = re.split(r"[：:]", line)
            if len(parts) > 1:
                prefix = "：".join(parts[:-1])
                asserted = re.search(r"(?<!不)正确计算|正确算式|订正|验算|检验|计算结果|重新计算", prefix)
                cited = not asserted and bool(re.search(r"错误示例|错误算式|错误选项|错例|反例|学生原式|孩子原式|原作答|[A-H]项", prefix))
                quoted_next = bool(cited and not parts[-1].strip())
            if cited:
                continue
            equation = parts[-1].strip().rstrip("，,.").translate(OPERATORS)
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
    match = re.match(r"^\s*(?:答案\s*[:：]?\s*)?(?:(?:应)?选(?:择)?|是|为)?\s*[(（]?([A-H])(.*)$", value, re.S)
    if not match:
        # A student may write the reasoning first and the selection last.
        declarations = list(re.finditer(r"(?:选(?:择)?|答案(?:是|为|[:：]))\s*([A-H])(?=$|[^A-Za-z])", value))
        if len({m[1] for m in declarations}) != 1:
            return None
        declaration = declarations[0]
        prefix = value[max(0, declaration.start() - 10):declaration.start()]
        if re.search(r"(?:不能|不|不是|别)\s*$|(?:可能|也许|或许).{0,4}$", prefix):
            return None
        # Reuse the same ambiguity/retraction checks for the remainder.
        return choice_letter(declaration[1] + value[declaration.end():])
    rest = match[2].strip()
    if not rest or re.fullmatch(r"[）).。\s]+", rest):
        return match[1]
    if not re.match(r"^[）).。:：，,；;（(]", rest):
        return None
    explanation = rest.lstrip("）).。:：，,；;（( ")
    if (re.match(r"[A-H](?:$|[\s、，,。.)）])", explanation)
            or re.search(r"(?:不是|不选|应选|改选|而是|或者|或)\s*[A-H]", explanation)):
        return None
    for assertion in re.finditer(r"(?:选(?:择)?|答案(?:是|为)?|最终(?:答案)?(?:是|为)?|其实|应该是|应为|[和与及或])\s*([A-H])", explanation):
        if assertion[1] == match[1]:
            continue
        prefix = explanation[max(0, assertion.start() - 10):assertion.start()]
        # Discussing an unchosen option is fine; another positive or uncertain
        # selection declaration makes the observed answer ambiguous.
        if re.search(r"(?:不能|不要|不应|不|别)\s*$", prefix) and not re.search(r"不能不\s*$", prefix):
            continue
        return None
    if any(letter != match[1] for letter in re.findall(r"([A-H])(?:才是|才对|是正确答案|是最终答案)", explanation)):
        return None
    return match[1]


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


def choice_comparison_conflict(item: dict) -> bool:
    """An unsupported optional comparison is not a contradictory student answer."""
    value = item.get("choice_evaluation")
    if not isinstance(value, dict) or not str(item.get("answer") or "").strip():
        return False
    selected, expected = choice_letter(item.get("answer")), choice_letter(item.get("reference_answer"))
    if (selected and isinstance(value.get("selected"), str) and len(value["selected"]) == 1
            and value["selected"] in "ABCDEFGH" and value["selected"] != selected):
        return True
    if (expected and isinstance(value.get("expected"), str) and len(value["expected"]) == 1
            and value["expected"] in "ABCDEFGH" and value["expected"] != expected):
        return True
    if selected and expected:
        matches = selected == expected
        if type(value.get("selection_matches")) is bool and value["selection_matches"] != matches:
            return True
        if item.get("status") in {"looks_correct", "check_again"}:
            return matches != (item["status"] == "looks_correct")
    return False


def false_option_is_requested(item: dict) -> bool:
    value = validated_choice_evaluation(item)
    if not value or not value["selection_matches"] or value.get("selected_statement_truth") != "false":
        return False
    requirement = re.sub(r"\s+", "", value["requirement"])
    visible = re.sub(r"\s+", "", str(item.get("visible_question_text") or ""))
    # Only explicit negative asks qualify. Double negatives and an unrelated
    # quoted statement are not evidence that the child should select a falsehood.
    if re.search(r"[“‘\"']", requirement):
        return False
    contexts = []
    start = visible.find(requirement)
    while start >= 0:
        prefix = re.split(r"[，。；;！？!?]", visible[max(0, start - 12):start])[-1]
        contexts.append(prefix + requirement)
        start = visible.find(requirement, start + 1)
    qualified = []
    for context in contexts:
        negative = False
        for match in re.finditer(r"不正确|不恰当|不属于|不符合|有误|错误", context):
            prefix = context[max(0, match.start() - 12):match.start()]
            if re.search(r"(?:没有|未发现|不存在|不是|并非|不能认为|不代表).{0,6}$", prefix):
                return False
            if re.match(r"(?:的)?(?:一项|选项|说法|句子|表述|是|为|[（(])", context[match.end():]):
                negative = True
        qualified.append(negative)
    return bool(qualified and all(qualified))
