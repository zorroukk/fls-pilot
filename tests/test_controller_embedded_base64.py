from __future__ import annotations

import ast
import base64
import json
import tokenize
from collections import Counter
from pathlib import Path

import pytest

from fl_studio_mcp import protocol

CONTROLLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "fl_controller"
    / "FLStudioMCP"
    / "device_FLStudioMCP.py"
)


def _controller_source() -> str:
    return CONTROLLER_PATH.read_text(encoding="utf-8")


def _load_controller_codec():
    tree = ast.parse(_controller_source(), filename=str(CONTROLLER_PATH))
    codec_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_b64encode", "_b64decode"}
    ]
    assert {node.name for node in codec_nodes} == {"_b64encode", "_b64decode"}

    namespace: dict[str, object] = {}
    exec(compile(ast.Module(codec_nodes, type_ignores=[]), str(CONTROLLER_PATH), "exec"), namespace)
    return namespace["_b64encode"], namespace["_b64decode"]


def _load_controller_json_codec():
    tree = ast.parse(_controller_source(), filename=str(CONTROLLER_PATH))
    names = {"_json_escape_string", "_json_dumps", "_JSONParser", "_json_loads"}
    codec_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {node.name for node in codec_nodes} == names

    namespace: dict[str, object] = {}
    exec(compile(ast.Module(codec_nodes, type_ignores=[]), str(CONTROLLER_PATH), "exec"), namespace)
    return namespace["_json_dumps"], namespace["_json_loads"]


def _load_controller_protocol_codec():
    tree = ast.parse(_controller_source(), filename=str(CONTROLLER_PATH))
    constants = {
        "SYSEX_MANUFACTURER",
        "SYSEX_MAGIC",
        "DIR_REQUEST",
        "DIR_RESPONSE",
        "DIR_HEARTBEAT",
        "REQUEST_ID_LEN",
        "_HEADER_LEN",
    }
    callables = {
        "_json_escape_string",
        "_json_dumps",
        "_JSONParser",
        "_json_loads",
        "_b64encode",
        "_b64decode",
        "_encode_message",
        "_decode_message",
    }
    selected = []
    for node in tree.body:
        is_callable = isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in callables
        is_constant = isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants for target in node.targets
        )
        if is_callable or is_constant:
            selected.append(node)

    namespace: dict[str, object] = {}
    exec(compile(ast.Module(selected, type_ignores=[]), str(CONTROLLER_PATH), "exec"), namespace)
    assert constants.issubset(namespace)
    assert callables.issubset(namespace)
    return namespace["_encode_message"], namespace["_decode_message"]


def _wire_message_from_json(body_json: str) -> bytes:
    header = bytes(
        [protocol.SYSEX_MANUFACTURER, *protocol.SYSEX_MAGIC, protocol.DIR_REQUEST]
    )
    return header + b"abc12345" + base64.b64encode(body_json.encode("ascii"))


def test_controller_avoids_stdlib_modules_missing_from_embedded_runtime() -> None:
    tree = ast.parse(_controller_source(), filename=str(CONTROLLER_PATH))
    imported_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_names.update(
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert imported_names.isdisjoint({"base64", "contextlib", "json"})


def test_controller_has_no_obsolete_codec_call_sites() -> None:
    tree = ast.parse(_controller_source(), filename=str(CONTROLLER_PATH))
    obsolete = {
        ("base64", "b64encode"),
        ("base64", "b64decode"),
        ("contextlib", "suppress"),
        ("json", "dumps"),
        ("json", "loads"),
    }
    calls = {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }

    assert calls.isdisjoint(obsolete)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "eval"
        for node in ast.walk(tree)
    )

    handlers = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_HANDLERS"
            for target in node.targets
        )
    )
    assert isinstance(handlers, ast.Dict)
    handler_names = {
        key.value
        for key in handlers.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert "bridge_eval" not in handler_names


def test_controller_source_has_no_duplicate_imports_or_class_methods() -> None:
    source = _controller_source()
    tree = ast.parse(source, filename=str(CONTROLLER_PATH))
    list(tokenize.generate_tokens(iter(source.splitlines(keepends=True)).__next__))

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, alias.asname) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.extend(
                (node.module + "." + alias.name, alias.asname)
                for alias in node.names
                if node.module is not None
            )
    assert [item for item, count in Counter(imports).items() if count > 1] == []

    for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        methods = [node.name for node in class_node.body if isinstance(node, ast.FunctionDef)]
        assert [name for name, count in Counter(methods).items() if count > 1] == []


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -42,
        3.25,
        "Hermes → FL Studio\r\n\t\"quoted\"",
        [1, "two", None, False],
        {"v": 2, "command": "get_project_state", "params": {}},
        {"nested": {"tracks": [1, 2], "name": "Müsic 🎹"}},
    ],
)
def test_controller_json_codec_matches_compact_standard_json(value: object) -> None:
    dumps, loads = _load_controller_json_codec()

    encoded = dumps(value)
    assert encoded == json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    assert loads(encoded) == value


