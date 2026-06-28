"""developer_agent — shapes the Claude Agent SDK run into yields + lifecycle.

The agent runs natively (framework: claude_agent). This module is the
post-run shaper: it maps the SDK result into yields, moves the ticket to
"In Review" in Jira and links the PR (deterministic lifecycle write), and
reports a RICH outcome (success | tests_failing | blocked_needs_info) so
the mesh routes a stuck build to a human instead of to review. Dev stub
keeps the chain runnable without the SDK/repo.
"""
from __future__ import annotations
from typing import Any
import re

from leafmesh import LeafMeshLogger
from agency._shared import store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "pr_url": "", "branch": "", "files_changed": 0, "tests_passed": False,
    "summary": "", "ticket_id": "", "dev_status": "",
}


def _pick(input_data: Any, key: str, default: Any = "") -> Any:
    if isinstance(input_data, dict):
        if input_data.get(key) not in (None, ""):
            return input_data[key]
        uy = input_data.get("upstream_yields")
        if isinstance(uy, dict):
            if uy.get(key) not in (None, ""):
                return uy[key]
            for v in uy.values():
                if isinstance(v, dict) and v.get(key) not in (None, ""):
                    return v[key]
    return default


def _finish(out: dict[str, Any]) -> dict[str, Any]:
    """Deterministic lifecycle write after the run."""
    key = _pick_key(out)
    if out.get("pr_url") and key:
        store.set_status(key, "In Review")
        store.link_pr(key, out["pr_url"])
    return out


def _pick_key(out: dict[str, Any]) -> str:
    t = store.get_ticket(out.get("ticket_id", "")) if out.get("ticket_id") else {}
    return t.get("key", "")


async def developer_agent(connector_result: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS, "ticket_id": str(_pick(input_data, "ticket_id") or "")}

    if isinstance(connector_result, dict) and connector_result:
        text = str(connector_result.get("result") or connector_result.get("text") or "")
        low = text.lower()
        m = re.search(r"https://github\.com/\S+/pull/\d+", text)
        out["pr_url"] = (m.group(0) if m else connector_result.get("pr_url", "")) or ""
        out["summary"] = connector_result.get("summary") or text[:300]
        out["branch"] = connector_result.get("branch", "")
        out["tests_passed"] = bool(connector_result.get("tests_passed", "test" in low and "fail" not in low))
        try:
            out["files_changed"] = int(connector_result.get("files_changed", 0) or 0)
        except (TypeError, ValueError):
            out["files_changed"] = 0
        # Rich outcome
        if not out["pr_url"]:
            out["dev_status"] = "blocked_needs_info" if ("blocked" in low or "clarif" in low) else "tests_failing"
        else:
            out["dev_status"] = "success" if out["tests_passed"] else "tests_failing"
        logger.info(f"[developer] dev_status={out['dev_status']} pr={out['pr_url'] or '(none)'}")
        return _finish(out)

    # Dev stub
    key = out["ticket_id"] or "DEV"
    out.update({"pr_url": "https://github.com/your-org/your-repo/pull/0",
                "branch": f"feature/{key}", "files_changed": 0,
                "tests_passed": True, "dev_status": "success",
                "summary": f"DEV STUB — would implement ticket {key} via the Claude Agent SDK."})
    logger.info("[developer] DEV STUB — placeholder PR (wire DEV_REPO_PATH + claude-agent-sdk)")
    return _finish(out)
