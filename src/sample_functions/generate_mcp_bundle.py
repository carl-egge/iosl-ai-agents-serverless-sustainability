#!/usr/bin/env python3
"""
Generate a single-file bundle for one sample function so it can be deployed by MCP.

Usage:
  python src/sample_functions/generate_mcp_bundle.py <function_key> [--deadline ISO8601] [--memory-mb N]

  Ideally pipe the output to a file, e.g.:
    python src/sample_functions/generate_mcp_bundle.py hello_world > hello_world_mcp_payload.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

DEFAULT_DEADLINE = "2026-01-05T18:00:00Z"
DEFAULT_MEMORY_MB = 256


def _find_repo_root(start: Path) -> Path:
    """Walk upward to find the repo root containing src/sample_functions/main.py."""
    cur = start.resolve()
    for _ in range(8):
        candidate = cur / "src" / "sample_functions" / "main.py"
        if candidate.exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise FileNotFoundError("Could not locate src/sample_functions/main.py from script location.")


def _parse_lazy_loader_map(tree: ast.AST) -> dict[str, str]:
    """Map variable name -> module name from _lazy_loader(...) assignments."""
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            call = node.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_lazy_loader":
                if len(call.args) >= 2 and all(isinstance(arg, ast.Constant) for arg in call.args[:2]):
                    mod = call.args[0].value
                    if isinstance(mod, str):
                        mapping[target] = mod
    return mapping


def _parse_function_registry(tree: ast.AST) -> dict[str, str]:
    """Map function_key -> variable name from FUNCTION_REGISTRY dict."""
    registry: dict[str, str] = {}

    def _consume_dict(dict_node: ast.Dict) -> None:
        for key_node, val_node in zip(dict_node.keys, dict_node.values):
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                if isinstance(val_node, ast.Name):
                    registry[key_node.value] = val_node.id

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "FUNCTION_REGISTRY" for t in node.targets):
                if isinstance(node.value, ast.Dict):
                    _consume_dict(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "FUNCTION_REGISTRY":
                if isinstance(node.value, ast.Dict):
                    _consume_dict(node.value)

    return registry


def _exit_with_error(message: str, valid_keys: list[str]) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    if valid_keys:
        sys.stderr.write("Valid function keys:\n")
        for key in sorted(valid_keys):
            sys.stderr.write(f"  - {key}\n")
    raise SystemExit(1)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a single-file MCP function bundle as a JSON payload."
    )
    parser.add_argument("function_key", help="Key from FUNCTION_REGISTRY in main.py")
    parser.add_argument(
        "--deadline",
        help="ISO8601 deadline for MCP submission",
    )
    parser.add_argument(
        "--memory-mb",
        type=int,
        help="Override memory in MB for MCP submission",
    )
    return parser.parse_args(argv)


def _load_default_memory_mb(repo_root: Path, function_key: str) -> int | None:
    metadata_path = repo_root / "local_bucket" / "function_metadata.json"
    if not metadata_path.exists():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    functions = data.get("functions")
    if isinstance(functions, dict):
        entry = functions.get(function_key)
        if isinstance(entry, dict):
            memory_mb = entry.get("memory_mb")
            if isinstance(memory_mb, (int, float)):
                return int(memory_mb)
    return None


def _strip_shebang(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("#!"):
        return "\n".join(lines[1:])
    return text


def _strip_future_import(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.strip() == "from __future__ import annotations":
            continue
        lines.append(line)
    return "\n".join(lines)


def _strip_dunder_main(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skip = False
    indent_level: int | None = None

    def is_dunder_main(line: str) -> bool:
        stripped = line.strip()
        return stripped in ("if __name__ == \"__main__\":", "if __name__ == '__main__':")

    for line in lines:
        if not skip and is_dunder_main(line):
            skip = True
            indent_level = len(line) - len(line.lstrip(" "))
            continue
        if skip:
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent_level is not None and indent <= indent_level:
                skip = False
                indent_level = None
                out.append(line)
            else:
                continue
        else:
            out.append(line)
    return "\n".join(out)


def _extract_wrapper_sources(main_text: str) -> str:
    """
    Extract the metrics wrapper pieces from main.py so bundles stay in sync.
    """
    tree = ast.parse(main_text)
    needed_assigns = {"FunctionCallable", "_PROCESS_START_UNIX", "_FIRST_INVOKE"}
    needed_funcs = {
        "_safe_get_max_rss_kb",
        "_estimate_request_bytes",
        "_normalize_handler_return",
        "_sanitize_response_json_for_logs",
        "_emit_metrics_log",
        "_with_metrics",
    }

    assigns: dict[str, str] = {}
    funcs: dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in needed_assigns:
                    segment = ast.get_source_segment(main_text, node)
                    if segment:
                        assigns[target.id] = segment
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in needed_assigns:
                segment = ast.get_source_segment(main_text, node)
                if segment:
                    assigns[node.target.id] = segment
        elif isinstance(node, ast.FunctionDef) and node.name in needed_funcs:
            segment = ast.get_source_segment(main_text, node)
            if segment:
                funcs[node.name] = segment

    missing = (needed_assigns - set(assigns)) | (needed_funcs - set(funcs))
    if missing:
        raise RuntimeError(f"Missing wrapper definitions in main.py: {sorted(missing)}")

    ordered = [
        assigns["FunctionCallable"],
        assigns["_PROCESS_START_UNIX"],
        assigns["_FIRST_INVOKE"],
        funcs["_safe_get_max_rss_kb"],
        funcs["_estimate_request_bytes"],
        funcs["_normalize_handler_return"],
        funcs["_sanitize_response_json_for_logs"],
        funcs["_emit_metrics_log"],
        funcs["_with_metrics"],
    ]
    return "\n\n".join(ordered) + "\n"


def main() -> int:
    args = _parse_args(sys.argv[1:])
    function_key = args.function_key.strip()
    if not function_key:
        _exit_with_error("Function key is empty.", [])

    script_dir = Path(__file__).resolve().parent
    try:
        repo_root = _find_repo_root(script_dir)
    except FileNotFoundError as exc:
        _exit_with_error(str(exc), [])

    main_path = repo_root / "src" / "sample_functions" / "main.py"
    main_text = main_path.read_text(encoding="utf-8")
    tree = ast.parse(main_text, filename=str(main_path))

    lazy_map = _parse_lazy_loader_map(tree)
    registry = _parse_function_registry(tree)
    valid_keys = list(registry.keys())

    if function_key not in registry:
        _exit_with_error(f"Unknown function key: {function_key}", valid_keys)

    var_name = registry[function_key]
    module_name = lazy_map.get(var_name, var_name)
    module_path = repo_root / "src" / "sample_functions" / f"{module_name}.py"

    if not module_path.exists():
        _exit_with_error(
            f"Could not find module file for key '{function_key}': {module_path}",
            valid_keys,
        )

    handler_text = module_path.read_text(encoding="utf-8")
    handler_text = _strip_shebang(handler_text)
    handler_text = _strip_future_import(handler_text)
    handler_text = _strip_dunder_main(handler_text).rstrip() + "\n"

    try:
        wrapper_text = _extract_wrapper_sources(main_text)
    except RuntimeError as exc:
        _exit_with_error(str(exc), valid_keys)

    bundle_text = "\n".join(
        [
            "#!/usr/bin/env python3",
            f'"""Autogenerated MCP bundle for {function_key} (includes metrics wrapper)."""',
            "from __future__ import annotations",
            "",
            "import json",
            "import os",
            "import time",
            "from typing import Any, Callable, Dict, Optional, Tuple",
            "",
            "# --- Metrics wrapper (from main.py) ---",
            wrapper_text.rstrip(),
            "",
            "# --- Handler module code ---",
            handler_text.rstrip(),
            "",
            "# --- Wrapped entrypoints ---",
            f"_raw_handler = {function_key}",
            f"{function_key} = _with_metrics(\"{function_key}\", _raw_handler)",
            "",
            "def main(request):",
            f"    return {function_key}(request)",
            "",
        ]
    ).rstrip() + "\n"

    # Output location: src/sample_functions/mcp_bundle_<function_key>.py
    output_dir = script_dir if script_dir.name == "sample_functions" else repo_root / "src" / "sample_functions"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"mcp_bundle_{function_key}.py"
    output_path.write_text(bundle_text, encoding="utf-8")

    deadline = args.deadline or DEFAULT_DEADLINE
    memory_mb = args.memory_mb
    if memory_mb is None:
        memory_mb = _load_default_memory_mb(repo_root, function_key) or DEFAULT_MEMORY_MB

    payload = {
        "code": bundle_text,
        "deadline": deadline,
        "memory_mb": memory_mb,
    }

    # Print MCP submission payload JSON to stdout.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
