"""Dev-mode store for the AI dev squad.

Stands in for Jira (tickets + status) and GitHub (PR diff, CI, merge) so
the planner→lead→build→review→merge loop runs before connectors are
wired. Replace each read/write with the matching call in prod; the function
signatures are the contract. The two integration points:
  * Jira   → the ticket read/write + create_bug functions
  * GitHub → get_pr_diff / get_ci_status / merge_pr
Creating the vault records 'jira_mcp' / 'github_mcp' gives the LLM agents
live tools (their `mcp:` blocks); it does not change this file. Until you
replace these bodies, tickets, diffs and CI come from here. Nothing reads an
address or token from .env.

Seed data is fictional (a mid-size online retailer's engineering team) and
covers every route: an open queue ordered by priority then age, a blocked
ticket, a needs-info ticket, a PR with green CI and one with red CI, and QA
tickets for each discipline.
"""
from __future__ import annotations
import json, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DIR = Path("./data")
SQUAD = "dev-squad-bot"

JIRA_BASE = "https://jira.example.com/browse"   # fictional; your Jira's browse URL
# est_effort is story points (Fibonacci). created_at decides "oldest first".
_SEED = [
    {"ticket_id": "t1", "key": "ENG-1201", "url": f"{JIRA_BASE}/ENG-1201",
     "title": "Add rate-limit headers to the public orders API", "priority": "high",
     "status": "open", "assignee": SQUAD, "est_effort": 3, "discipline": "api",
     "created_at": "2026-10-21T09:12:00+00:00",
     "description": "Partners hit 429s with no hint of when to retry.",
     "acceptance_criteria": [
         "GET /v2/orders returns X-RateLimit-Limit, X-RateLimit-Remaining and X-RateLimit-Reset",
         "A 429 response carries Retry-After in seconds",
         "Existing clients that ignore the headers keep working (contract tests green)"]},
    {"ticket_id": "t2", "key": "ENG-1188", "url": f"{JIRA_BASE}/ENG-1188",
     "title": "Fix flaky checkout integration test", "priority": "medium",
     "status": "open", "assignee": SQUAD, "est_effort": 2, "discipline": "backend",
     "created_at": "2026-10-14T15:40:00+00:00",
     "description": "test_checkout_applies_coupon fails about 1 run in 12 on CI.",
     "acceptance_criteria": [
         "test_checkout_applies_coupon passes 50 runs in a row on CI",
         "No sleep()-based waits remain in the checkout suite"]},
    {"ticket_id": "t3", "key": "ENG-1210", "url": f"{JIRA_BASE}/ENG-1210",
     "title": "Migrate staff sign-in to OIDC", "priority": "high",
     "status": "blocked", "assignee": SQUAD, "est_effort": 8, "discipline": "backend",
     "created_at": "2026-10-09T11:05:00+00:00",
     "description": "Blocked on the identity team issuing the OIDC client.",
     "acceptance_criteria": ["Staff sign in through the company IdP", "Local passwords disabled"]},
    {"ticket_id": "t4", "key": "ENG-1215", "url": f"{JIRA_BASE}/ENG-1215",
     "title": "Payment webhook retries create duplicate refunds", "priority": "critical",
     "status": "open", "assignee": SQUAD, "est_effort": 5, "discipline": "backend",
     "created_at": "2026-10-30T07:58:00+00:00",
     "description": "A retried refund.succeeded webhook issued a second refund twice last week.",
     "acceptance_criteria": [
         "Replaying the same refund.succeeded event twice creates exactly one refund row",
         "The second delivery returns 200 and logs 'duplicate event ignored'",
         "A unit test covers the replay"]},
    {"ticket_id": "t5", "key": "ENG-1196", "url": f"{JIRA_BASE}/ENG-1196",
     "title": "Add CSV export to the orders page", "priority": "medium",
     "status": "open", "assignee": SQUAD, "est_effort": 3, "discipline": "frontend",
     "created_at": "2026-10-17T13:20:00+00:00",
     "description": "Ops downloads orders by hand every Monday.",
     "acceptance_criteria": [
         "An Export CSV button on /orders downloads the filtered list",
         "The file has one row per order with id, date, customer, total, status",
         "Exports over 10,000 rows show a 'too large, narrow the filter' message"]},
    {"ticket_id": "t6", "key": "ENG-1219", "url": f"{JIRA_BASE}/ENG-1219",
     "title": "Speed up the admin dashboard", "priority": "low",
     "status": "needs_info", "assignee": SQUAD, "est_effort": 5, "discipline": "frontend",
     "created_at": "2026-10-31T10:02:00+00:00",
     "description": "\"It's slow.\" Waiting for the reporter to say which page and how slow.",
     "acceptance_criteria": []},
]
_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now(): return datetime.now(timezone.utc).isoformat()
def _path(k): _DIR.mkdir(parents=True, exist_ok=True); return _DIR/f"{k}.json"
def _load(k):
    p=_path(k)
    if not p.exists(): return {}
    try: return json.loads(p.read_text())
    except (ValueError, OSError): return {}
