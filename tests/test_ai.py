"""Guard against displaying uncertain model output as a confirmed answer check."""

from __future__ import annotations

import base64
import json
from contextvars import ContextVar
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from k12_server.ai import (
    analyze,
    assess_hint_attempt,
    assess_idle_checkin,
    detect_help_candidate,
    grounded_check_status,
    locate_question,
    normalized_visual_cue_box,
    parse_model_json,
    post_model,
    quoted_target_conflict,
    recheck_single,
    scan_pages,
    teach_from_evidence,
)


def test_astra_requests_omit_unsupported_sampling_parameters(monkeypatch):
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_timeout_seconds=10)
    payload = {"model": "gpt-6-astra", "temperature": 0.1, "top_p": 0.9}
    post_model(settings, payload)
    assert sent[0] == {"model": "gpt-6-astra"}
    assert payload["temperature"] == 0.1
    post_model(settings, {"model": "gpt-6-sol", "temperature": 0.1})
    assert sent[1]["temperature"] == 0.1
    post_model(settings, {"model": "gpt-6.1-sol", "temperature": 0.1})
    assert "temperature" not in sent[2]


def test_missing_sol_channel_falls_back_to_astra_and_remembers_it(monkeypatch):
    import k12_server.ai as ai

    monkeypatch.setattr(ai, "_model_unavailable_until", {})
    seen = []

    def fake_post(url, **kwargs):
        model = kwargs["json"]["model"]
        seen.append(model)
        if model == "gpt-6.1-sol":
            return httpx.Response(503, json={"error": {"code": "model_not_found"}},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/sol-fallback",
                               ai_api_key="test", ai_timeout_seconds=10)
    payload = {"model": "gpt-6.1-sol", "temperature": .1}
    post_model(settings, payload)
    post_model(settings, payload)
    assert seen == ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-astra"]
    assert payload["model"] == "gpt-6.1-sol"


def test_missing_sol_channel_falls_back_during_stream(monkeypatch):
    import k12_server.ai as ai

    monkeypatch.setattr(ai, "_model_unavailable_until", {})
    monkeypatch.setattr(ai, "_usage_family", ContextVar("test_usage_family", default=None))
    seen = []

    class FakeStream:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return None

    def fake_stream(_method, url, **kwargs):
        model = kwargs["json"]["model"]
        seen.append(model)
        if model == "gpt-6.1-sol":
            response = httpx.Response(503, json={"error": {"code": "model_not_found"}},
                                      request=httpx.Request("POST", url))
        else:
            response = httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                                      request=httpx.Request("POST", url))
        return FakeStream(response)

    monkeypatch.setattr(ai.httpx, "stream", fake_stream)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/sol-stream",
                               ai_api_key="test", ai_timeout_seconds=10)
    assert list(ai.stream_model(settings, {"model": "gpt-6.1-sol", "temperature": .2})) == ["ok"]
    assert seen == ["gpt-6.1-sol", "gpt-6-astra"]


def test_hint_attempt_requires_visible_evidence_and_never_certifies_learning(monkeypatch):
    model = {"status": "new_work", "confidence": .9,
             "evidence": "目标框里新写了一行式子", "response": "你已经完全做对了。"}
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)

    def inspect():
        return assess_hint_attempt(settings, b"before", b"after", "当前题", "找条件",
                                   [.1, .2, .3, .4])

    result = inspect()
    assert result["status"] == "new_work"
    assert "完全做对" not in result["response"]
    assert "不要判断答案对错" in sent[0]["messages"][0]["content"]
    assert "目标题框" in sent[0]["messages"][1]["content"][0]["text"]
    model["change_box"] = [.16, .28, .1, .07]
    assert inspect()["change_box"] == [.16, .28, .1, .07]
    model["change_box"] = [.8, .8, .1, .1]
    assert inspect()["change_box"] is None
    model["evidence"] = ""
    assert inspect()["status"] == "unclear"
    model["evidence"] = "有新式子"
    model["confidence"] = .7
    assert inspect()["status"] == "unclear"
    model["confidence"] = .9
    model["status"] = "changed_page"
    assert inspect()["response"].startswith("纸面似乎换了")


