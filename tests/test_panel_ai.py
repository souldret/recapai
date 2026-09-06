"""Vision kutu parse ve maliyet tahmini."""

from core.panel_ai import parse_vision_boxes, estimate_vision_cost


def test_parse_percent_boxes():
    text = '{"panels":[{"x":10,"y":10,"w":40,"h":50},{"x":55,"y":10,"w":40,"h":50}]}'
    boxes = parse_vision_boxes(text, 1000, 2000)
    assert len(boxes) == 2
    assert boxes[0] == (100, 200, 400, 1000)


def test_parse_normalized_0_1():
    text = '{"panels":[{"x":0.1,"y":0.2,"w":0.3,"h":0.4}]}'
    boxes = parse_vision_boxes(text, 1000, 1000)
    assert boxes[0] == (100, 200, 300, 400)


def test_parse_markdown_fence():
    text = '```json\n{"panels":[{"x":0,"y":0,"w":50,"h":50}]}\n```'
    boxes = parse_vision_boxes(text, 200, 200)
    assert len(boxes) == 1
    assert boxes[0][2] == 100


def test_cost_positive():
    est = estimate_vision_cost("google/gemini-2.5-flash", 10)
    assert est["pages"] == 10
    assert est["usd"] > 0
    assert "Tahmini" in est["note"] or "tahmini" in est["note"].lower() or "$" in est["note"]
