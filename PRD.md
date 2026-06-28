# AI Dev Squad — Product Requirements / Build Doc

> A Jira-driven autonomous software-development squad. A **product-owner
> agent** grooms a raw request into a well-formed ticket — testable
> acceptance criteria, priority, a QA discipline — and files it once a
> stakeholder signs off scope. A planner agent then
> watches the tickets **assigned to it**, picks the best one, writes a
> plan, and pings a human lead for sign-off. On approval, the **developer
> agent — the real Claude Agent SDK** (`framework: claude_agent`) —
> implements the feature in the repo: edits files, runs tests, commits,
> pushes, opens a PR, and updates the docs. An **independent QA agent** — a
> second Claude Agent SDK, read+run only — then **re-runs the test cases**
> for its discipline against the build (so the developer can't mark its own
> homework) and files any defects back into Jira as bugs; it also works its
> own assigned test tickets. A reviewer reads the PR; the human lead merges.
> Humans and agents talk back and forth at the gates.
>
> Built on LeafMesh as an **operation of self-reliant agents**. The coder
> is a first-class external connector (no custom code) — the Claude Agent
> SDK runs in-process via the SDK's native `claude_agent` framework.

---

## 1. The operation at a glance

```
 a stakeholder submits a raw request   (new_request, or jira:created event)
        │
   ┌────▼──────────────────────────────────────────────────────────┐
   │ product_owner_agent  (LLM)                                     │
   │  • pre_compose: raw request + open-ticket titles (dedupe)      │
   │  • groom → title, description, TESTABLE acceptance_criteria,   │
   │    priority, QA discipline                                     │
   │  • chain: file the ticket to Jira (status: triage)             │
   └────┬──────────────────────────────────────────────────────────┘
        │ ticket_ready (not a dup)
   ┌────▼──────────────────────────────────────────────────────────┐
   │ stakeholder_human   (scope sign-off)                          │
   │   approve → queue it · revise → re-groom · reject → audit      │
   └────┬──────────────────────────────────────────────────────────┘
        │ approve  (directed pick — releases the triage ticket)
 a human can also assign a Jira ticket directly to the squad's account
        │  (ticket_assigned event, or the planner's poll cron)
   ┌────▼──────────────────────────────────────────────────────────┐
   │ dev_planner_agent  (LLM)                                       │
   │  • @pre_compose: pull the tickets ASSIGNED to us, in priority  │
   │    order (Jira)                                                │
   │  • pick the best ticket to work next (deterministic, in chain) │
   │  • write an implementation plan                                │
   │  • ping the lead with the ticket URL for sign-off             │
   └────┬──────────────────────────────────────────────────────────┘
        │ plan ready
   ┌────▼──────────────────────────────────────────────────────────┐
   │ tech_lead_human   (review the plan)                           │
   │   approve → build · request_changes → re-plan · reject → audit │
   └────┬──────────────────────────────────────────────────────────┘
        │ approved
   ┌────▼──────────────────────────────────────────────────────────┐
   │ developer_agent  (EXTERNAL — framework: claude_agent)         │
   │   the Claude Agent SDK, in-process:                            │
   │   read repo → implement → run tests → commit → push → open PR  │
   │   → update docs (all in one branch/PR)                         │
   └────┬──────────────────────────────────────────────────────────┘
        │ PR opened
   ┌────▼──────────────────────────────────────────────────────────┐
   │ qa_agent  (EXTERNAL — claude_agent, read+run only, typed)      │
   │   independently RE-RUNS the test cases for its discipline      │
   │   (backend / api / frontend); files every defect into Jira     │
   │   clean → reviewer · failing → developer (bounded) · cap → lead │
   │   ALSO works its own assigned QA/test tickets (event + cron)   │
   └────┬──────────────────────────────────────────────────────────┘
        │ verified
   ┌────▼──────────────────────────────────────────────────────────┐
   │ code_reviewer_agent  (LLM — reads the PR diff)                 │
   │   clean → lead merge · issues → senior · blocker → developer   │
   └────┬───────────────────────┬───────────────────────────────────┘
        │ clean                 │ issues
   ┌────▼─────────────┐   ┌─────▼──────────────────┐
   │ tech_lead_human  │   │ senior_engineer_human  │
   │ (merge approval) │   │ approve → lead merge   │
   └────┬─────────────┘   │ changes → developer    │
        │ merge            └────────────────────────┘
        ▼
   audit_logger_agent  (WORM — every plan, approval, PR, merge)
```