def test_visual_cue_pixel_coordinates_are_normalized_without_guessing_formats():
    output = BytesIO()
    Image.new("RGB", (1000, 500), "white").save(output, format="JPEG")
    image = output.getvalue()
    assert normalized_visual_cue_box([400, 150, 80, 30], image) == [.4, .3, .08, .06]
    assert normalized_visual_cue_box([.4, .3, .08, .06], image) == [.4, .3, .08, .06]
    assert normalized_visual_cue_box([552, 155, 22, 5], image, "underline") == [
        .552, .3075, .022, .015]
    assert normalized_visual_cue_box([552, 155, 22, 1], image, "underline") is None
    assert normalized_visual_cue_box([980, 150, 80, 30], image) is None


def test_idle_checkin_needs_same_readable_page_and_no_visible_progress(monkeypatch):
    model = {"same_page": True, "readable": True, "progress": False,
             "confidence": .91, "evidence": "两张纸面相同，没有新增书写"}
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)

    def assess():
        return assess_idle_checkin(settings, b"current", b"reference")

    assert assess()["offer"] is True
    assert "不猜孩子正在做哪道题" in sent[0]["messages"][0]["content"]
    model["progress"] = True
    assert assess() == {"offer": False, "progress": True, "page_changed": False}
    model["progress"] = False
    model["confidence"] = .7
    assert assess()["offer"] is False
    model["confidence"] = .92
    model["same_page"] = False
    assert assess() == {"offer": False, "progress": False, "page_changed": True}
    model["same_page"] = True
    model["readable"] = False
    assert assess()["offer"] is False


def test_help_candidate_keeps_readable_partial_occlusion_but_rejects_uncertainty(monkeypatch):
    model = {"found": True, "same_page": True, "progress": False,
             "label": "当前题", "evidence": "题干和已写内容清晰，边缘有笔遮挡",
             "box": [.2, .3, .3, .2], "confidence": .92, "effort": "short"}

    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)

    def inspect():
        return detect_help_candidate(settings, b"current", b"earlier",
                                     [.25, .32, .2, .15], "primary", "三年级")

    assert inspect()["found"] is True
    model["same_page"] = False
    assert inspect()["found"] is False
    model["same_page"] = True
    model["progress"] = True
    assert inspect()["found"] is False
    model["progress"] = False
    model["confidence"] = .7
    assert inspect()["found"] is False
    model["confidence"] = .92
    model["box"] = [.75, .3, .2, .2]
    assert inspect()["found"] is False


def test_question_locator_requires_visible_unique_evidence(monkeypatch):
    result = {"found": True, "ambiguous": False, "label": "三、（3）",
              "evidence": "纸面可见题干", "confidence": .91,
              "box": [.2, .3, .3, .2]}
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(result)}}]}

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    located = locate_question(settings, b"image", "第3大题第3小题")
    assert located["found"] is True and located["box"] == [.2, .3, .3, .2]
    assert "第3大题第3小题" in sent[0]["messages"][1]["content"][0]["text"]
    locate_question(settings, b"image", "第2大题第1小题第2小题")
    assert "第2大题第1小题第2小题" in sent[1]["messages"][1]["content"][0]["text"]
    assert "最后一层才是目标" in sent[1]["messages"][0]["content"]
    result["ambiguous"] = True
    assert locate_question(settings, b"image", "第3大题第3小题")["found"] is False
    result["ambiguous"] = False
    result["evidence"] = ""
    assert locate_question(settings, b"image", "第3大题第3小题")["found"] is False


def test_vague_question_locator_uses_full_page_and_recent_dialogue(monkeypatch):
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "found": True, "ambiguous": False, "label": "二、（4）",
            "evidence": "本页清晰可见的题干与作答", "confidence": .84,
            "box": [.2, .3, .4, .3],
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    result = locate_question(settings, b"whole-page", "这题我卡住了", "上轮讨论二、（4）", True)
    assert result["found"] is True
    request = sent[0]["messages"][1]["content"]
    assert "上轮讨论二、（4）" in request[0]["text"]
    assert "只有候选唯一" in sent[0]["messages"][0]["content"]
    assert "从左向右" in sent[0]["messages"][0]["content"]
    assert "未被遮挡的小题" in sent[0]["messages"][0]["content"]
    assert "三级编号要定位到最后一层" in sent[0]["messages"][0]["content"]
    assert base64.b64decode(request[1]["image_url"]["url"].split(",")[1]) == b"whole-page"


