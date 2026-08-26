#!/usr/bin/env python3
"""Produce a deterministic, static baseline inventory for the Wootify refactor.

The inventory intentionally uses the Python/JavaScript syntax trees instead of
importing the application.  That keeps it safe to run without credentials,
database access, network access, or a fully configured virtual environment.

Usage::

    python scripts/inventory_baseline.py --write
    python scripts/inventory_baseline.py --format markdown

``--write`` writes ``docs/refactor/baseline-inventory.json`` and the matching
Markdown summary.  The output is a structural baseline, not a runtime API
contract; the refactor verification suite should compare these records with a
post-refactor inventory.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "1"
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "websocket"}
PYTHON_IGNORED_PARTS = {"__pycache__", ".venv", ".deploy_venv", ".venv_corrupted"}


def _text(node: ast.AST | None) -> str | None:
    """Return a compact source representation for an AST node."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _literal(node: ast.AST | None) -> Any:
    """Return a literal value when a node is statically evaluable."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def _dotted(node: ast.AST | None) -> str:
    """Render a dotted AST name, or an empty string for dynamic expressions."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _module_name(path: Path, source_root: Path, package: str) -> str:
    rel = path.relative_to(source_root).with_suffix("")
    bits = list(rel.parts)
    # ``source_root`` is the src/repository root so that the reported file
    # remains useful.  Strip the package directory before prepending its
    # import name (otherwise ``wootify.wootify.config`` would be reported).
    if bits and bits[0] == package:
        bits.pop(0)
    if bits and bits[-1] == "__init__":
        bits.pop()
    return ".".join([package, *bits]) if bits else package


def _source_files(root: Path, source_root: Path) -> list[Path]:
    if not source_root.exists():
        return []
    files = []
    for path in source_root.rglob("*.py"):
        if any(part in PYTHON_IGNORED_PARTS for part in path.parts):
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.as_posix())


def _decorator_call(node: ast.AST) -> tuple[str, list[ast.expr], list[ast.keyword], str] | None:
    if not isinstance(node, ast.Call):
        return None
    name = _dotted(node.func).split(".")[-1]
    if name not in ROUTE_METHODS:
        return None
    owner = _dotted(node.func).rsplit(".", 1)[0] if "." in _dotted(node.func) else ""
    return name, list(node.args), list(node.keywords), owner


def _decorator_text(node: ast.AST) -> str:
    return _text(node) or "<dynamic>"


def _class_bases(node: ast.ClassDef) -> list[str]:
    return [_dotted(base) or (_text(base) or "<dynamic>") for base in node.bases]


def _is_settings_class(node: ast.ClassDef) -> bool:
    return any("BaseSettings" in base for base in _class_bases(node))


def _is_orm_class(node: ast.ClassDef) -> bool:
    if any(name == "Base" or name.endswith(".Base") for name in _class_bases(node)):
        return True
    return any(
        isinstance(item, (ast.Assign, ast.AnnAssign))
        and any(getattr(target, "id", None) == "__tablename__" for target in _targets(item))
        for item in node.body
    )


def _targets(node: ast.Assign | ast.AnnAssign) -> Iterable[ast.expr]:
    if isinstance(node, ast.Assign):
        return node.targets
    return (node.target,)


def _field_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    for target in _targets(node):
        if isinstance(target, ast.Name):
            return target.id
    return None


def _call_name(node: ast.AST | None) -> str:
    if not isinstance(node, ast.Call):
        return ""
    return _dotted(node.func)


def _column_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    """Extract a declared SQLAlchemy column name, including explicit aliases."""
    value = node.value
    if not isinstance(value, ast.Call):
        return None
    call_name = _call_name(value)
    if not (call_name.endswith("Column") or call_name.endswith("mapped_column")):
        return None
    if value.args and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str):
        return value.args[0].value
    return _field_name(node)


