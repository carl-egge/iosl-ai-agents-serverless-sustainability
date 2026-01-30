#!/usr/bin/env python3
"""
Export MCP-style container sources for manual Cloud Run deployment.

This script mirrors the MCP server's wrapper + requirements + Dockerfile so
manual deploys match MCP runtime shape.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BASE_REQUIREMENTS = "flask>=3.0.0\ngunicorn>=21.2.0\nfunctions-framework>=3.0.0\n"
DEFAULT_ENTRY_POINT = "main"
METADATA_PATH = "local_bucket/function_metadata_with_code.json"
DOCKERFILE_TEMPLATE_FALLBACK = """FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "300", "main:app"]
"""


def _find_repo_root(start: Path) -> Path:
    """Walk upward to find repo root containing src/sample_functions/main.py."""
    cur = start.resolve()
    for _ in range(8):
        candidate = cur / "src" / "sample_functions" / "main.py"
        if candidate.exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise FileNotFoundError("Could not locate repo root from script location.")


def _sanitize_entry_point(entry_point: str) -> str:
    """Match MCP entry point sanitization rules."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", entry_point or "")
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    if not sanitized:
        sanitized = "main"
    return sanitized


def _strip_shebang(lines: list[str]) -> list[str]:
    if lines and lines[0].startswith("#!"):
        return lines[1:]
    return lines


def _split_future_imports(code: str) -> tuple[str, str]:
    future_imports: list[str] = []
    remaining: list[str] = []
    for line in code.split("\n"):
        if re.match(r"^\s*from\s+__future__\s+import\s+", line):
            future_imports.append(line)
        else:
            remaining.append(line)
    future_section = "\n".join(future_imports) + "\n" if future_imports else ""
    return future_section, "\n".join(remaining)


def _wrap_code(code: str, entry_point: str) -> str:
    entry_point = _sanitize_entry_point(entry_point)
    lines = _strip_shebang(code.splitlines())
    future_section, clean_code = _split_future_imports("\n".join(lines))
    wrapped_code = f'''{future_section}"""Auto-generated Cloud Run service wrapper."""
from flask import Flask, request, jsonify
import traceback

app = Flask(__name__)

# User's code
{clean_code}

# Store reference to user's handler
_user_handler = None
if 'handler' in dir():
    _user_handler = handler
elif 'main' in dir():
    _user_handler = main
elif 'run' in dir():
    _user_handler = run

@app.route('/', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def {entry_point}():
    """HTTP Cloud Run entry point."""
    try:
        if _user_handler is not None:
            # Call user's handler with the Flask request object
            result = _user_handler(request)
            # If result is a tuple (body, status, headers), return as-is
            if isinstance(result, tuple):
                return result
            # Otherwise jsonify the result
            return jsonify(result) if not isinstance(result, str) else result
        else:
            request_json = request.get_json(silent=True) or {{}}
            return jsonify({{"message": "No handler found", "input": request_json}})
    except Exception as e:
        return jsonify({{"error": str(e), "traceback": traceback.format_exc()}}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({{"status": "healthy"}})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
'''
    return wrapped_code


def _load_dockerfile_template(repo_root: Path) -> str:
    template_path = repo_root / "src" / "mcp_server" / "templates" / "function.Dockerfile"
    try:
        return template_path.read_text(encoding="utf-8")
    except Exception:
        return DOCKERFILE_TEMPLATE_FALLBACK


def _write_requirements(path: Path, extra_requirements: str | None) -> None:
    extra = (extra_requirements or "").strip()
    if extra:
        content = BASE_REQUIREMENTS + extra + "\n"
    else:
        content = BASE_REQUIREMENTS
    path.write_text(content, encoding="utf-8")


def _export_function(out_dir: Path, function_name: str, code: str, requirements: str | None, dockerfile: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    main_py = _wrap_code(code, DEFAULT_ENTRY_POINT)
    (out_dir / "main.py").write_text(main_py, encoding="utf-8")
    _write_requirements(out_dir / "requirements.txt", requirements)
    (out_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export MCP-style container sources for manual Cloud Run deploys."
    )
    parser.add_argument(
        "--out",
        default="out/manual_deploy",
        help="Output directory for per-function bundles (default: out/manual_deploy)",
    )
    parser.add_argument(
        "--functions",
        nargs="*",
        help="Optional list of function keys to export (default: all in metadata)",
    )
    parser.add_argument(
        "--metadata",
        default=METADATA_PATH,
        help=f"Path to function metadata with code (default: {METADATA_PATH})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = _find_repo_root(Path(__file__).resolve())
    metadata_path = repo_root / args.metadata
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    functions = data.get("functions", {})
    if not isinstance(functions, dict) or not functions:
        raise SystemExit("No functions found in metadata.")

    selected = set(args.functions or functions.keys())
    dockerfile = _load_dockerfile_template(repo_root)
    out_base = repo_root / args.out

    for function_name, meta in functions.items():
        if function_name not in selected:
            continue
        if not isinstance(meta, dict):
            continue
        code = meta.get("code")
        if not code:
            raise SystemExit(f"Missing code for function '{function_name}'.")
        requirements = meta.get("requirements")
        _export_function(out_base / function_name, function_name, code, requirements, dockerfile)

    print(f"Exported {len(selected)} function(s) to {out_base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
