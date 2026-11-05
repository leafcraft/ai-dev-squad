# ai-dev-squad — AI Dev Squad

A Jira-driven autonomous software-development squad. A **product owner**
grooms a raw request into a well-formed ticket (acceptance criteria), and a
stakeholder signs off scope. A **planner** watches
the tickets assigned to it, picks the best, plans it, and pings the tech
lead. On sign-off, the **developer — the real Claude Agent SDK** — builds
it (edit → test → commit → push → open PR → update docs). An independent
**QA agent** — a second Claude Agent SDK, read+run only — re-runs the test
cases and files bugs to Jira. A **reviewer** reads the PR; the **tech
lead** approves the merge (a **senior engineer** does for cross-cutting or
risky changes), and a finisher re-checks that approval and CI before it
merges. **Read `PRD.md` first.**

## Why it works in an organization

Engineering throughput is gated by the **boring middle of the SDLC** —
grooming tickets, planning, writing the change, testing it, reviewing,
merging. This squad automates that middle: the **real Claude Agent SDK**
writes the code and an **independent QA agent re-runs the tests** (so a
self-reported "passed" can't ship), while humans keep the high-judgment
gates — scope sign-off, plan approval, merge. Work flows through **Jira and
GitHub**, the systems the org already runs; bugs QA finds become Jira
tickets the planner later picks up — a closed **find → fix loop**. The
result: routine tickets ship without a human hand-writing code, while a
tech lead still controls *what* gets built and merged, with bounded rework
and a hash-chained audit keeping it safe. ROI: more throughput per
engineer, shorter cycle time, fewer review round-trips.

## The squad (11 agents)

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `squad_coordinator_agent` | Squad Coordinator (case owner) | Open the case on a new request, hand the verbatim ask to the product owner, keep live status, be the requester's single point of contact; hand an exception it cannot route to the tech lead — never grooms, codes, or approves | LLM (pure YAML); entry points `new_request` / `delivery_status`; routes to product_owner / tech_lead |
| `product_owner_agent` | Product owner (upstream feeder) | Groom a raw request → well-formed ticket (testable acceptance criteria + priority + QA discipline); dedupe vs the backlog; file it as `triage` | LLM; `@pre_compose` (request + open titles), `@chain` (file ticket), tool `jira_update`; listens on `jira:created` (commented); routes to stakeholder (ready, not a duplicate) / audit (everything else) |
| `stakeholder_human` | Product Manager — scope gate | Approve / revise / reject a groomed ticket's scope before it costs eng time; answers within two business days | Human; `approve` → planner, `revise` → PO, `reject` → audit |
| `dev_planner_agent` | Planner | Pull assigned tickets (priority, then oldest, then smallest effort), pick the best, write the plan; directed-pick a stakeholder-approved ticket | LLM; `@pre_compose` (queue + directed), `@chain` (commit pick + WIP cap + Jira), tool `jira_update`; cron every 30 min in working hours + `jira:assigned` (commented); routes to tech_lead / audit |
| `developer_agent` | Coder — the Claude Agent SDK | Implement the approved ticket: edit, test, commit, push, open the PR, update docs | External `claude_agent` (writes); shaper maps result + sets Jira "In Review"; routes to QA or tech_lead (blocked) |
| `qa_agent` | QA engineer — Claude Agent SDK (read+run) | Re-run the test cases for its discipline against the build; work its own assigned test tickets; file every defect to Jira; bound the rework loop (3 rounds) | External `claude_agent` (read+bash, no writes), 3 parallel copies; `@pre_compose` (test target), shaper (verdict + file bugs + escalate); cron at :15/:45 in working hours + `jira:qa_assigned` (commented); routes reviewer / developer / tech_lead / audit |
| `code_reviewer_agent` | PR reviewer | Judge the diff: approve / request changes / block; enforce the security floor; cap rework rounds (3) | LLM; `@pre_compose` (PR diff), `@chain` (floors + bound + verdict normalised); exactly one route per review: blocker → audit, cross-cutting → senior, approve → tech_lead, changes → developer |
| `merge_coordinator_agent` | Release gate (finisher) | Re-check the human approval → CI gate → merge the PR (once) → move the ticket to Done in Jira → free the WIP slot | Programmatic; routes audit (merged / blocked) / developer (CI red, ≤ 3 rounds) / tech_lead (CI rework cap) |
| `tech_lead_human` | Tech Lead | Sign off the plan, then approve the merge; take every rework-cap escalation; answers within one business day | Human; `build` / `replan` / `merge` / `reject` |
| `senior_engineer_human` | Senior (Staff) Engineer | Handle review escalations (cross-cutting / risky); their `approve` is the merge approval for that PR; answers within one business day | Human; `approve` / `request_changes` / `reject` |
| `audit_logger_agent` | WORM audit | Hash-chain every rejection, duplicate, empty queue, blocked review, standalone QA result, merge and blocked merge | External (custom sink); terminus |

The maker never approves its own work: the developer cannot pass its own
tests (QA re-runs them, read-only), cannot review its own diff (the reviewer),
and cannot merge (a human approves; the finisher re-checks the approval and CI).

## Who decides, and what happens on silence

| Seat | Real job | Replies | Waits | If nobody answers (2.4.486) | Where a real team escalates |
|---|---|---|---|---|---|
| `stakeholder_human` | Product Manager | `approve` / `revise` / `reject` | 2 business days (172800 s) | Card closes; the ticket stays in `triage`, never queued | Head of Product |
| `tech_lead_human` | Tech Lead | `build` / `replan` / `merge` / `reject` | 1 business day (86400 s) | Card closes; nothing is built or merged | Engineering Manager |
| `senior_engineer_human` | Senior (Staff) Engineer | `approve` / `request_changes` / `reject` | 1 business day (86400 s) | Card closes; the PR is not merged | Tech Lead |

When nobody answers in time, the request waits no longer: its Inbox card
closes and the ticket records that no reply came. Nothing is sent, built or
merged on silence, and **nobody is chased automatically** — the Manager does
not escalate an unanswered gate. Each seat's `fallback_response`
(`held_for_review`, which matches its audit route) is the declared intent; on
leafmesh 2.4.486 a dual seat's timeout does not send it on. The escalation
column is not wired yet.

The **Manager** escalates to the Tech Lead only when an agent hits an error
retrying cannot fix (auth, bad request, out of credit, bad config), when 3
different agents fail on one ticket (`max_errors_per_session: 3`), or when an
agent's restarts are used up. A broken output contract only alerts people. The
squad's own rework loops (QA, review, CI) reach the Tech Lead through their
`can_call` wires after 3 rounds.

## Configuration — what to set, in three tiers

Everything is set in `.env` (`cp env .env`). The squad boots and the full
**request → groom → plan → review → merge** chain runs on **Tier 1 alone**
(developer + QA run as safe stubs, tickets come from a seeded local store).
Tiers 2–3 turn the stubs into real code + real connectors.

### Tier 1 — required to boot (dev mode)

| Setting | What it's for | Notes |
|---|---|---|
| `LEAFMESH_LICENSE_KEY` | LeafMesh runtime license | **Required.** |
| `ANTHROPIC_API_KEY` | The planner / reviewer / PO LLMs **and** the Claude Agent SDK | **Required.** Bedrock/Vertex/Foundry env flags work instead. |
| `OPENAI_API_KEY` | The Manager model (`gpt-4o-mini`) | **Required** unless you change `manager.model`. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | State store + event broker | Defaults to `localhost:6379`. `docker run -d -p 6379:6379 redis:7-alpine`. |

With just Tier 1: `pip install -r requirements.txt && python validate_config.py && python main.py`.

### Tier 2 — host prerequisites to make the coder + QA real

The developer **writes** code and the QA agent **runs** it — both are the
real Claude Agent SDK, so they need tools on the host, not just env vars:

| Prerequisite | Why |
|---|---|
| `pip install claude-agent-sdk` | The SDK the `claude_agent` framework drives in-process. **Not in `requirements.txt`** — it's a host concern. |
| **Claude Code CLI** on `PATH` | The SDK shells out to it. |
| `git` with `user.name` / `user.email` | The developer commits. |
| `gh` CLI installed + `GH_TOKEN` | The developer runs `gh pr create`. |
| `DEV_REPO_PATH` | Absolute path to the repo checkout they work in (developer writes, QA reads+runs). **If empty, there's nowhere to work.** |
| `QA_DISCIPLINE` | `backend \| api \| frontend` — the QA lane. Duplicate the `qa_agent` block in `configs/config.yaml` to run all three at once. |

### Tier 3 — going live (replace the dev store)

⚠️ The Jira/GitHub side is **not a plug-in URL**. The deterministic steps
run on a local dev store (`agency/_shared/store.py`) that stands in for both
Jira and GitHub. To go live you **replace those store functions with your
own MCP/API calls** (their signatures are the contract — see the PRD).

The LLM agents can also be given live Jira/GitHub tools. These ship
commented; addresses and tokens live only in the vault (a URL or token
written in `config.yaml` is refused at load):

| Vault connection (Infrastructure → Integrations → MCP, or an "Add custom" record with service type Tools) | Then uncomment (in `configs/config.yaml` before the first start, in Studio's YAML editor after it) | Feeds |
|---|---|---|
| `jira_mcp` — the Jira MCP server's URL + token | the `mcp:` rows naming `jira_mcp` | product_owner, planner |
| `github_mcp` — the GitHub MCP server's URL + token | the `mcp:` rows naming `github_mcp` | code_reviewer |

Neither connection changes `store.py`: until you replace it, tickets, PR
diffs and CI status still come from the dev store (the merge coordinator says
so in its log). Nothing for Jira or GitHub goes in `.env`. The QA agent (an
external Claude Agent SDK agent) takes no `mcp:` rows — its Jira reads and bug
filing are `store.py` functions.

One broker connector needs no code, but is **off on day 0**:

| Setting | What it's for |
|---|---|
| *(vault connection `jira_events`)* | The `jira_events` broker the planner/QA/PO `listen_events` on. Create the connection (its URL) on Infrastructure → Brokers & queues → Redis Streams, then uncomment the broker and the three `listen_events` blocks — in `configs/config.yaml` before the first start, in Studio's YAML editor after it (the agency reloads itself in place; no restart). |

For a production event bus, swap the `brokers:` block to SQS/Kafka (commented
examples are in `configs/config.yaml`; before the first start edit the file,
after it use Studio's YAML editor — see *Changing the configuration after the
first start*) and `pip install leafmesh[sqs]` or `leafmesh[kafka]`.

**Optional tuning:** `DEV_SQUAD_MAX_WIP` (default `1`) caps how many
tickets are in flight at once — raise it to let the squad work in parallel.
The rework cap (3 rounds for QA, review and CI) is `_MAX_REWORK_ROUNDS` /
`_MAX_MERGE_FIX_ROUNDS` in the agents' Python.

## Run it

```bash
pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7-alpine
cp env .env                # fill at least Tier 1
python validate_config.py  # pre-flight: wiring, yields, dead-code
python main.py             # API at http://127.0.0.1:18820/docs
```

Submit a raw request to start the chain:

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/request \
  -H "Content-Type: application/json" \
  -d '{"entry_point":"new_request",
       "data":{"request":"Let customers download each invoice as a PDF from the account page"}}'
```

In dev mode the PO grooms it, the stakeholder approves it in Studio's Inbox,
the planner plans it, the tech lead replies `build`, and the developer/QA stubs
carry it to review — all against `./data`. Add Tier 2 to make it write real
code, Tier 3 to use real Jira/GitHub.

### Seed data (day 0)

`agency/_shared/store.py` seeds a fictional online retailer's engineering
queue so every route has input:

- **Squad queue** (planner order: priority, then oldest, then smallest effort):
  ENG-1215 critical duplicate-refund bug → ENG-1201 rate-limit headers →
  ENG-1188 flaky checkout test → ENG-1196 CSV export; ENG-1210 is `blocked`
  and ENG-1219 is `needs_info`, so neither is picked.
- **Dedupe:** send `"Add CSV export to the orders page"` as a new request — it
  matches open ticket ENG-1196, which is in the PO's open-title list; the PO
  model should set `duplicate_of`, and a duplicate goes to audit instead of the
  stakeholder (the route is enforced; spotting the duplicate is the model's
  judgement).
- **PRs:** `.../pull/1287` (ENG-1201, CI green) merges after approval;
  `.../pull/1291` (ENG-1188, CI red) goes back to the developer — run
  `direct_call_merge` with `{"pr_url": "https://github.com/your-org/shop-platform/pull/1291", "human_message": "merge"}`.
- **QA backlog:** four `label: qa` tickets across api, frontend and backend.

---
*Built with `create-leafmesh`. The config is the team; the PRD is the brief.*

## Changing the configuration after the first start

`configs/config.yaml` is only the first-start seed. On the first start (an empty store) the agency imports it as version 1 and from then on keeps its configuration in the store. After that the file is not read again: an edit to it is **not** applied, even after a restart (the boot log says "that edit was NOT applied").

To change a running agency:

- **Studio** — its settings pages, or the raw YAML editor.
- **API** — `PUT /api/yaml/raw` with an `X-API-Key` header and the body `{"yaml_content": "<the whole YAML>", "save_to_disk": true}`, or `PATCH /api/yaml/config` with `{"updates": {...}, "save_to_disk": true}`.

Each save is a new version and applies live on every pod — no restart. (A few settings, such as brokers, make the agency reload itself in place within seconds.) So wherever this README or a comment says "uncomment X in `configs/config.yaml`", do it in the file before the first start, or in Studio's YAML editor after it.

## Changing a running project (Docker)

What reaches a project that is already running (leafmesh 2.4.497). The
image holds the Python code and the first-start `configs/config.yaml`;
`.env` is passed in when the container is created; the store (Redis) keeps
its data on its own volume.

| You change | When it applies |
|---|---|
| An agent in Studio (prompt, model, tools, routes, human seats, schedules, …) | At once, no restart |
| A human seat's `channels` (Slack, email, …) | The agency reloads itself in place within seconds |
| Add or delete an agent in Studio | At once, including routes and schedules |
| A connection in the vault (model keys, MCP, webhooks, connectors, Slack, email) | At once. An agent switched off for a missing connection or address switches back on by itself |
| Anything in `.env` (licence, agency name, store, ports) | After `docker compose up -d` (the container is recreated) |
| Python code (`agency/*.py`: programmatic agents, tools, `@pre_compose`) | After a rebuild: `docker compose up -d --build` |
| `configs/config.yaml` | Never after the first start. It is read only when the store is empty |
| Several app containers on one store | A Studio save reaches every one within about 15 seconds |

- Keep model keys in the vault. A key there wins over the same key in
  `.env` (the log warns), and a change applies at once. From 2.4.497 that
  holds for every value the vault holds (endpoints, organisation / project
  ids, cloud identities); the `.env` value returns when the record goes.
- Code the image does not have: an agent marked pure Python
  (`integration: python`) is switched off until its function is in the image;
  any other programmatic agent stays on, and each call to code it does not
  have fails. Rebuild the image with the code first.
- A new Redis volume is an empty store, so `configs/config.yaml` is imported
  again as version 1.
- **If the agency refuses to start after an upgrade** (a new leafmesh pin,
  then a rebuild: "Configuration has N error(s) in the stored
  configuration"), repair the stored configuration with the agency stopped:

  ```bash
  docker compose run --rm sales-orchestration leafmesh config check      # each problem, with its line
  docker compose run --rm sales-orchestration leafmesh config versions   # kept versions: which pass, which is in use
  docker compose run --rm sales-orchestration leafmesh config rollback   # back to the newest version that passes (saved as a new version)
  ```

  Or `leafmesh config export > config.yaml`, fix it by hand, then
  `leafmesh config import config.yaml` (it is checked before it is saved).

## Where the data store is set

From leafmesh 2.4.482 the store (Redis or Postgres) is set **only in `.env`** — never in `configs/config.yaml`. A `persistence:` block in the YAML is ignored with a warning on leafmesh 2.4.482–2.4.485; from 2.4.486 the agency refuses to start if that block differs from `.env`, and names each `.env` line to add. The project's `.env` sets the store explicitly (`LEAFMESH_STORE`, `LEAFMESH_AGENCY_NAME`, `REDIS_*`).

- Redis (default): `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`; optional `REDIS_DEFAULT_TTL`, `REDIS_SESSION_TTL`, `REDIS_SSL*`, `REDIS_CLUSTER_NODES`.
- Postgres instead: `LEAFMESH_STORE=postgres` plus `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (optional `POSTGRES_SSLMODE`).
- Or the whole server in one value: `REDIS_URL=redis://user:pw@host:6379/0` (`rediss://` turns TLS on) or `DATABASE_URL=postgresql://user:pw@host:5432/db`. The single names fill in what the URL leaves out.
- With Docker Compose the app reaches Redis as `redis` (the service name); compose sets that for you.
- Check it in Studio → **Infrastructure → Datastores**: it shows whether the store is reachable (never the address or password).

## This mesh's sign-in (leafmesh 2.4.497)

From leafmesh 2.4.497 each mesh signs in to LeafCraft with its own key, not
a key shared by every install.

- **First start.** The mesh makes a key pair and registers it with
  `LEAFMESH_LICENSE_KEY` and `LEAFMESH_ENV_TOKEN` from `.env` (copy the
  environment token from Studio — a placeholder such as `development` is
  refused). With both valid it works at once. If your organisation turned on
  "require approval", the first start stops and says so: approve it in
  Studio → **Keys → SDK access**, then start again. Organisation admins get an
  email for every new registration.
- **Where the key lives.** In the vault, in the store, encrypted with
  `LEAFMESH_VAULT_KEY`: it survives rebuilds and every copy of the app shares
  it. A new Redis volume, or a new vault key, makes a new key pair that
  registers again — revoke the old one in Studio.
- **Check or change it** (in Docker: `docker compose run --rm sales-orchestration leafmesh auth status`):
  - `leafmesh auth status` — credential id, key fingerprint, registration and
    mode, never a key; `--check` signs in now and reports the tokens.
  - `leafmesh auth register` — register now.
  - `leafmesh auth rotate` — a new key pair: once it is active the mesh
    switches to it and revokes the old one. A revoked or expired key is never
    revived; rotate instead.
- **Lost the environment token?** From 5 Dec 2026 Studio shows the licence
  key and environment token only once, when they are made (then only a hint).
  Rotate the environment token in Studio and put the new one in `.env`.
  Rotating it changes where the agency's data is kept in the store, so do it
  before the agency holds data.
- **An existing project whose `.env` says `LEAFMESH_ENV_TOKEN=development`.**
  2.4.497 needs the real token. The token also names where the agency's data
  sits in the store, so with the real token the agency starts on an empty
  store (vault, sessions, board) — the old data stays under the old name. Set
  the real token before you put data in the agency, or contact us to move it.

## Your vault key — back it up

`create-leafmesh` put a `LEAFMESH_VAULT_KEY` in your `.env` when it made this project. It is the master key that encrypts every connection and credential you store in the vault (in Redis / Postgres).

- **Back it up like a master password.** Lose it and every stored connection has to be entered again.
- **Never change or regenerate it** for an existing project — a new key locks the existing vault. To change it, use `leafmesh vault rotate-key`.
- Every copy of the app (every replica) must get the **same** value. Docker Compose passes it in from `.env` as an environment variable.
- Never commit `.env` (it is already in `.gitignore`).
