"""
Regression test for the exact bug that caused agent chat to 500 in
production: Gemini's SDK rejects a 'default' field in tool parameter
schemas, which our tool_defs legitimately include (e.g. top_k's default
value). This tests the schema sanitizer in isolation - no live Gemini
API call, no API key needed, just proving the function does what it claims.
"""
from app.core.llm import _strip_unsupported_schema_fields


def test_strips_default_field():
    schema = {
        "type": "object",
        "properties": {
            "top_k": {"type": "integer", "description": "x", "default": 15},
        },
    }
    cleaned = _strip_unsupported_schema_fields(schema)
    assert "default" not in cleaned["properties"]["top_k"]


def test_preserves_supported_fields():
    schema = {
        "type": "object",
        "properties": {
            "job_titles": {"type": "array", "items": {"type": "string"}, "description": "keep this"},
        },
        "required": ["job_titles"],
    }
    cleaned = _strip_unsupported_schema_fields(schema)
    assert cleaned["properties"]["job_titles"]["description"] == "keep this"
    assert cleaned["required"] == ["job_titles"]


def test_strips_default_from_nested_items():
    schema = {
        "type": "array",
        "items": {"type": "integer", "default": 0},
    }
    cleaned = _strip_unsupported_schema_fields(schema)
    assert "default" not in cleaned["items"]
