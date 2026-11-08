"""Static rules: no role-name authorization (AC-AUTHZ-01) and a domain-neutral core."""

import ast
import re
from pathlib import Path

import pytest

from app.authz.catalogue import get_catalogue, load_permission_sets

BACKEND = Path(__file__).parents[1]
REPO = BACKEND.parent
PYTHON_ROOTS = [BACKEND / "app", BACKEND / "packs"]
FRONTEND_SRC = REPO / "frontend" / "src"

SET_NAMES = {s.key for s in load_permission_sets(get_catalogue())}
ROLE_IDENTIFIER = re.compile(r"(^|_)(is_admin|is_owner|has_role|require_role|check_role)($|_)")


def python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def docstring_nodes(tree: ast.AST) -> set[ast.AST]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(body[0].value)
    return found


def violations_in(source: str) -> list[str]:
    tree = ast.parse(source)
    docstrings = docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node not in docstrings and node.value in SET_NAMES:
                found.append(f"set name {node.value!r} at line {node.lineno}")
        name = getattr(node, "name", None) or getattr(node, "id", None)
        if isinstance(name, str) and ROLE_IDENTIFIER.search(name):
            found.append(f"role-style identifier {name!r} at line {node.lineno}")
    return found


def test_scanner_detects_violations():
    assert violations_in("if s == 'business_admin': pass")
    assert violations_in("def is_owner(u): pass")
    assert not violations_in('"""business_admin in a docstring is fine"""')
    assert not violations_in("approver_user_id = 1")


@pytest.mark.parametrize("root", PYTHON_ROOTS, ids=lambda p: p.name)
def test_no_set_names_in_python(root):
    problems = {
        str(path.relative_to(BACKEND)): found
        for path in python_files(root)
        if (found := violations_in(path.read_text(encoding="utf-8")))
    }
    assert problems == {}


def test_no_set_names_in_frontend():
    names = "|".join(sorted(SET_NAMES))
    pattern = re.compile(rf"""['"`]({names})['"`]""")
    files = [*FRONTEND_SRC.rglob("*.ts"), *FRONTEND_SRC.rglob("*.tsx")]
    assert files, "frontend sources not found"
    assert [str(f) for f in files if pattern.search(f.read_text(encoding="utf-8"))] == []


def test_core_never_imports_a_domain_pack():
    offenders = []
    for path in python_files(BACKEND / "app"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(module == "packs" or module.startswith("packs.") for module in modules):
                offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == []
