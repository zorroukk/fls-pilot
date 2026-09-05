from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_tool_registration_baseline import (  # noqa: E402
    EXPECTED_DYNAMIC_SAFETY_CLASSES,
    _classification_mismatches,
    _runtime_safety_by_name,
)

from fl_studio_mcp.server import build_server
from fl_studio_mcp.tool_metadata import SafetyAwareFastMCP


def _registered_tools():
    return list(asyncio.run(build_server().list_tools()))


def test_registered_safety_classes_survive_mcp_serialization() -> None:
    tools = _registered_tools()

    summary = Counter(
        (tool.meta or {}).get("safetyClass", "unannotated") for tool in tools
    )

    assert summary == {
        "external-write": 4,
        "read-only": 49,
        "server-state": 4,
        "write-safe": 33,
    }

    for tool in tools:
        safety_class = (tool.meta or {}).get("safetyClass")
        assert safety_class in {
            "external-write",
            "read-only",
            "server-state",
            "transient",
            "write-safe",
        }
        serialized = tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
        assert serialized["_meta"]["safetyClass"] == safety_class
        assert "safetyClass" not in serialized.get("annotations", {})


def test_conflicting_safety_metadata_is_rejected() -> None:
    mcp = SafetyAwareFastMCP("test")

    with pytest.raises(ValueError, match="Conflicting safetyClass values"):

        @mcp.tool(
            annotations={"safetyClass": "read-only"},
            meta={"safetyClass": "write-safe"},
        )
        def conflicting_tool() -> None:
            pass


def test_per_tool_safety_classification_rejects_swapped_classes() -> None:
    expected = {"read_tool": "read-only", "write_tool": "write-safe"}
    runtime = {"read_tool": "write-safe", "write_tool": "read-only"}

    mismatches = _classification_mismatches(expected, runtime)

    assert mismatches == [
        "read_tool: expected 'read-only', runtime 'write-safe'",
        "write_tool: expected 'write-safe', runtime 'read-only'",
    ]


def test_per_tool_safety_classification_rejects_missing_runtime_tool() -> None:
    assert _classification_mismatches({"one": "read-only"}, {}) == [
        "one: missing runtime tool (expected 'read-only')"
    ]


def test_per_tool_safety_classification_rejects_unexpected_runtime_tool() -> None:
    assert _classification_mismatches({}, {"one": "read-only"}) == [
        "one: unexpected runtime tool ('read-only')"
    ]


def test_per_tool_safety_classification_rejects_invalid_class() -> None:
    assert _classification_mismatches(
        {"one": "read-only"}, {"one": "not-a-safety-class"}
    ) == ["one: expected 'read-only', runtime 'not-a-safety-class'"]


def test_missing_meta_safety_class_is_rejected() -> None:
    missing = SimpleNamespace(name="one", to_mcp_tool=lambda: SimpleNamespace(meta=None))
    assert _runtime_safety_by_name([missing]) == {"one": "unannotated"}
    assert _classification_mismatches({"one": "read-only"}, {"one": "unannotated"})


def test_dynamic_kb_tools_reject_incorrect_runtime_class() -> None:
    runtime = dict(EXPECTED_DYNAMIC_SAFETY_CLASSES)
    runtime["kb_record_finding"] = "read-only"

    assert _classification_mismatches(EXPECTED_DYNAMIC_SAFETY_CLASSES, runtime) == [
        "kb_record_finding: expected 'external-write', runtime 'read-only'"
    ]


def test_dynamic_kb_tools_have_explicit_expected_classes() -> None:
    assert EXPECTED_DYNAMIC_SAFETY_CLASSES == {
        "kb_get": "read-only",
        "kb_get_conversion": "read-only",
        "kb_get_parameter_spec": "read-only",
        "kb_list_open_questions": "read-only",
        "kb_record_finding": "external-write",
        "kb_record_verified_finding": "external-write",
        "kb_search": "read-only",
    }
