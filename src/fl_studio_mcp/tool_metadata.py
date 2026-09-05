"""FastMCP registration helpers for FL Studio safety metadata."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

SAFETY_CLASS_KEY = "safetyClass"


class SafetyAwareFastMCP(FastMCP):
    """Move the project's safety shorthand into MCP's extensible ``_meta``.

    MCP ``ToolAnnotations`` accepts only the standard hint fields and silently
    discards unknown keys. Tool ``_meta`` is the protocol-supported extension
    point for project-specific metadata.
    """

    def tool(self, name_or_fn: Any = None, **kwargs: Any) -> Any:
        annotations = kwargs.get("annotations")
        if not isinstance(annotations, dict) or SAFETY_CLASS_KEY not in annotations:
            return super().tool(name_or_fn, **kwargs)

        standard_annotations = dict(annotations)
        safety_class = standard_annotations.pop(SAFETY_CLASS_KEY)

        meta = dict(kwargs.get("meta") or {})
        existing = meta.get(SAFETY_CLASS_KEY)
        if existing is not None and existing != safety_class:
            raise ValueError(
                "Conflicting safetyClass values in annotations and tool metadata: "
                f"{safety_class!r} != {existing!r}"
            )
        meta[SAFETY_CLASS_KEY] = safety_class

        kwargs["annotations"] = standard_annotations
        kwargs["meta"] = meta
        return super().tool(name_or_fn, **kwargs)
