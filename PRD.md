# PRD.md — HiveOS MVP

## Product

A cloud-deployed team workspace where human members request shared AI agents through an OS-style queue, with a real-time token budget visible to everyone — built entirely on AWS.

**The one-sentence test.** If a judge can say *"HiveOS is the OS scheduler for your team's shared AI budget — like a queue management system for AI agents, visible to everyone in real time"* — the product landed. Every implementation decision should serve that sentence.

---

## Problem

Teams sharing AI agents have **no visibility into usage, no fairness mechanism for access, and no real-time governance over token spend**. One heavy agentic task can burn a team's entire monthly budget in minutes — and nobody can see it happen while it happens.

This is verified, not assumed:

- Uber burned its entire 2026 AI coding budget in four months *(Fortune / The Information, May 2026)*
- One internal Uber demo cost $1,200 in two hours
- 79% of enterprises had AI cost overruns in the past 12 months *(DoiT / Sapio Research, Feb 2026)*
- Only 36% of organisations have any token or usage controls *(PointFive Research, Jul 2026)*
- Agentic workflows consume 10–30× more tokens than simple queries *(Forbes Tech Council, Aug 2026)*

**The insight.** Operating systems solved exactly this for CPU fifty years ago: process scheduling, time slices, resource quotas, fair queue allocation. HiveOS applies that abstraction to team AI compute — making invisible resource allocation visible and fair.

---

## Target user

Remote or hybrid teams of 3–6 people who share AI agent access. The demo uses a software team (developer, team lead, designer), but the product is general-purpose, not coding-only.

---

## MVP objective

Ship a **deployed, publicly reachable** workspace where three simultaneous browsers demonstrably share one token budget and one pool of agent slots, with queueing and shared memory working live and in real time.

---

## Core user journey

1. Open the public URL. Pick a name. Land in Team Alpha's workspace.
2. See the team's token meter and agent slot states — **identical on every screen**.
3. Click "Get Agent." A slot is free → it turns BUSY on everyone's screen.
4. Send the agent a task. Watch the token meter tick down on every screen as it works.
5. A third member claims while both slots are busy → sees "Position #1 · ~3 min."
6. Tell the agent to remember a team fact. A badge appears on every screen.
7. A slot frees → the queued member is auto-assigned → their agent already knows the team fact.

---

## MVP features

### Must build (MVP-Critical)

| Feature | Behaviour |
|---|---|
| Team workspace | One team (`TEAM#alpha`), multiple concurrent members |
| Agent slots | 2 named slots, `IDLE` / `BUSY`, with current holder |
| Real-time token meter | `tokens_used / token_budget`, identical across all browsers, updates without refresh |
| **Enforced budget ceiling** | Server-side refusal to invoke Bedrock at 100% — a real control, not a gauge |
| FIFO queue | Claims made while all slots are busy wait in order |
| Queue position display | "Position #1 · ~3 min", updated live |
| Auto-dispatch | Queued task is assigned automatically the moment a slot frees |
| WebSocket sync | Slot state, token count, queue, and memory pushed to all connected clients |
| Shared team memory | Agent saves a fact; the next user's agent loads it automatically |
| Working AWS deployment | Public HTTPS URL, opens cold on a judge's laptop with zero setup |
| GoneException handling | Stale WebSocket connections cleaned up on every broadcast |

### Build if core works (MVP-Supporting)

- Agent chat sidebar with response display
- 2D CSS canvas with member avatars *(cut first if earlier phases overrun)*
- Per-task token cost shown on completion
- Task history in the sidebar
- Team lead announcement broadcast

### Post-Hackathon

Cognito authentication · multiple teams · priority queue bump · agent marketplace · usage analytics · per-user budgets

### Out of scope — never build

Phaser.js or any game engine · tilemaps, physics, pathfinding · Google Calendar / Gmail / OAuth · CPU-style token preemption mid-generation · multiple DynamoDB tables · mobile app · voice or video · multi-team analytics

---

## Functional requirements

- A member may hold at most one agent slot at a time.
- Slot claims are atomic — two simultaneous claims never both succeed on the same slot.
- Token accounting is atomic and never lost to a race between concurrent agent runs.
- Queue ordering is FIFO by enqueue timestamp.
- Every state change is broadcast to all connected team members.
- Agent memory persists across sessions and is loaded into every new agent task.
- Bedrock is not invoked when the budget is exhausted.

## Non-functional requirements

- **Demo reliability outranks feature count.** One feature that runs beats five that almost do.
- State changes visible across browsers in under ~2 seconds.
- The public URL works from a cold browser with no local setup.
- Everything serverless, scaling to zero — no always-on resources.
- Total AWS spend for the build and demo stays well under the available credit.
- No secrets in the repository.

---

## Demo requirements

The 3-minute video is the **only** judge touchpoint. There is no live demo and no Q&A.

- Three browser windows, pre-loaded, three named members
- Must show on camera: the shared token meter moving, a slot going BUSY, a real queue position, auto-dispatch on release, and shared memory benefiting a different user
- Must name where AWS fits — naming AWS in the writeup alone does not count
- Must state honestly what the architecture does. Say *"every agent task runs through a real SQS queue, and waiting tasks auto-dispatch the moment a slot frees."* Do **not** claim queued users are parked inside SQS — see `ARCHITECTURE.md`.
- Address the Munder Difflin comparison in the first 20 seconds: *"Munder Difflin is a local harness for one developer's own CLI agents. HiveOS is a cloud governance layer for a team."*

**Fallback ladder if something breaks on the final day.** Fix only what is broken — never add features to rescue a demo.
1. Token meter + slot badges working → demo the HUD only
2. WebSocket broken → record components separately
3. Bedrock unavailable → mock agent responses and say so

---

## Success criteria

**Minimum viable submission**
- Public URL loads and shows live shared state across two browsers
- Slot claim and release work end to end
- Video recorded and under 3 minutes; repo public; writeup submitted before the deadline

**Target**
- All Must-build features working on the deployed URL
- Queue and auto-dispatch demonstrated live on video
- Shared memory demonstrably benefiting a second user

**Stretch**
- 2D canvas with avatars
- Per-task cost breakdown
