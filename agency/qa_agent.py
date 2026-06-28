"""qa_agent — Jira-driven QA engineer (Claude Agent SDK executor).

@pre_compose  load_test_target — assemble what to test: an inline fresh
              PR (when the developer just handed off ticket_id + pr_url),
              or, standalone, the top QA/test ticket assigned to this
              discipline pulled from Jira (dev: the store). The discipline,
              acceptance criteria, and PR branch become the SDK run prompt.
Shaper        qa_agent(connector_result, ...) — map the SDK's verdict into
              yields, FILE every defect as a Jira bug (deterministic — the
              run's job is to test, the shaper guarantees the bugs land),
              and bound the qa⇄developer rework loop.

QA never edits the repo (permission_mode: default — no Write/Edit): it
VERIFIES and reports. The bugs it files become new Jira tickets the
planner later picks up — that's how the squad closes the find→fix loop.
Discipline comes from QA_DISCIPLINE (backend | api | frontend).
"""
from __future__ import annotations
from typing import Any
import json, os, re

from leafmesh import LeafMeshLogger, pre_compose
from agency._shared import store, session_stash

logger = LeafMeshLogger(__name__)

_DISCIPLINE = (os.getenv("QA_DISCIPLINE", "backend") or "backend").strip().lower()
_MAX_REWORK_ROUNDS = 3
_DEFAULTS: dict[str, Any] = {
    "ticket_id": "", "pr_url": "", "discipline": _DISCIPLINE,
    "qa_verdict": "", "qa_escalate": False, "from_dev_flow": False,
    "bugs_found": [], "bugs_filed": [], "tests_run": 0,
}


def _pick(data: Any, key: str, default: Any = "") -> Any:
    if isinstance(data, dict):
        if data.get(key) not in (None, ""):
            return data[key]
        uy = data.get("upstream_yields")
        if isinstance(uy, dict):
            if uy.get(key) not in (None, ""):
                return uy[key]
            for v in uy.values():
                if isinstance(v, dict) and v.get(key) not in (None, ""):
                    return v[key]
    return default


def _prepared(context: Any) -> dict[str, Any]:
    if isinstance(context, dict) and isinstance(context.get("prepared_data"), dict):
        return context["prepared_data"]
    return {}


def _sid(data: Any, context: Any) -> str:
    for s in (context, data):
        if isinstance(s, dict) and s.get("session_id"):
            return str(s["session_id"])
    return ""


def load_test_target(data: Any, ctx: Any) -> dict[str, Any]:
    """Build the test brief the SDK runs against — inline PR or standalone ticket."""
    pr = str(_pick(data, "pr_url") or "")
    tid = str(_pick(data, "ticket_id") or "")
    if pr or tid:                                  # inline — verify the fresh build
        t = store.get_ticket(tid) if tid else {}
        return {"mode": "pr", "discipline": _DISCIPLINE, "pr_url": pr,
                "ticket": t, "acceptance_criteria": t.get("acceptance_criteria", []),
                "from_dev_flow": True}
    # standalone — pick the top assigned QA/test ticket for this discipline
    queue = store.qa_assigned(discipline=_DISCIPLINE)
    pick = queue[0] if queue else {}
    if pick:
        store.qa_mark_in_flight(pick["ticket_id"])
    return {"mode": "ticket", "discipline": _DISCIPLINE, "pr_url": "",
            "ticket": pick, "acceptance_criteria": pick.get("acceptance_criteria", []),
            "from_dev_flow": False}


def _parse(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except ValueError:
            m = re.search(r"\{.*\}", resp, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
    return {}


def _file_bugs(bugs: list[Any], discipline: str, parent_key: str) -> list[str]:
    filed: list[str] = []
    for b in bugs:
        if not isinstance(b, dict):
            continue
        title = str(b.get("title") or b.get("summary") or "Defect found in QA")
        desc = "\n".join(f"{k}: {b.get(k)}" for k in ("steps", "expected", "actual") if b.get(k))
        sev = str(b.get("severity") or "major").lower()
        sev = sev if sev in ("blocker", "major", "minor") else "major"
        filed.append(store.create_bug(title, desc, sev, discipline, parent_key))
    return filed


@pre_compose(context_processor=load_test_target)
async def qa_agent(connector_result: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS}
    pd = _prepared(context)
    tk = pd.get("ticket") if isinstance(pd.get("ticket"), dict) else {}
    pr = str(_pick(input_data, "pr_url") or pd.get("pr_url") or "")
    tid = str(_pick(input_data, "ticket_id") or (tk.get("ticket_id") if tk else "") or "")
    from_dev_flow = bool(pd["from_dev_flow"]) if isinstance(pd.get("from_dev_flow"), bool) else bool(pr or tid)
    parent = (store.get_ticket(tid).get("key", "") if tid else "") or (tk.get("key", "") if tk else "")
    out.update({"ticket_id": tid, "pr_url": pr, "from_dev_flow": from_dev_flow})

    if isinstance(connector_result, dict) and connector_result:
        text = str(connector_result.get("result") or connector_result.get("text") or "")
        parsed = _parse(text)
        bugs = parsed.get("bugs")
        if not isinstance(bugs, list):
            cr_bugs = connector_result.get("bugs")
            bugs = cr_bugs if isinstance(cr_bugs, list) else []
        verdict = str(parsed.get("qa_verdict") or connector_result.get("qa_verdict") or "").lower()
        if verdict not in ("pass", "fail"):
            verdict = "fail" if bugs else "pass"
        try:
            out["tests_run"] = int(parsed.get("tests_run", connector_result.get("tests_run", 0)) or 0)
        except (TypeError, ValueError):
            out["tests_run"] = 0
        out["bugs_found"] = bugs
        out["qa_verdict"] = verdict
        out["bugs_filed"] = _file_bugs(bugs, _DISCIPLINE, parent)
    else:
        # Dev stub — no SDK/repo: pass with no bugs so the chain runs.
        out["qa_verdict"] = "pass"
        logger.info(f"[qa] DEV STUB — would run {_DISCIPLINE} test cases via the Claude Agent SDK")

    # Bound the qa⇄developer rework loop (only the inline fresh-PR path loops).
    if out["qa_verdict"] == "fail" and from_dev_flow:
        rounds = session_stash.bump(_sid(input_data, context if isinstance(context, dict) else {}),
                                    f"qa_rework:{pr or tid}")
        if rounds > _MAX_REWORK_ROUNDS:
            out["qa_escalate"] = True
            logger.warning(f"[qa] rework cap hit ({rounds}) — escalating to the tech lead")

    # Lifecycle: a failing QA on a PR sends the ticket back to In Progress;
    # a standalone test ticket frees its slot when done.
    if parent and from_dev_flow:
        store.set_status(parent, "In Progress" if out["qa_verdict"] == "fail" else "In Review")
    if not from_dev_flow and tid:
        store.qa_clear_in_flight(tid)
    logger.info(f"[qa] {_DISCIPLINE} verdict={out['qa_verdict']} "
                f"bugs={len(out['bugs_found'])} filed={out['bugs_filed'] or '-'}")
    return out