def _save(k, d):
    p=_path(k); t=p.with_suffix(".json.tmp"); t.write_text(json.dumps(d, indent=2, default=str)); t.replace(p)


def _tickets() -> dict[str, Any]:
    s = _load("tickets")
    if not s:
        s = {t["ticket_id"]: t for t in _SEED}; _save("tickets", s)
    return s


def assigned_tickets(assignee: str = SQUAD, exclude_in_flight: bool = True) -> list[dict[str, Any]]:
    """Open, actionable tickets for the squad, priority→effort. PROD: Jira search."""
    rows = [t for t in _tickets().values()
            if t.get("assignee") == assignee and t.get("status") == "open"]
    if exclude_in_flight:
        flying = set(_load("in_flight").keys())
        rows = [t for t in rows if t["ticket_id"] not in flying]
    # Priority, then OLDEST first, then smallest effort — the order the
    # planner's prompt and the PRD promise.
    rows.sort(key=lambda t: (_RANK.get(t.get("priority", "low"), 3),
                             str(t.get("created_at") or "9999"),
                             int(t.get("est_effort", 99))))
    return rows


def get_ticket(ticket_id: str) -> dict[str, Any]:
    return _tickets().get(ticket_id, {})


def open_ticket_titles(assignee: str = SQUAD) -> list[str]:
    """Titles of the open backlog — the product owner's dedupe signal."""
    return [t.get("title", "") for t in _tickets().values()
            if t.get("status") == "open" and t.get("assignee") == assignee and t.get("title")]


def create_ticket(title: str, description: str = "", priority: str = "medium",
                  acceptance_criteria: list[str] | None = None,
                  discipline: str = "backend", assignee: str = SQUAD) -> dict[str, Any]:
    """File a new ticket (product owner). Status is "triage" — invisible to
    the planner's autonomous poll until a stakeholder approves and the
    directed-pick route hands it on. PROD: Jira create-issue."""
    with _LOCK:
        s = _tickets()
        seq = _load("eng_seq") or {}
        n = int(seq.get("n", 1300)) + 1
        seq["n"] = n; _save("eng_seq", seq)
        tid, key = f"e{n}", f"ENG-{n}"
        rec = {"ticket_id": tid, "key": key, "url": f"{JIRA_BASE}/{key}",
               "title": title, "description": description, "priority": priority,
               "status": "triage", "assignee": assignee, "est_effort": 3,
               "acceptance_criteria": acceptance_criteria or [],
               "discipline": discipline, "created_at": _now()}
        s[tid] = rec; _save("tickets", s)
    return rec


# ── Jira write-back (the lifecycle) ──────────────────────────────────
def set_status(ticket_key: str, status: str) -> None:
    """PROD: Jira transition. DEV: stamp the ticket record."""
    with _LOCK:
        s = _tickets()
        for t in s.values():
            if t.get("key") == ticket_key:
                t["status"] = status; t["status_updated"] = _now()
        _save("tickets", s)


def add_comment(ticket_key: str, text: str) -> None:
    with _LOCK:
        c = _load("ticket_comments"); c.setdefault(ticket_key, []).append({"at": _now(), "text": text}); _save("ticket_comments", c)


def link_pr(ticket_key: str, pr_url: str) -> None:
    with _LOCK:
        s = _tickets()
        for t in s.values():
            if t.get("key") == ticket_key: t["pr_url"] = pr_url
        _save("tickets", s)


# ── In-flight / WIP control (event idempotency + concurrency cap) ────
def mark_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("in_flight"); f[ticket_id] = _now(); _save("in_flight", f)
def clear_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("in_flight"); f.pop(ticket_id, None); _save("in_flight", f)
def is_in_flight(ticket_id: str) -> bool:
    return ticket_id in _load("in_flight")
def in_flight_count() -> int:
    return len(_load("in_flight"))


# ── Plans / PRs / CI (GitHub side) ───────────────────────────────────
def put_plan(ticket_id: str, plan: str) -> None:
    with _LOCK:
        s=_load("plans"); s[ticket_id]={"ticket_id": ticket_id, "plan": plan, "at": _now()}; _save("plans", s)
def get_plan(ticket_id: str) -> str:
    return (_load("plans").get(ticket_id) or {}).get("plan", "")