def test_vague_question_locator_rejects_ambiguous_choice(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "found": True, "ambiguous": True, "label": "二、（4）",
            "evidence": "可见题干", "confidence": .99, "box": [.2, .3, .4, .3],
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    assert locate_question(settings, b"whole-page", "这题", "", True)["found"] is False


def test_pen_locator_receives_clean_page_and_visible_underline(monkeypatch):
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "found": True, "ambiguous": False, "label": "第 2 题",
            "evidence": "第 2 题完整题干", "confidence": .91,
            "box": [.15, .3, .7, .18],
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    buffer = BytesIO()
    Image.new("RGB", (600, 400), "white").save(buffer, format="JPEG")
    clean_page = buffer.getvalue()
    located = locate_question(settings, clean_page, "请提示", "", False,
                              [[[.2, .51], [.7, .51]]])
    assert located["box"] == [.15, .3, .7, .18]
    payload = sent[0]
    assert "下方横线通常指向上方" in payload["messages"][0]["content"]
    content = payload["messages"][1]["content"]
    assert len(content) == 3
    assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == clean_page
    marked = base64.b64decode(content[2]["image_url"]["url"].split(",")[1])
    with Image.open(BytesIO(marked)) as picture:
        red, green, blue = picture.getpixel((300, 204))
    assert red > green * 2 and red > blue * 2


def test_hint_prompt_guides_one_teacher_move_without_hardcoded_subject(monkeypatch):
    captured = []

    def fake_post(*args, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "response": "先读出题目要你找什么？", "target_confirmed": True,
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    result = analyze(settings, b"image", "第1层教师式辅导；先带孩子读题", "primary", "三年级",
                     None, "hint")
    instruction = captured[0]["messages"][0]["content"]
    assert result["target_confirmed"] is True
    assert "读题理解" in instruction and "条件与问题分析" in instruction
    assert "只有需要知道孩子想法时才问一个问题" in instruction
    assert "不得套用固定题目" in instruction


def test_setup_prompt_distinguishes_blank_answer_slots_from_hidden_content(monkeypatch):
    captured = []

    def fake_post(*args, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "response": "纸面题意可读", "label": "只有空白括号被挡住", "ready": True,
            "confidence": .9,
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    result = analyze(settings, b"image", "检查纸面", "primary", "一年级", None, "setup")
    assert result["ready"] is True
    instruction = captured[0]["messages"][0]["content"]
    assert "空白括号" in instruction and "ready=true" in instruction
    assert "真实题干、必要条件或图表中的解题必要信息" in instruction
    assert "孩子尚未作答、答案擦改涂改或答案字迹不清，本身不妨碍开始作业" in instruction
    assert "题干和条件无论印刷、手写或手绘都同样需要读清" in instruction
    assert "看不清的内容不可猜" in instruction
    assert "若图形含义仍可辨认" in instruction
    assert "仅说某物盖在某题上，不足以判为 ready=false" in instruction
    assert "issue_box=null" in instruction


@pytest.mark.parametrize("ready,label", [
    (True, "题干与必要图表清楚，答题区留白"),
    (True, "题干可读，旁题答案有擦改"),
    (True, "手写题干和已知条件清楚，孩子答案字迹不清"),
    (False, "手写题干中的给定长度被涂改遮住"),
    (False, "题目条件反光，无法辨认"),
    (False, "孩子已写答案被涂改覆盖，无法辨认"),
])
def test_setup_normalization_preserves_model_decision_without_reason_overrides(
        monkeypatch, ready, label):
    """Readiness scope belongs in the prompt, never a keyword-based bypass."""
    model = {"ready": ready, "label": label, "confidence": .94,
             "issue_box": [.2, .3, .2, .1], "issue_confidence": .91}
    captured = []

    def fake_post(*args, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    result = analyze(settings, b"exact-image", "检查纸面", "primary", "三年级", None, "setup")
    assert result["ready"] is ready and result["label"] == label
    assert result["issue_box"] == (None if ready else [.2, .3, .2, .1])
    assert len(captured) == 1
    instruction = captured[0]["messages"][0]["content"]
    assert "不解题、不批改" in instruction
    assert "若这些痕迹盖住了题干、必要条件或图表" in instruction
    assert "已写答案中的必要信息" not in instruction
    assert captured[0]["messages"][1]["content"][1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"exact-image").decode())


def test_setup_issue_box_requires_unreadable_content_and_reliable_location(monkeypatch):
    model = {
        "response": "请调整纸面", "label": "局部内容不清", "ready": False,
        "confidence": .9, "issue_box": [.3, .4, .2, .1], "issue_confidence": .84,
    }

    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)

    def inspect():
        return analyze(settings, b"image", "检查纸面", "primary", "三年级", None, "setup")

    assert inspect()["issue_box"] == [.3, .4, .2, .1]
    model["issue_confidence"] = .4
    assert inspect()["issue_box"] is None
    model["issue_confidence"] = .9
    model["confidence"] = .4
    assert inspect()["issue_box"] is None
    model["confidence"] = .9
    model["issue_box"] = [.95, .4, .2, .1]
    assert inspect()["issue_box"] is None
    model["issue_box"] = [.3, .4, .2, .1]
    model["ready"] = True
    assert inspect()["issue_box"] is None


def test_setup_uses_fast_vision_and_reviews_uncertain_results(monkeypatch):
    calls = []
    fast = {"ready": True, "label": "题目可读", "confidence": .92,
            "subject": "数学", "subject_confidence": .9}
    primary = {"ready": False, "label": "第二题条件被挡住", "confidence": .9,
               "issue_box": [.2, .3, .2, .1], "issue_confidence": .9}

    def fake_post(*args, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        output = fast if payload["model"] == "gpt-6-luna" else primary
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="gpt-6-sol", ai_fast_vision_model="gpt-6-luna",
                               ai_timeout_seconds=10)
    result = analyze(settings, b"image", "检查纸面", "primary", "三年级", None, "setup")
    assert result["ready"] is True
    assert [call["model"] for call in calls] == ["gpt-6-luna"]
    assert "reasoning_effort" not in calls[0]
    assert "visible_steps" not in calls[0]["messages"][0]["content"]
    fast["confidence"] = .3
    reviewed = analyze(settings, b"image", "检查纸面", "primary", "三年级", None, "setup")
    assert reviewed["ready"] is False
    assert reviewed["issue_box"] == [.2, .3, .2, .1]
    assert [call["model"] for call in calls[-2:]] == ["gpt-6-luna", "gpt-6-sol"]


def test_setup_retries_primary_when_fast_request_times_out(monkeypatch):
    models = []

    def fake_post(*args, **kwargs):
        model = kwargs["json"]["model"]
        models.append(model)
        if model == "gpt-6-luna":
            raise httpx.ReadTimeout("fast request timed out")
        result = {"ready": True, "label": "readable", "confidence": .9}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="gpt-6-sol", ai_fast_vision_model="gpt-6-luna",
                               ai_timeout_seconds=20)
    result = analyze(settings, b"image", "check", "primary", None, None, "setup")
    assert result["ready"] is True
    assert models == ["gpt-6-luna", "gpt-6-sol"]


def test_idle_check_keeps_uncertain_fast_result_non_interactive(monkeypatch):
    models = []

    def fake_post(*args, **kwargs):
        models.append(kwargs["json"]["model"])
        output = {"same_page": True, "readable": True, "progress": False,
                  "confidence": .6, "evidence": "纸面有遮挡"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="gpt-6-sol", ai_fast_vision_model="gpt-6-luna",
                               ai_timeout_seconds=10)
    assert assess_idle_checkin(settings, b"current", b"reference") == {
        "offer": False, "progress": False, "page_changed": False}
    assert models == ["gpt-6-luna"]


def test_hint_attempt_reviews_uncertain_fast_result(monkeypatch):
    models = []

    def fake_post(*args, **kwargs):
        model = kwargs["json"]["model"]
        models.append(model)
        output = ({"status": "unclear", "confidence": .3}
                  if model == "gpt-6-luna" else
                  {"status": "new_work", "confidence": .9,
                   "evidence": "目标处新写了算式", "response": "我看到新算式，请检查这一步。",
                   "change_box": [.2, .3, .1, .1]})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]},
                              request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="gpt-6-sol", ai_fast_vision_model="gpt-6-luna",
                               ai_timeout_seconds=10)
    result = assess_hint_attempt(settings, b"before", b"after", "第一题", "找条件",
                                 [.1, .2, .4, .4])
    assert result["status"] == "new_work"
    assert models == ["gpt-6-luna", "gpt-6-sol"]


