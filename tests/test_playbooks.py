"""
Source-contract tests for the Day 13-14 Playbooks Engine.
No running server required — tests read file contents and import modules.
"""
from __future__ import annotations

import ast
import importlib.util
import os

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel_path: str) -> str:
    with open(os.path.join(_REPO_ROOT, rel_path)) as f:
        return f.read()


def _load_module(rel_path: str, module_name: str):
    """Load a Python source file as a module without executing its imports."""
    full_path = os.path.join(_REPO_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    assert spec is not None, f"Could not create spec for {rel_path}"
    return spec  # return spec so callers can decide what to do


# ---------------------------------------------------------------------------
# 1. Playbook model has required fields
# ---------------------------------------------------------------------------

def test_playbook_model_has_required_fields():
    """playbooks.py defines a Playbook class with name, steps, trigger_conditions."""
    source = _read("services/autonomy/playbooks.py")
    tree = ast.parse(source)

    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "Playbook" in class_names, "Playbook class missing from playbooks.py"

    # Check that the Playbook class body references required field names
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "Playbook":
            class_src = ast.unparse(cls)
            assert "name" in class_src, "Playbook missing 'name' field"
            assert "steps" in class_src, "Playbook missing 'steps' field"
            assert "trigger_conditions" in class_src, "Playbook missing 'trigger_conditions' field"
            break


# ---------------------------------------------------------------------------
# 2. execute_playbook async function exists
# ---------------------------------------------------------------------------

def test_execute_playbook_function_exists():
    """playbooks.py exports an async function named execute_playbook."""
    source = _read("services/autonomy/playbooks.py")
    tree = ast.parse(source)

    async_fns = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert "execute_playbook" in async_fns, (
        "execute_playbook async function not found in playbooks.py"
    )


# ---------------------------------------------------------------------------
# 3. get_playbook_templates() returns exactly 4 items
# ---------------------------------------------------------------------------

def test_templates_returns_four_items():
    """get_playbook_templates() returns a list of exactly 4 template dicts."""
    # We parse and eval only the get_playbook_templates function body to avoid
    # importing heavy dependencies. Use ast to extract and exec the function.
    source = _read("services/autonomy/playbooks.py")
    tree = ast.parse(source)

    fn_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_playbook_templates":
            fn_node = node
            break

    assert fn_node is not None, "get_playbook_templates not found in playbooks.py"

    # Compile and exec the standalone function with module-level constants in scope.
    # Extract all module-level string assignments (ACTION_* / MODE_* / STATUS_* constants)
    # so the isolated function execution doesn't hit NameError.
    const_nodes = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and all(isinstance(t, ast.Name) for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    module = ast.Module(body=const_nodes + [fn_node], type_ignores=[])
    code = compile(module, "<test>", "exec")
    ns: dict = {}
    exec(code, ns)  # noqa: S102

    result = ns["get_playbook_templates"]()
    assert isinstance(result, list), "get_playbook_templates must return a list"
    assert len(result) == 4, (
        f"Expected 4 playbook templates, got {len(result)}"
    )
    for item in result:
        assert "name" in item, f"Template missing 'name': {item}"
        assert "steps" in item, f"Template missing 'steps': {item}"
        assert "trigger_conditions" in item, f"Template missing 'trigger_conditions': {item}"


# ---------------------------------------------------------------------------
# 4. Router contains GET/POST /playbooks and GET/PATCH/DELETE /playbooks/{id}
# ---------------------------------------------------------------------------

def test_playbooks_router_has_crud():
    """router.py contains the required CRUD route decorators for /playbooks."""
    source = _read("services/autonomy/router.py")

    assert '"/playbooks"' in source or "'/playbooks'" in source, (
        "router.py missing /playbooks route"
    )
    assert '"/playbooks/{playbook_id}"' in source or "'/playbooks/{playbook_id}'" in source, (
        "router.py missing /playbooks/{playbook_id} route"
    )

    # Verify HTTP methods are present
    assert "@router.get" in source, "router.py missing @router.get"
    assert "@router.post" in source, "router.py missing @router.post"
    assert "@router.patch" in source, "router.py missing @router.patch"
    assert "@router.delete" in source, "router.py missing @router.delete"


# ---------------------------------------------------------------------------
# 5. Trigger route exists in router.py
# ---------------------------------------------------------------------------

def test_trigger_route_exists():
    """router.py contains a /trigger route under /playbooks/{...}."""
    source = _read("services/autonomy/router.py")
    assert "/playbooks/{" in source, "router.py missing parameterised /playbooks/{...} routes"
    assert "trigger" in source, "router.py missing 'trigger' in playbook routes"


# ---------------------------------------------------------------------------
# 6. Runs route exists in router.py
# ---------------------------------------------------------------------------

def test_runs_route_exists():
    """router.py contains a /runs route for listing playbook runs."""
    source = _read("services/autonomy/router.py")
    assert "/runs" in source, "router.py missing /runs route for playbook runs"


# ---------------------------------------------------------------------------
# 7. Gateway proxies /playbooks
# ---------------------------------------------------------------------------

def test_gateway_proxies_playbooks():
    """gateway/main.py contains /playbooks proxy routes."""
    source = _read("services/gateway/main.py")
    assert "/playbooks" in source, (
        "gateway/main.py does not contain /playbooks proxy routes"
    )
    # Verify both list and parameterised forms
    assert '"/playbooks"' in source or "'/playbooks'" in source, (
        "gateway/main.py missing /playbooks endpoint"
    )
    assert "/playbooks/{pid}" in source or "/playbooks/{" in source, (
        "gateway/main.py missing parameterised /playbooks/{...} endpoint"
    )


# ---------------------------------------------------------------------------
# 8. api.js exports playbookService
# ---------------------------------------------------------------------------

def test_api_js_has_playbook_service():
    """ui/src/services/api.js exports a playbookService object."""
    source = _read("ui/src/services/api.js")
    assert "playbookService" in source, (
        "ui/src/services/api.js does not export playbookService"
    )
    # Verify key methods exist
    assert "getTemplates" in source, "playbookService missing getTemplates method"
    assert "trigger" in source, "playbookService missing trigger method"
    assert "getRuns" in source, "playbookService missing getRuns method"