The chain flows **forward** — the planner kicks work off and steps out;
nothing returns to a conductor (lesson from the outbound-sdr build: no
hub agents, or you get re-dispatch loops). Review→rework is the one
intentional cycle and it's **bounded** (see §4.5).

---

## 2. Actor inventory

| Agent | Class | One-line |
|---|---|---|
| `product_owner_agent` | 🤖 LLM | Grooms a raw request into a well-formed ticket (acceptance criteria), dedupes, files it |
| `stakeholder_human` | 👤 human | Signs off a groomed ticket's scope before it enters the dev queue |
| `dev_planner_agent` | 🤖 LLM | Pulls assigned Jira tickets, picks the best, plans it, pings the lead |
| `developer_agent` | 🧩 external `claude_agent` | The Claude Agent SDK — implements, tests, commits, opens the PR, updates docs |
| `qa_agent` | 🧩 external `claude_agent` (read+run) | QA engineer — re-runs the test cases for its discipline, works assigned test tickets, files bugs to Jira |
| `code_reviewer_agent` | 🤖 LLM | Reads the PR diff; approves, flags issues, or blocks |
| `merge_coordinator_agent` | ⚙️ system | CI gate → merge the PR → ticket to Done (Jira) → free the WIP slot |
| `tech_lead_human` | 👤 human | Signs off the plan; approves the merge |
| `senior_engineer_human` | 👤 human | Handles review escalations (cross-cutting / risky changes) |
| `audit_logger_agent` | 🧾 external (WORM) | Hash-chained record of plan / approval / PR / merge |

> **Why the coder is `claude_agent`, not an LLM agent.** A plain LLM emits
> text; it can't edit a repo, run tests, or open a PR. The Claude Agent
> SDK is an autonomous coder with file-edit + bash + git tools. LeafMesh
> runs it natively in-process via `framework: claude_agent` — pure YAML,
> no connector code to write. (Swap to `openai_agents` or a hosted
> platform like `lyzr` with a one-line `framework:` change if ever needed.)

---

## 3. Data artifacts
- **Ticket** — `{ticket_id, key, url, title, description, priority, status, assignee, acceptance_criteria[]}` (from Jira).
- **Plan** — `{ticket_id, approach, files_to_touch[], test_strategy, risks[], est_effort}`.
- **PR result** (from the developer agent) — `{pr_url, branch, files_changed, tests_passed, summary}`.
- **Review** — `{pr_url, verdict(approve|changes|block), findings[], requires_senior}`.

Tickets live in **Jira**; code + PRs in **GitHub**. A dev-mode
`_shared/store.py` seeds sample tickets so the planner → lead → (stub
developer) chain runs before connectors are wired.

---

## 4. Per-agent PRD