def test_located_hint_checks_only_explicit_content_mismatch(monkeypatch):
    captured = []

    def fake_post(*args, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "response": "先说说这题在问什么？", "target_mismatch": True,
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    result = analyze(settings, b"focus", "纸面定位线索", "primary", "一年级",
                     None, "hint_located", context_image=b"whole")
    assert result["target_mismatch"] is True
    assert "仅在局部题干与给定线索明确冲突时为真" in captured[0]["messages"][0]["content"]


def test_hint_reply_mode_requires_an_explicit_spoken_question(monkeypatch):
    model = {"response": "你能说说为什么先用这个条件吗？",
             "teaching_move": "review_attempt", "reply_mode": "spoken"}
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(model)}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    def hint():
        return analyze(settings, b"paper", "孩子说答案是 24", "primary", "三年级",
                       None, "hint_located")

    assert hint()["reply_mode"] == "spoken"
    assert hint()["teaching_move"] == "review_attempt"
    assert "不盲目附和" in sent[0]["messages"][0]["content"]
    model["response"] = "先在纸上核对这一条件。"
    assert hint()["reply_mode"] == "none"
    model.update(teaching_move="unknown", reply_mode="invalid")
    assert hint()["teaching_move"] == "give_clue"


def test_hint_uses_session_record_for_continuity_but_current_photo_for_truth(monkeypatch):
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "response": "先核对这张纸上的题目。",
        })}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    analyze(settings, b"current", "继续提示", "primary", "一年级", None,
            "hint_located", session_context="孩子刚才说先画图")
    message = sent[0]["messages"]
    assert "孩子刚才说先画图" in message[1]["content"][1]["text"]
    assert "不能作为本次题干、已写答案、正确性" in message[0]["content"]