def _python_inventory(root: Path) -> dict[str, Any]:
    candidates: list[tuple[Path, Path, str]] = []
    backend_src = root / "backend" / "src"
    if (backend_src / "wootify").exists():
        candidates.append((backend_src, backend_src / "wootify", "wootify"))
    elif (root / "app").exists():
        candidates.append((root, root / "app", "app"))
    package_src = root / "packages" / "bale-pv-client" / "src"
    if not package_src.exists():
        package_src = root / "bale_pv_connector" / "src"
    if package_src.exists():
        candidates.append((package_src, package_src / "bale_pv_connector", "bale_pv_connector"))

    modules: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    settings: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    platform_signals: dict[str, dict[str, Any]] = {}

    for source_root, package_root, package in candidates:
        for path in _source_files(root, package_root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                modules.append({"module": _module_name(path, source_root, package), "file": path.relative_to(root).as_posix(), "parse_error": str(exc)})
                continue
            rel = path.relative_to(root).as_posix()
            module_entry: dict[str, Any] = {
                "module": _module_name(path, source_root, package),
                "file": rel,
                "classes": [],
                "functions": [],
            }
            router_prefixes: dict[str, str] = {}
            for statement in tree.body:
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)) or not isinstance(statement.value, ast.Call):
                    continue
                if not _call_name(statement.value).endswith("APIRouter"):
                    continue
                name = _field_name(statement)
                prefix = next((
                    _literal(keyword.value)
                    for keyword in statement.value.keywords
                    if keyword.arg == "prefix"
                ), "")
                if name and isinstance(prefix, str):
                    router_prefixes[name] = prefix
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module_entry["functions"].append({
                        "name": node.name,
                        "line": node.lineno,
                        "async": isinstance(node, ast.AsyncFunctionDef),
                        "decorators": [_decorator_text(d) for d in node.decorator_list],
                    })
                    for decorator in node.decorator_list:
                        route_call = _decorator_call(decorator)
                        if route_call:
                            method, args, keywords, owner = route_call
                            route_path = _literal(args[0]) if args else None
                            if isinstance(route_path, str):
                                prefix = router_prefixes.get(owner, "")
                                full_path = f"{prefix.rstrip('/')}/{route_path.lstrip('/')}" if prefix else route_path
                                route = {
                                    "method": method.upper(),
                                    "path": full_path,
                                    "function": node.name,
                                    "module": module_entry["module"],
                                    "file": rel,
                                    "line": node.lineno,
                                }
                                for keyword in keywords:
                                    if keyword.arg in {"name", "summary", "operation_id"}:
                                        value = _literal(keyword.value)
                                        if value is not None:
                                            route[keyword.arg] = value
                                routes.append(route)
                elif isinstance(node, ast.ClassDef):
                    methods = []
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            methods.append({
                                "name": item.name,
                                "line": item.lineno,
                                "async": isinstance(item, ast.AsyncFunctionDef),
                                "decorators": [_decorator_text(d) for d in item.decorator_list],
                            })
                    class_entry = {
                        "name": node.name,
                        "line": node.lineno,
                        "bases": _class_bases(node),
                        "methods": methods,
                    }
                    module_entry["classes"].append(class_entry)
                    if _is_settings_class(node):
                        fields = []
                        for item in node.body:
                            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                                field = _field_name(item)
                                if field and field != "model_config":
                                    annotation = getattr(item, "annotation", None)
                                    fields.append({
                                        "name": field,
                                        "line": item.lineno,
                                        "type": _text(annotation),
                                        "default": _text(item.value),
                                    })
                        settings.append({"class": node.name, "module": module_entry["module"], "file": rel, "line": node.lineno, "fields": fields})
                    if _is_orm_class(node):
                        table_name = None
                        columns = []
                        for item in node.body:
                            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                                field = _field_name(item)
                                if field == "__tablename__":
                                    table_name = _literal(item.value)
                                column = _column_name(item)
                                if column:
                                    columns.append(column)
                        if table_name:
                            tables.append({"class": node.name, "module": module_entry["module"], "file": rel, "line": node.lineno, "table": table_name, "columns": columns})
            for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
                if not _dotted(call.func).endswith("add_api_route") or not call.args:
                    continue
                route_path = _literal(call.args[0])
                if not isinstance(route_path, str):
                    continue
                handler = _dotted(call.args[1]) if len(call.args) > 1 else ""
                methods_node = next((keyword.value for keyword in call.keywords if keyword.arg == "methods"), None)
                methods = [
                    item.value
                    for item in getattr(methods_node, "elts", [])
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ] or ["GET"]
                for method in methods:
                    routes.append({
                        "method": method.upper(),
                        "path": route_path,
                        "function": handler,
                        "module": module_entry["module"],
                        "file": rel,
                        "line": call.lineno,
                    })
            # Registry signals are deliberately broad: both seed keys and
            # connector map keys are relevant to a later relocation audit.
            if "registry" in path.name or "platform" in path.name or "connector" in path.name:
                platform_pattern = re.compile(
                    r"['\"]((?:bale|telegram|instagram)(?:_(?:enterprise|pv|pv_enterprise))?)['\"]"
                )
                for match in platform_pattern.finditer(path.read_text(encoding="utf-8")):
                    value = match.group(1)
                    signal = platform_signals.setdefault(value, {"value": value, "files": []})
                    if rel not in signal["files"]:
                        signal["files"].append(rel)
            modules.append(module_entry)

    modules.sort(key=lambda item: item["module"])
    routes.sort(key=lambda item: (item["path"], item["method"], item["module"], item["function"]))
    settings.sort(key=lambda item: (item["module"], item["class"]))
    tables.sort(key=lambda item: item["table"])
    return {
        "modules": modules,
        "routes": routes,
        "settings": settings,
        "orm_tables": tables,
        "platform_registry_signals": [platform_signals[key] for key in sorted(platform_signals)],
    }