# Two seeded PRs so the reviewer and the merge finisher have real-looking
# input on day 0: #1287 (ENG-1201, CI green) and #1291 (ENG-1188, CI red —
# drives the "CI red → back to the developer" route). The developer's dev stub
# opens pull/0, which has no record: CI reads green and the diff says so.
PR_BASE = "https://github.com/your-org/shop-platform/pull"   # placeholder repo
_SEED_PRS = [
    {"pr_url": f"{PR_BASE}/1287", "ticket_key": "ENG-1201", "branch": "feature/ENG-1201-rate-limit-headers",
     "ci_status": "green", "files_changed": 4, "merged": False,
     "diff": ("--- a/api/middleware/rate_limit.py\n+++ b/api/middleware/rate_limit.py\n"
              "@@ -41,6 +41,10 @@ def apply_limit(request, bucket):\n"
              "+    response.headers['X-RateLimit-Limit'] = str(bucket.capacity)\n"
              "+    response.headers['X-RateLimit-Remaining'] = str(bucket.tokens)\n"
              "+    response.headers['X-RateLimit-Reset'] = str(bucket.reset_epoch)\n"
              "+    # Retry-After on 429 is set in errors.py\n")},
    {"pr_url": f"{PR_BASE}/1291", "ticket_key": "ENG-1188", "branch": "fix/ENG-1188-flaky-coupon-test",
     "ci_status": "red", "files_changed": 2, "merged": False,
     "diff": ("--- a/tests/integration/test_checkout.py\n+++ b/tests/integration/test_checkout.py\n"
              "@@ -88,7 +88,7 @@ def test_checkout_applies_coupon(client, cart):\n"
              "-    time.sleep(2)\n+    wait_for(lambda: cart.coupon_applied, timeout=5)\n")},
]


def _prs() -> dict[str, Any]:
    s = _load("prs")
    if not s:
        s = {p["pr_url"]: dict(p) for p in _SEED_PRS}; _save("prs", s)
    return s


def get_pr_diff(pr_url: str) -> str:
    """PROD: GitHub pull-request diff. DEV: the seeded diff, or says there is none."""
    return _prs().get(pr_url, {}).get(
        "diff", "(dev store: no diff recorded for this PR — replace get_pr_diff with a GitHub call)")
def get_ci_status(pr_url: str) -> str:
    """PROD: GitHub checks API. DEV: the seeded status; a PR with no record reads green."""
    return _prs().get(pr_url, {}).get("ci_status", "green")
def merge_pr(pr_url: str) -> bool:
    """PROD: gh pr merge. DEV: mark merged. Merging an already-merged PR is a no-op."""
    with _LOCK:
        s=_prs(); rec=s.get(pr_url) or {"pr_url": pr_url}
        if rec.get("merged"):
            return True
        rec["merged"]=True; rec["merged_at"]=_now(); s[pr_url]=rec; _save("prs", s)
    return True
def is_merged(pr_url: str) -> bool:
    return bool(_prs().get(pr_url, {}).get("merged"))


# ── QA / test tickets + bugs (the QA engineer's lane) ────────────────
QA_BOT = "qa-bot"
_SEED_QA = [
    {"ticket_id": "qa1", "key": "QA-440", "url": f"{JIRA_BASE}/QA-440",
     "title": "Regression: payments API error paths", "priority": "high",
     "status": "open", "assignee": QA_BOT, "discipline": "api", "labels": ["qa"],
     "acceptance_criteria": ["POST /v2/payments with a missing amount returns 422",
                             "Any /v2/payments call without a token returns 401",
                             "The 101st call in a minute returns 429 with Retry-After"]},
    {"ticket_id": "qa2", "key": "QA-455", "url": f"{JIRA_BASE}/QA-455",
     "title": "Smoke: checkout flow in the web app", "priority": "medium",
     "status": "open", "assignee": QA_BOT, "discipline": "frontend", "labels": ["qa"],
     "acceptance_criteria": ["Add two items to the cart", "Apply coupon AUTUMN10 (10% off)",
                             "Pay with the test card and see the order confirmation",
                             "No errors in the browser console"]},
    {"ticket_id": "qa3", "key": "QA-460", "url": f"{JIRA_BASE}/QA-460",
     "title": "Integration: order service against the new schema", "priority": "medium",
     "status": "open", "assignee": QA_BOT, "discipline": "backend", "labels": ["qa"],
     "acceptance_criteria": ["Migrations 0141–0143 apply cleanly to a copy of staging",
                             "The order-service suite is green",
                             "Fetching an order with 50 lines runs at most 3 queries"]},
    {"ticket_id": "qa4", "key": "QA-466", "url": f"{JIRA_BASE}/QA-466",
     "title": "Regression: refund webhook replay (ENG-1215)", "priority": "critical",
     "status": "open", "assignee": QA_BOT, "discipline": "backend", "labels": ["qa"],
     "acceptance_criteria": ["Replaying refund.succeeded twice leaves one refund row",
                             "The second delivery returns 200"]},
]