def test_located_hint_only_keeps_confident_specific_visual_cue(monkeypatch):
    model = {"response": "先看题干里的条件。", "visual_cue": {
        "kind": "underline", "box": [.2, .3, .25, .03], "label": "题干条件", "confidence": .9}}

    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(model)}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)

    def hint():
        return analyze(settings, b"focus", "看题干", "primary", "一年级",
                       None, "hint_located", context_image=b"whole")

    assert hint()["visual_cue"] == {"kind": "underline", "box": [.2, .3, .25, .03],
                                    "label": "题干条件"}
    model["visual_cue"]["confidence"] = .7
    assert hint()["visual_cue"] is None
    model["visual_cue"]["confidence"] = .9
    model["visual_cue"]["box"] = [.95, .3, .25, .03]
    assert hint()["visual_cue"] is None


def test_quoted_target_conflict_catches_character_substitution():
    assert quoted_target_conflict('先写“木”，再解释理由。', '题目问“林”的结构。')
    assert not quoted_target_conflict('先看“林”的两部分。', '题目问“林”的结构。')
    assert not quoted_target_conflict('先读题。', '题目问“林”的结构。')


def test_text_only_hint_fallback_cannot_rename_the_target_character(monkeypatch):
    answer = {"response": "先看“林”由什么组成？"}

    def fake_post(*args, **kwargs):
        assert all(isinstance(message["content"], str) for message in kwargs["json"]["messages"])
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(answer)}}]}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid/model", ai_api_key="test",
                               ai_model="model", ai_timeout_seconds=10)
    assert "“林”" in teach_from_evidence(settings, '题目问“林”的结构', '先读题', 'primary', '一年级')
    answer["response"] = "先看“木”由什么组成？"
    with pytest.raises(HTTPException) as exc:
        teach_from_evidence(settings, '题目问“林”的结构', '先读题', 'primary', '一年级')
    assert exc.value.status_code == 502
    answer["response"] = "题干看不清，请重拍。"
    recovered = teach_from_evidence(settings, '题目问“林”的结构', '先读题', 'primary', '一年级')
    assert "“林”" in recovered and "重拍" not in recovered


def test_model_json_accepts_fenced_object_without_accepting_prose():
    assert parse_model_json('```json\n{"response":"可以继续"}\n```') == {
        "response": "可以继续",
    }
    with pytest.raises(json.JSONDecodeError):
        parse_model_json('请看：{"response":"可以继续"}')


def test_conflicting_judgment_is_withheld_without_another_model_call():
    item = {"status": "looks_correct", "confidence": .9, "answer": "可见作答",
            "evidence": "题干和作答清楚", "reason": "这一步写错，需要订正"}
    assert grounded_check_status(item, [.2, .2, .1, .1]) == ("unclear", True)
    item["reason"] = "已核对题干，答案相符"
    assert grounded_check_status(item, [.2, .2, .1, .1]) == ("looks_correct", False)


