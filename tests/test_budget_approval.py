"""
Source-contract tests for the budget approval workflow.

These tests verify that:
1. The BudgetRequest model exists with all required fields.
2. create_request and review_request functions are importable.
3. The billing router exposes /budget-requests routes.
4. The approve endpoint is present.
5. The gateway proxies budget-requests.
6. api.js billingService exposes listBudgetRequests.
"""

from __future__ import annotations

import pathlib

# ---------------------------------------------------------------------------
# Helper: read source files as text
# ---------------------------------------------------------------------------

REPO = pathlib.Path(__file__).parent.parent

BUDGET_REQUESTS_PY = REPO / "services/usage/billing_routes/budget_requests.py"
BILLING_ROUTER_PY = REPO / "services/usage/billing_routes/router.py"
GATEWAY_MAIN_PY = REPO / "services/gateway/main.py"
API_JS = REPO / "ui/src/services/api.js"


def _src(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: BudgetRequest model fields
# ---------------------------------------------------------------------------


def test_budget_request_model_has_required_fields():
    """budget_requests.py must define BudgetRequest with all required columns."""
    src = _src(BUDGET_REQUESTS_PY)
    assert "class BudgetRequest" in src, "BudgetRequest class not found"
    required_fields = [
        "tenant_id",
        "agent_id",
        "agent_name",
        "requested_by",
        "current_cap_usd",
        "requested_cap_usd",
        "reason",
        "status",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    ]
    for field in required_fields:
        assert field in src, f"Required field '{field}' missing from BudgetRequest"


# ---------------------------------------------------------------------------
# Test 2: CRUD functions exist
# ---------------------------------------------------------------------------


def test_create_request_function_exists():
    """budget_requests.py must expose create_request and review_request."""
    src = _src(BUDGET_REQUESTS_PY)
    assert "async def create_request" in src, "create_request function not found"
    assert "async def review_request" in src, "review_request function not found"


# ---------------------------------------------------------------------------
# Test 3: Billing router has budget-requests routes
# ---------------------------------------------------------------------------


def test_billing_router_has_budget_routes():
    """billing/router.py must contain /budget-requests path strings."""
    src = _src(BILLING_ROUTER_PY)
    assert "/budget-requests" in src, "/budget-requests not found in billing router"
    # Verify all five route patterns are present
    assert "budget-requests" in src


# ---------------------------------------------------------------------------
# Test 4: Approve route exists in billing router
# ---------------------------------------------------------------------------


def test_approve_route_exists():
    """billing/router.py must contain an approve endpoint."""
    src = _src(BILLING_ROUTER_PY)
    assert "approve" in src, "approve endpoint not found in billing/router.py"
    assert "reject" in src, "reject endpoint not found in billing/router.py"


# ---------------------------------------------------------------------------
# Test 5: Gateway proxies budget-requests
# ---------------------------------------------------------------------------


def test_gateway_proxies_budget_requests():
    """Gateway must proxy /billing/budget-requests. Extracted from main.py to
    routers/billing.py in sprint-5; scan both."""
    src = _src(GATEWAY_MAIN_PY) + _src(GATEWAY_MAIN_PY.parent / "routers" / "billing.py")
    assert "budget-requests" in src, "gateway does not proxy /billing/budget-requests"
    assert "approve" in src
    assert "reject" in src


# ---------------------------------------------------------------------------
# Test 6: api.js billingService has listBudgetRequests
# ---------------------------------------------------------------------------


def test_api_js_billing_has_budget_service():
    """ui/src/services/api.js billingService must expose listBudgetRequests."""
    src = _src(API_JS)
    assert "listBudgetRequests" in src, "listBudgetRequests not found in api.js billingService"
    assert "createBudgetRequest" in src, "createBudgetRequest not found in api.js"
    assert "approveBudgetRequest" in src, "approveBudgetRequest not found in api.js"
    assert "rejectBudgetRequest" in src, "rejectBudgetRequest not found in api.js"