def test_controller_json_codec_rejects_malformed_input() -> None:
    _, loads = _load_controller_json_codec()

    for payload in (
        "",
        "{",
        "[1,]",
        '{"x":}',
        '{"x":1,}',
        '"unterminated',
        '"\\u12xz"',
        '"line\nfeed"',
        "true false",
        "01",
        "1.",
        "1e",
        "1e+",
    ):
        with pytest.raises(ValueError):
            loads(payload)


@pytest.mark.parametrize("number", ["-0", "1.25", "1e3", "-2E-2"])
def test_controller_json_numeric_parsing_matches_standard_json(number: str) -> None:
    _, loads = _load_controller_json_codec()

    assert loads(number) == json.loads(number)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_controller_json_rejects_non_finite_floats(value: float) -> None:
    dumps, _ = _load_controller_json_codec()

    with pytest.raises(ValueError):
        dumps(value)


def test_controller_json_rejects_numeric_overflow() -> None:
    _, loads = _load_controller_json_codec()

    with pytest.raises(ValueError):
        loads("1e309")


def test_controller_json_handles_bmp_and_surrogate_pair_escapes() -> None:
    dumps, loads = _load_controller_json_codec()
    value = "Müsic 🎹"

    assert dumps(value) == '"M\\u00fcsic \\ud83c\\udfb9"'
    assert loads('"M\\u00fcsic \\ud83c\\udfb9"') == value


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"f",
        b"fo",
        b"foo",
        b"foob",
        b"fooba",
        b"foobar",
        bytes(range(256)),
        b'{"message":"Hermes \\u2192 FL Studio","ok":true}',
    ],
)
def test_controller_codec_matches_standard_base64(raw: bytes) -> None:
    encode, decode = _load_controller_codec()

    encoded = encode(raw)
    assert encoded == base64.b64encode(raw)
    assert decode(encoded) == raw


def test_controller_codec_rejects_invalid_payloads() -> None:
    _, decode = _load_controller_codec()

    for payload in (b"A", b"AAA", b"AA=A", b"AA?=", b"====", b"AB==", b"AAB="):
        with pytest.raises(ValueError):
            decode(payload)


def test_protocol_rejects_non_finite_numbers() -> None:
    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            protocol.encode_message(protocol.DIR_REQUEST, "abc12345", {"value": value})

    for body_json in ("NaN", "Infinity", "-Infinity", "1e309"):
        assert protocol.decode_message(_wire_message_from_json(body_json)) is None


def test_protocol_decoders_reject_non_object_payloads() -> None:
    _, controller_decode = _load_controller_protocol_codec()

    for body_json in ("[]", '"text"', "null", "42"):
        message = _wire_message_from_json(body_json)
        assert controller_decode(message) is None
        assert protocol.decode_message(message) is None


def test_server_encode_controller_decode_protocol_interoperability() -> None:
    _, controller_decode = _load_controller_protocol_codec()
    payload = {
        "v": 2,
        "command": "get_project_state",
        "params": {"name": "Müsic 🎹", "nested": [True, None, -2.5e3]},
    }

    server_message = protocol.encode_message(protocol.DIR_REQUEST, "abc12345", payload)

    assert controller_decode(server_message) == (protocol.DIR_REQUEST, "abc12345", payload)


def test_controller_encode_server_decode_protocol_interoperability() -> None:
    controller_encode, _ = _load_controller_protocol_codec()
    payload = {
        "v": 2,
        "ok": True,
        "result": {"title": "Müsic 🎹", "lines": ["one\r\ntwo", "\ttab"]},
    }

    controller_message = controller_encode(protocol.DIR_RESPONSE, "r3sp0nse", payload)

    assert protocol.decode_message(controller_message) == (
        protocol.DIR_RESPONSE,
        "r3sp0nse",
        payload,
    )