def _frontend_inventory(root: Path) -> dict[str, Any]:
    frontend = root / "frontend"
    if not frontend.exists():
        frontend = root / "wootify-instance-manager"
    entries = []
    if frontend.exists():
        for path in sorted(frontend.rglob("*.js")) + sorted(frontend.rglob("*.jsx")):
            if "node_modules" in path.parts or "dist" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            functions = []
            pattern = re.compile(
                r"export\s+(?:(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|const\s+([A-Za-z_$][\w$]*)\s*=)"
            )
            for match in pattern.finditer(source):
                functions.append({"name": match.group(1) or match.group(2), "line": source.count("\n", 0, match.start()) + 1})
            if functions:
                entries.append({"file": path.relative_to(root).as_posix(), "functions": functions})
    return {"root": frontend.relative_to(root).as_posix() if frontend.exists() else None, "api_functions": entries}


def inventory(root: Path) -> dict[str, Any]:
    root = root.resolve()
    python = _python_inventory(root)
    frontend = _frontend_inventory(root)
    return {
        "schema_version": SCRIPT_VERSION,
        "source_layout": {
            "backend": "backend/src/wootify" if (root / "backend/src/wootify").exists() else "app" if (root / "app").exists() else None,
            "frontend": frontend["root"],
            "bale_pv": "packages/bale-pv-client/src/bale_pv_connector" if (root / "packages/bale-pv-client/src/bale_pv_connector").exists() else "bale_pv_connector/src/bale_pv_connector" if (root / "bale_pv_connector/src/bale_pv_connector").exists() else None,
        },
        "python": python,
        "frontend": frontend,
    }


def _markdown(data: dict[str, Any]) -> str:
    py = data["python"]
    lines = [
        "# Baseline inventory",
        "",
        "> Generated by `scripts/inventory_baseline.py`. This is a static structural snapshot for the behavior-preserving refactor.",
        "",
        "## Summary",
        "",
        f"- Python modules: **{len(py['modules'])}**",
        f"- Top-level routes: **{len(py['routes'])}**",
        f"- Settings classes: **{len(py['settings'])}**",
        f"- ORM tables: **{len(py['orm_tables'])}**",
        f"- Platform registry signals: **{len(py['platform_registry_signals'])}**",
        f"- Frontend files exporting API functions: **{len(data['frontend']['api_functions'])}**",
        "",
        "## Routes",
        "",
        "| Method | Path | Handler | File |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(f"| {r['method']} | `{r['path']}` | `{r['function']}` | `{r['file']}:{r['line']}` |" for r in py["routes"])
    lines.extend(["", "## Settings", "", "| Class | Module | Fields |", "| --- | --- | ---: |"])
    lines.extend(f"| `{s['class']}` | `{s['module']}` | {len(s['fields'])} |" for s in py["settings"])
    lines.extend(["", "## ORM tables", "", "| Class | Table | Columns |", "| --- | --- | ---: |"])
    lines.extend(f"| `{t['class']}` | `{t['table']}` | {len(t['columns'])} |" for t in py["orm_tables"])
    lines.extend(["", "## Platform registry signals", "", "| Value | Referenced by |", "| --- | --- |"])
    lines.extend(f"| `{s['value']}` | {', '.join(f'`{f}`' for f in s['files'])} |" for s in py["platform_registry_signals"])
    lines.extend(["", "## Frontend API functions", "", "| File | Functions |", "| --- | --- |"])
    lines.extend(f"| `{e['file']}` | {', '.join(f'`{f['name']}`' for f in e['functions'])} |" for e in data["frontend"]["api_functions"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--write", action="store_true", help="write both baseline files under docs/refactor")
    parser.add_argument("--output", type=Path, help="write the selected format to this path")
    args = parser.parse_args(argv)
    data = inventory(args.repo_root)
    if args.write and args.output:
        parser.error("--write and --output cannot be used together")
    if args.write:
        output_dir = args.repo_root / "docs" / "refactor"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "baseline-inventory.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_dir / "baseline-inventory.md").write_text(_markdown(data), encoding="utf-8")
    elif args.output:
        rendered = json.dumps(data, indent=2, sort_keys=True) + "\n" if args.format == "json" else _markdown(data)
        destination = args.output if args.output.is_absolute() else args.repo_root / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
    else:
        if args.format == "json":
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print(_markdown(data), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