### 4.0 `product_owner_agent` — the product owner (LLM)
**Role.** Upstream of the planner — turns free-text into a buildable
ticket. The acceptance criteria it writes are the **same conditions QA
verifies**, so grooming quality drives test quality.
**Triggers.** `new_request` (a stakeholder / intake form posts raw text),
`jira:created` event (a bare ticket created elsewhere needs grooming),
`direct_call_po`.
**`@pre_compose`** `load_request_context` — pull the raw request + the
open-ticket titles (the dedupe signal) into prepared_data.
**`@chain`** `commit_ticket` — deterministic: file the groomed ticket to
Jira (dev: the store) as **status `triage`** so the planner's autonomous
poll won't grab it until a stakeholder signs off; the approve route then
hands it on (the planner's *directed pick* releases it).
**Wires.** → `stakeholder_human` when `ticket_ready` and not a duplicate;
→ `audit_logger_agent` when `not_actionable` or a duplicate (logged, no
ticket filed).
**Yields.** `title, description, acceptance_criteria, priority, discipline, ticket_ready, duplicate_of, not_actionable, ticket_id, ticket_key, ticket_url`.

### 4.0b `stakeholder_human` — scope sign-off (HUMAN)
**Role.** The business gate. Approves a groomed ticket's scope before it
costs engineering time. Routes on `human_message`: `approve` →
`dev_planner_agent` (queue + plan it), `revise` → `product_owner_agent`
(re-groom — a bounded, human-gated loop), `reject` / timeout → audit.
**Why a triage status + directed pick.** Filing as `triage` keeps the
ticket out of the autonomous queue until a human releases it — so an
auto-groomed ticket can't reach the developer without sign-off, and there's
no race with the poll cron.

### 4.1 `dev_planner_agent` — the planner (LLM)
**Triggers.** `ticket_assigned` (Jira webhook/event), `poll_assigned_tickets`
(cron, e.g. every 30 min), `direct_call_planner`.
**Responsibilities.**
1. **Pull assigned tickets, in order.** `@pre_compose` `load_assigned_tickets`
   hits Jira (dev: the store) and returns the squad's open tickets sorted
   by priority then age — so the LLM reasons over the real queue.
2. **Pick the best ticket** to work next — deterministic tie-break in the
   `@chain` (`pick_ticket`): highest priority, then oldest, then smallest
   est-effort; skip blocked/needs-info. (The LLM proposes; the chain locks
   the pick so it's explainable.)
3. **Write the plan** (approach, files, tests, risks).
4. **Ping the lead** — route to `tech_lead_human` with `ticket_url`.
**`@chain`** `pick_ticket` + `persist_plan` (write the plan to the store /
Jira comment).
**Wires.** → `tech_lead_human` when `plan_ready == true`; →
`audit_logger_agent` when `no_actionable_ticket == true` (queue empty —
stop, logged; not a loop).
**Yields.** `ticket_id, ticket_key, ticket_url, plan, plan_ready, no_actionable_ticket, priority_rank`.

### 4.2 `tech_lead_human` — plan sign-off + merge (HUMAN)
**Role.** The human in the loop. Reviews the plan (with the ticket URL),
and later approves the merge. Routes on `human_message`.
**Wires.**
- → `developer_agent` when `approve` (build it)
- → `dev_planner_agent` when `request_changes` (re-plan) — bounded (§4.5)
- → `audit_logger_agent` when `reject`
- (merge context) → `audit_logger_agent` when `merge`
**Inputs.** `ticket_key, ticket_url, plan, pr_url`.

### 4.3 `developer_agent` — the coder (EXTERNAL · `claude_agent`)
**Role.** Implement the approved ticket end-to-end with the Claude Agent SDK.
**Config (pure YAML).** `agent_type: external`, `framework: claude_agent`,
`connector_config`: `model`, `system_prompt` (senior-dev instructions:
read the repo, implement, run tests, commit on a feature branch, push,
`gh pr create`, update docs), `allowed_tools: [Read, Edit, Write, Bash,
Glob, Grep]`, `permission_mode: acceptEdits` (headless, no prompts),
`cwd: ${DEV_REPO_PATH:}`, `max_turns`, `timeout`.
**`@sdk.intelligence`** `shape_pr_result` — map the SDK's final result
into `{pr_url, branch, files_changed, tests_passed, summary}`.
**Input.** the approved plan + ticket (becomes the SDK run prompt).
**Wires.** → `qa_agent` when `pr_url` present (QA verifies before review); →
`tech_lead_human` when the run failed (no PR — a human investigates).
**Yields.** `pr_url, branch, files_changed, tests_passed, summary, ticket_id`.

### 4.3b `qa_agent` — the QA engineer (EXTERNAL · `claude_agent`, read+run)
**Why a separate agent (the gap it closes).** The developer self-reports
`tests_passed`; that's marking its own homework. The reviewer is an LLM
reading *text*; it never executes. The merge gate's CI check is a passive
status lookup. So nothing **independently runs the code** — a false green
sails to merge. QA is the independent executor that breaks that.
**Role.** A second Claude Agent SDK, **read + run only**
(`permission_mode: default`, `allowed_tools: [Read, Bash, Glob, Grep]` —
no Write/Edit: it VERIFIES, it doesn't change the repo), **typed by
discipline** via `QA_DISCIPLINE` (`backend | api | frontend`).
**Two trigger surfaces** (like the planner):
1. **Inline** — after the developer opens a PR, QA re-runs the test cases
   for its discipline against that build.
2. **Standalone** — QA is assigned its own test tickets in Jira
   (`label: qa`) and works them on an event (`jira:qa_assigned`) or the
   `*/45` cron.
**`@pre_compose`** `load_test_target` — assemble the test brief: the
inline PR + the ticket's acceptance criteria, or (standalone) the top
QA/test ticket for this discipline pulled from Jira (dev: the store);
this becomes the SDK run prompt.
**Shaper** `qa_agent(connector_result, …)` — map the SDK verdict into
yields, **file every defect as a Jira bug** (deterministic — the run
tests, the shaper guarantees the bugs land; `store.create_bug` /
`jira_create_bug` tool), and **bound the qa⇄developer rework loop**
(session-stash counter; at the cap → escalate to the lead, not loop).
**Wires.**
- → `code_reviewer_agent` when `qa_verdict == 'pass'` and `from_dev_flow` (verified build → human judgment)
- → `developer_agent` when `qa_verdict == 'fail'` and `from_dev_flow` and not `qa_escalate` (fix it — bounded)
- → `tech_lead_human` when `qa_escalate` (rework cap hit)
- → `audit_logger_agent` when `qa_verdict == 'fail'` or not `from_dev_flow` (log; standalone test tickets terminate here — the bugs filed to Jira are the output, and the planner picks them up: find→fix loop)
**Yields.** `ticket_id, pr_url, discipline, qa_verdict, qa_escalate, from_dev_flow, bugs_found, bugs_filed, tests_run`.
**Scale to all three disciplines** — duplicate the YAML block with a
different name + `QA_DISCIPLINE` (a department can declare three testers).

### 4.4 `code_reviewer_agent` — PR review (LLM)
**Role.** Read the PR diff and judge it.
**`@pre_compose`** `load_pr_diff` — pull the diff from GitHub (dev: from
the store/PR-result) so the LLM reviews real changes.
**`@chain`** `route_review` — set `requires_senior` / `is_blocker`
deterministically from the findings (a HIGH finding blocks; cross-cutting
→ senior) so the LLM can't wave through a blocker.
**Wires.**
- → `tech_lead_human` when `verdict == 'approve'` (merge gate)
- → `senior_engineer_human` when `requires_senior == true`
- → `developer_agent` when `verdict == 'changes'` (fix it) — bounded (§4.5)
- → `audit_logger_agent` when `is_blocker == true`
**Yields.** `pr_url, verdict, requires_senior, is_blocker, findings, ticket_id`.

### 4.5 `senior_engineer_human` + the rework bound
**Role.** Cross-cutting / risky review escalation. → `tech_lead_human`
on `approve`; → `developer_agent` on `request_changes`.
**Rework bound (LESSON FROM outbound-sdr / finance):** the
review→developer→review and plan→lead→plan cycles are intentional but
MUST terminate. A `@chain` rework-round counter (session stash) caps
fix loops at N (e.g. 3); on exhaustion → `tech_lead_human` with a
"manual intervention needed" flag instead of looping to the chain cap.

### 4.6 `audit_logger_agent` — WORM terminus.

---

## 5. Entry points
| Entry point | Target | Trigger |
|---|---|---|
| `new_request` | `product_owner_agent` | A stakeholder / intake form submits a raw request |
| `ticket_assigned` | `dev_planner_agent` | Jira assigns a ticket to the squad (webhook/event) |
| `poll_assigned_tickets` | `dev_planner_agent` | Cron — sweep the assigned queue |
| `qa_ticket_assigned` | `qa_agent` | Jira assigns a QA/test ticket (`label: qa`) |
| `poll_qa_tickets` | `qa_agent` | Cron — sweep the assigned QA/test queue |
| `pr_review_requested` | `code_reviewer_agent` | A PR is opened/updated (GitHub webhook) |
| `direct_call_planner` / `direct_call_developer` / `direct_call_qa` / `direct_call_reviewer` / `direct_call_merge` | each | Debug / replay |

---

## 6. Decorator map
| Agent | @pre_compose | @chain | @sdk.intelligence |
|---|---|---|---|
| product_owner | load request + open titles (dedupe) | file the groomed ticket (triage) | — |
| dev_planner | load assigned tickets (Jira) + directed pick | pick ticket + persist plan + rework-bound | — |
| developer | — | — | shape PR result from the SDK run |
| qa | load test target (PR or assigned QA ticket) | — | shape verdict + file bugs to Jira + bound qa⇄dev rework |
| code_reviewer | load PR diff (GitHub) | route review (blocker/senior floor) | — |

The QA shaper carries the `@pre_compose` AND the connector-result shaping
on one external agent — proof the external lifecycle is first-class
(pre_compose before the SDK run, shaper after), same as an LLM agent.

---

## 7. What you need to run it
1. **`pip install claude-agent-sdk`** + **Claude Code CLI on the host**
   *(host prerequisite — the customer's environment provides it; we just
   require it).*
2. **`ANTHROPIC_API_KEY`** (or Bedrock/Vertex/Foundry env flags).
3. **`gh` CLI installed + authed** (`GH_TOKEN`) and **git** configured
   (`user.name`/`user.email`) in the developer agent's environment.
4. **`DEV_REPO_PATH`** — a checkout the developer works in (branch/worktree per ticket).
5. **Jira connector** — `${JIRA_MCP_URL:}` + the squad's assignee account (planner's ticket pull).
6. **GitHub connector** — `${GITHUB_MCP_URL:}`/`GH_TOKEN` (reviewer's PR diff).
7. **LeafMesh base** — license + Redis + a model key for the planner/reviewer LLMs.

Connectors ship inert (env-var); a dev store seeds sample tickets so the
planner → lead chain runs day-0. The developer agent needs the real
Claude Agent SDK + repo to actually code.

---

## 8. Reflection / decisions
- **Replaced the generic `engineering-sdlc`** — superseded; this is the engineering template.
- **One human, two duties:** `tech_lead_human` does plan sign-off AND merge approval (one role, multiple responsibilities) rather than two separate humans.
- **Developer owns docs** — updating documentation is part of the same PR, not a separate agent.
- **Reviewer is an LLM, not a second Claude Agent SDK** — reviewing a diff is judgment over text; the heavy autonomous-coding SDK is reserved for build + verification. The *QA* agent is the read-only `claude_agent` that actually runs the code.
- **QA is independent, read-only, and Jira-driven** — separating "build" (developer, can write) from "verify" (QA, read+run only) closes the self-attestation gap; a developer can't both write the code and be the one that declares it passing. QA files bugs back to Jira, so its findings become first-class tickets the planner picks up — the squad's find→fix loop closes through Jira, not through an in-memory handoff.
- **One QA agent, typed by discipline** — shipped as a single `qa_agent` parameterized by `QA_DISCIPLINE` (backend default) rather than three hard-coded testers; duplicate the block to run frontend + backend + api in parallel. Keeps the default template lean while showing the fan-out pattern.