def test_blank_evidence_and_nonfinite_confidence_cannot_produce_a_check_mark():
    item = {"status": "looks_correct", "confidence": .95, "answer": "42",
            "evidence": "  ", "reason": "答案相符"}
    assert grounded_check_status(item, [.2, .2, .1, .1])[0] == "unclear"
    item["evidence"] = "题目与作答可见"
    item["confidence"] = "NaN"
    assert grounded_check_status(item, [.2, .2, .1, .1])[0] == "unclear"


def test_single_recheck_reads_page_and_focus_without_the_previous_verdict(monkeypatch):
    def fake_post(*args, **kwargs):
        content = kwargs["json"]["messages"][1]["content"]
        assert len([part for part in content if part["type"] == "image_url"]) == 2
        assert "第 4 题" in content[0]["text"]
        assert "先前结论" not in content[0]["text"]
        result = {"matched": True, "status": "check_again", "answer": "4",
                  "evidence": "印刷条件与作答均清楚", "reason": "需要重新计算",
                  "explanation": "先按题意计算", "confidence": .9,
                  "question_complete": True, "solution_confidence": .96,
                  "reference_answer": "6", "solution_idea": "先找总量",
                  "solution_steps": ["把两部分相加", "得到 6"],
                  "comparison": "孩子写 4，漏加 2。"}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "```json\n" + json.dumps(result) + "\n```"}}]},
            request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = recheck_single(settings, b"whole", b"focused", "第 4 题",
                            "primary", "三年级", None)
    assert result["status"] == "check_again"
    assert result["answer"] == "4"
    assert result["reference_answer"] == "6"
    assert result["solution_steps"] == ["把两部分相加", "得到 6"]
    assert result["comparison"] == "孩子写 4，漏加 2。"


def test_blank_answer_requires_complete_question_and_worked_solution():
    blank = {"status": "check_again", "answer": "", "answer_state": "blank",
             "evidence": "第 2 题的印刷题干和答题格清楚，答题格完全空白",
             "reason": "孩子未作答", "confidence": .95, "question_complete": True,
             "solution_confidence": .96, "reference_answer": "12",
             "solution_steps": ["先算 7+5=12"]}
    box = [.2, .3, .2, .08]
    assert grounded_check_status(blank, box)[0] == "check_again"
    assert grounded_check_status({**blank, "answer": "未作答"}, box)[0] == "check_again"
    assert grounded_check_status({**blank, "answer_state": "uncertain"}, box)[0] == "unclear"
    assert grounded_check_status({**blank, "question_complete": False}, box)[0] == "unclear"
    assert grounded_check_status({**blank, "solution_steps": []}, box)[0] == "unclear"
    assert grounded_check_status(blank, None)[0] == "check_again"
    assert grounded_check_status({**blank, "answer": "12", "answer_state": "written"}, None)[0] == "unclear"


def test_correction_includes_blank_written_question_with_answer_and_steps(monkeypatch):
    model_result = {
        "response": "第 2 题还没有做", "confidence": .95, "visible_answer_count": 1,
        "checks": [{"question": "第 2 题", "status": "check_again", "answer": "未作答",
                    "answer_state": "blank", "evidence": "题干和空白答题格清楚可见",
                    "reason": "孩子未作答", "confidence": .96,
                    "question_complete": True, "solution_confidence": .95,
                    "reference_answer": "12", "solution_idea": "把两部分相加",
                    "solution_steps": ["先列式 7+5", "算出 12"],
                    "box": None}],
    }

    def fake_post(*args, **kwargs):
        instruction = kwargs["json"]["messages"][0]["content"]
        assert "以及孩子未作答的题" in instruction
        assert "status=check_again" in instruction
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(model_result, ensure_ascii=False)}}]},
            request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = analyze(settings, b"\xff\xd8image", "批改", "primary", "三年级", None, "correction")
    check = result["checks"][0]
    assert check["status"] == "check_again"
    assert check["answer_state"] == "blank"
    assert check["answer"] == ""
    assert check["box"] is None
    assert check["reference_answer"] == "12"
    assert check["solution_steps"] == ["先列式 7+5", "算出 12"]
    assert "未作答" in check["comparison"]


