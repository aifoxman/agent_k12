"""A delivered status message is not a finished teaching explanation."""

import pytest
from test_api import make_client


@pytest.mark.parametrize("tier,turn,delivery,expected,can_continue", [
    (1, "give_clue", True, "ready", True),
    (2, "give_clue", True, "ready", False),
    (1, "clarify_target", True, "needs_clarification", False),
    (2, "clarify_target", True, "needs_clarification", False),
    (1, "parent_handoff", True, "needs_parent", False),
    (2, "parent_handoff", False, "needs_parent", False),
    (1, "give_clue", False, "interrupted", False),
    (2, "give_clue", False, "interrupted", False),
])
def test_status_only_delivery_cannot_offer_another_teaching_tier(
    monkeypatch, tmp_path, tier, turn, delivery, expected, can_continue,
):
    _, module = make_client(monkeypatch, tmp_path)
    result = module._hint_tier_contract({
        "id": "current-hint", "target_box": [.1, .1, .5, .3],
        "visual_beats": [{"speech": "Current response", "teaching_move": turn}],
        "teaching_move": turn, "delivery_complete": delivery,
    }, tier, {"evidence": "Current printed question", "read_confidence": .96})
    assert result["teaching_tier"] == tier
    assert result["explanation_status"] == expected
    assert result["continuation_available"] is can_continue
    # Keep the existing binding for a specific clarification or same-layer retry.
    assert result["continuation_hint_id"] == "current-hint"