def _qa_tickets() -> dict[str, Any]:
    s = _load("qa_tickets")
    if not s:
        s = {t["ticket_id"]: t for t in _SEED_QA}; _save("qa_tickets", s)
    return s


def qa_assigned(discipline: str = "", assignee: str = QA_BOT,
                exclude_in_flight: bool = True) -> list[dict[str, Any]]:
    """Open QA/test tickets for the squad, optionally filtered by discipline,
    priority-ordered. PROD: Jira search (assignee + label=qa)."""
    rows = [t for t in _qa_tickets().values()
            if t.get("assignee") == assignee and t.get("status") == "open"]
    if discipline:
        rows = [t for t in rows if (t.get("discipline") or "backend") == discipline]
    if exclude_in_flight:
        flying = set(_load("qa_in_flight").keys())
        rows = [t for t in rows if t["ticket_id"] not in flying]
    rows.sort(key=lambda t: _RANK.get(t.get("priority", "low"), 3))
    return rows


def qa_mark_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("qa_in_flight"); f[ticket_id] = _now(); _save("qa_in_flight", f)
def qa_clear_in_flight(ticket_id: str) -> None:
    with _LOCK:
        f = _load("qa_in_flight"); f.pop(ticket_id, None); _save("qa_in_flight", f)


def claim_next_qa(discipline: str = "", assignee: str = QA_BOT) -> dict[str, Any]:
    """ATOMIC backlog claim for the parallel QA testers (scaling.copies.count: N).

    Under one lock: find the top open, not-in-flight QA ticket (optionally for a
    discipline), mark it in-flight, and return it. Each concurrent instance that
    calls this gets a DISJOINT ticket (LPOP-style) — so N copies never duplicate
    work. Returns {} when the backlog is drained (that copy goes idle).
    PROD: an atomic Jira transition (assign + move to In Progress) or a queue
    `LPOP`/`UPDATE ... SKIP LOCKED RETURNING`."""
    with _LOCK:
        rows = [t for t in _qa_tickets().values()
                if t.get("assignee") == assignee and t.get("status") == "open"]
        if discipline:
            rows = [t for t in rows if (t.get("discipline") or "backend") == discipline]
        flying = set(_load("qa_in_flight").keys())
        rows = [t for t in rows if t["ticket_id"] not in flying]
        rows.sort(key=lambda t: _RANK.get(t.get("priority", "low"), 3))
        if not rows:
            return {}
        pick = rows[0]
        f = _load("qa_in_flight"); f[pick["ticket_id"]] = _now(); _save("qa_in_flight", f)
        return pick


def create_bug(summary: str, description: str = "", severity: str = "major",
               discipline: str = "backend", parent_key: str = "",
               reporter: str = "qa_agent") -> str:
    """File a bug back into Jira. DEV: persist + return a BUG-key.
    PROD: Jira create-issue (type=Bug, linked to parent_key)."""
    with _LOCK:
        seq = _load("bug_seq") or {}
        n = int(seq.get("n", 4000)) + 1
        seq["n"] = n; _save("bug_seq", seq)
        key = f"BUG-{n}"
        bugs = _load("bugs")
        bugs[key] = {"key": key, "summary": summary, "description": description,
                     "severity": severity, "discipline": discipline,
                     "parent_key": parent_key, "reporter": reporter,
                     "status": "open", "created_at": _now()}
        _save("bugs", bugs)
    return key


def get_bugs() -> dict[str, Any]:
    return _load("bugs")


# ── Finisher recovery (post-HITL) + release ledger ───────────────────
def put_under_review(session_id: str, pack: dict[str, Any]) -> None:
    """Persist the PR/ticket under review so the merge finisher can recover it
    AFTER the human merge gate (the SDK re-delivers only human-response meta —
    the pr_url/ticket flowing through the chain is dropped). Dev fallback to the
    in-process session stash; PROD: the Redis session store."""
    if not session_id:
        return
    with _LOCK:
        s = _load("under_review"); s[session_id] = {"stored_at": _now(), **pack}
        _save("under_review", s)


def get_under_review(session_id: str) -> dict[str, Any]:
    return _load("under_review").get(session_id, {}) if session_id else {}


def append_release_ledger(entry: dict[str, Any]) -> dict[str, Any]:
    """Append-only release ledger (dev mirror of the WORM sink for local demo)."""
    with _LOCK:
        ledger = _load("release_ledger")
        rows = ledger.get("rows", []) if isinstance(ledger, dict) else []
        rec = {"recorded_at": _now(), **entry}; rows.append(rec)
        _save("release_ledger", {"rows": rows})
    return rec