def test_single_recheck_keeps_confirmed_blank_as_wrong_with_solution(monkeypatch):
    model_result = {"matched": True, "status": "check_again", "answer": "",
                    "answer_state": "blank", "evidence": "题干和答题格均完整可见，格内空白",
                    "reason": "孩子未作答", "confidence": .95, "question_complete": True,
                    "solution_confidence": .95, "reference_answer": "6",
                    "solution_idea": "先合并数量", "solution_steps": ["4+2=6"],
                    "comparison": "孩子没有填写答案。"}

    def fake_post(*args, **kwargs):
        assert "answer_state=blank" in kwargs["json"]["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(model_result, ensure_ascii=False)}}]},
            request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = recheck_single(settings, b"whole", b"focused", "第 2 题",
                            "primary", "三年级", None)
    assert result["status"] == "check_again"
    assert result["answer_state"] == "blank"
    assert result["reference_answer"] == "6"
    assert result["solution_steps"] == ["4+2=6"]


def test_correction_downgrades_uncertain_or_unsupported_checks(monkeypatch):
    model_result = {
        "response": "检查完成", "confidence": .9,
        "checks": [
            {"question": "第 1 题", "status": "looks_correct", "answer": "12",
             "evidence": "纸上清楚写 12", "reason": "算式相符", "confidence": .92,
             "question_complete": True, "solution_confidence": .94,
             "reference_answer": "12", "solution_idea": "先相加两个数量",
             "solution_steps": ["先算 7+5", "得到 12"],
             "comparison": "孩子写 12，与计算结果一致。",
             "diagram": {"caption": "线段图", "description": "两段合起来是十二", "elements": [
                 {"kind": "rect", "x": 10, "y": 35, "w": 30, "h": 15},
                 {"kind": "rect", "x": 40, "y": 35, "w": 45, "h": 15},
                 {"kind": "text", "x": 50, "y": 25, "text": "12"},
                 {"kind": "svg", "content": "<script>alert(1)</script>"},
                 {"kind": "circle", "x": 99, "y": 99, "r": 20},
             ]},
             "box": [.2, .3, .1, .08],
             "steps": [{"label": "列式", "status": "check_again", "box": [.1, .5, .5, .06],
                        "evidence": "纸上写了加法", "explanation": "这一步应检查运算符"}]},
            {"question": "第 2 题", "status": "looks_correct", "answer": "8",
             "evidence": "字迹有遮挡", "reason": "可能相符", "confidence": .55,
             "question_complete": True, "solution_confidence": .99,
             "reference_answer": "8", "solution_steps": ["猜出来的"],
             "box": [1.2, .1, .1, .1]},
            {"question": "第 3 题", "status": "looks_correct", "answer": "",
             "evidence": "题目清楚", "reason": "模型猜测", "confidence": .95},
            {"question": "阅读理解第 4 题", "status": "needs_discussion", "answer": "我觉得主角很勇敢",
             "evidence": "纸上写有这句话", "reason": "可以讨论文中的依据", "confidence": .9,
             "box": None,
             "steps": [{"label": "引用原文", "status": "needs_discussion", "box": None,
                        "evidence": "写出了一个例子", "explanation": "可再说清例子与观点的关系"}]},
        ],
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(model_result)}}]}

    def fake_post(*args, **kwargs):
        instruction = kwargs["json"]["messages"][0]["content"]
        assert "题干文字和题中图形不能算孩子已答" in instruction
        assert "不要因题目不是印刷的就跳过" in instruction
        assert "开放表达" in instruction
        assert "正确答案" in instruction
        assert "可见选项没有正确答案" in instruction
        assert kwargs["timeout"] >= 240
        return FakeResponse()

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = analyze(settings, b"\xff\xd8image", "批改", "primary", "三年级", None, "correction")
    assert [item["status"] for item in result["checks"]] == ["looks_correct", "unclear", "unclear", "needs_discussion"]
    assert result["checks"][0]["box"] == [.2, .3, .1, .08]
    assert result["checks"][0]["reference_answer"] == "12"
    assert result["checks"][0]["solution_steps"] == ["先算 7+5", "得到 12"]
    assert [item["kind"] for item in result["checks"][0]["diagram"]["elements"]] == [
        "rect", "rect", "text"]
    assert result["checks"][0]["steps"][0]["box"] == [.1, .5, .5, .06]
    assert result["checks"][1]["box"] is None
    assert result["checks"][1]["reference_answer"] == ""
    assert result["checks"][1]["diagram"] is None
    assert result["checks"][3]["box"] is None
    assert result["checks"][3]["steps"][0]["status"] == "needs_discussion"


def test_correction_covers_both_columns_of_24_question_page(monkeypatch):
    model_result = {
        "response": "两列已检查", "confidence": .9, "visible_answer_count": 24,
        "checks": [
            {"question": f"第 {index} 题", "status": "looks_correct", "answer": str(index),
             "evidence": f"第 {index} 题纸上可见", "reason": "与算式一致", "confidence": .95,
             "box": [.2 if index <= 12 else .7, (index - 1) % 12 * .06, .06, .04]}
            for index in range(1, 25)
        ],
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(model_result)}}]}

    monkeypatch.setattr("k12_server.ai.httpx.post", lambda *args, **kwargs: FakeResponse())
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = analyze(settings, b"\xff\xd8image", "批改", "primary", "一年级", None, "correction")
    assert result["visible_answer_count"] == 24
    assert len(result["checks"]) == 24
    assert result["checks"][12]["question"] == "第 13 题"


def test_correction_timeout_has_actionable_chinese_message(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("upstream did not respond")

    monkeypatch.setattr("k12_server.ai.httpx.post", timeout)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    with pytest.raises(HTTPException) as error:
        analyze(settings, b"\xff\xd8image", "批改", "primary", "一年级", None, "correction")
    assert error.value.status_code == 504
    assert "重试" in error.value.detail


def test_correction_can_use_whole_page_context_while_locating_on_detail(monkeypatch):
    def fake_post(*args, **kwargs):
        message = kwargs["json"]["messages"][1]["content"]
        assert "整体参考图、待批改局部图" in message[0]["text"]
        assert len([part for part in message if part["type"] == "image_url"]) == 2
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"response": "已检查", "checks": []})}}]},
            request=httpx.Request("POST", "https://example.com/v1/chat/completions"))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = analyze(settings, b"detail", "批改局部", "primary", None, None,
                     "correction", context_image=b"whole-page")
    assert result["checks"] == []


def test_page_scan_keeps_two_separate_readable_pages_and_rejects_guesswork(monkeypatch):
    model_result = {
        "reason": "左页和右页都清晰",
        "pages": [
            {"box": [.5, .05, .46, .9], "confidence": .94, "readable": True,
             "subject": "语文", "printed_page": "第 43 页"},
            {"box": [.03, .06, .43, .88], "confidence": .91, "readable": True,
             "subject": "语文", "printed_page": "第 42 页"},
        ],
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(model_result)}}]}

    monkeypatch.setattr("k12_server.ai.httpx.post", lambda *args, **kwargs: FakeResponse())
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = scan_pages(settings, b"\xff\xd8image")
    assert [page["printed_page"] for page in result["pages"]] == ["第 42 页", "第 43 页"]
    model_result["pages"][0]["readable"] = False
    assert len(scan_pages(settings, b"\xff\xd8image")["pages"]) == 1
    model_result["pages"][0].update({"readable": True, "box": [.04, .06, .43, .88]})
    assert scan_pages(settings, b"\xff\xd8image")["pages"] == []


def test_page_scan_recovers_unread_spread_side_with_a_second_image(monkeypatch):
    image = BytesIO()
    Image.new("RGB", (1200, 800), "white").save(image, format="JPEG")
    model_results = [
        {"reason": "先找到左页", "pages": [
            {"box": [.08, .04, .42, .9], "confidence": .95, "readable": True,
             "subject": "语文", "printed_page": "1"}]},
        {"reason": "右侧还有一页", "pages": [
            {"box": [.08, .05, .75, .88], "confidence": .9, "readable": True,
             "subject": "语文", "printed_page": "2"}]},
    ]
    sizes = []

    def fake_post(*args, **kwargs):
        encoded = kwargs["json"]["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as submitted:
            sizes.append(submitted.size)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(model_results[len(sizes) - 1])}}]},
            request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("k12_server.ai.httpx.post", fake_post)
    settings = SimpleNamespace(ai_api_url="https://example.com/v1/chat/completions",
                               ai_api_key="test", ai_model="vision", ai_timeout_seconds=5)
    result = scan_pages(settings, image.getvalue())
    assert [page["printed_page"] for page in result["pages"]] == ["第 1 页", "第 2 页"]
    assert sizes[0] == (1200, 800)
    assert sizes[1][0] < sizes[0][0]
