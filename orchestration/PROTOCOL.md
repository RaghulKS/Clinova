# Orchestration protocol

Four orchestrators build Clinova at once: **A-claude** (integrator), **A-codex**, **B-claude** and **B-codex**. Each runs in its own clone, owns a queue of tasks, and hands every task to a subagent. They never talk to each other directly. They coordinate through **git tags on origin**, a **shared Tiger Cloud database**, and **push notifications**.

## Signals (git tags on origin)

| Tag | Meaning | Created by |
| --- | --- | --- |
| `kickoff` | Build clock starts (hour 0) | A-claude, at launch |
| `gate/contracts` | Skeleton, contracts and spikes are on main; Wave 1 may start | T00 |
| `gate/1`, `gate/2`, `gate/3` | Gate merged and verified on main; next wave may start | G1, G2, G3 |
| `gate/ship` | v1.0 released; the build is complete | G4 |
| `done/<ID>` | Task finished; message starts with `ok:`, `partial:` or `cut:` | the task agent |
| `failed/<ID>` | Task gave up; message has the reason | the task agent |

`scripts/orch.sh` wraps all of it: `status`, `hour`, `wait`, `is-done`, `start`, `done`, `failed`, `gate`, `notify`, `preflight`, `kickoff`.

## Orchestrator rules

1. **Stay thin.** Never write product code yourself. Read only task headers (the front matter between `---` lines), not task bodies; the subagent reads its own task file.
2. **Idempotent.** Before starting a task, run `scripts/orch.sh is-done <ID>`; if it exits 0, skip it. Re-pasting a launch prompt after a crash resumes where the build left off.
3. **Waiting.** Call `scripts/orch.sh wait --timeout-min 5 <tags...>` repeatedly until it exits 0. Every 30 minutes of waiting, run `scripts/orch.sh status` once and check whether anything you wait for is `FAILED`. If you wait more than 45 minutes past a task's `deadline_hour`, notify once with what you are waiting for.
4. **Before each wave:** `git switch main && git pull --ff-only` in your main clone, so new worktrees start from the latest gate.
5. **Spawning.** Spawn each task as a subagent of the type in its header, passing the model and effort in the header, with exactly this delegation message:
   `Task <ID>. You are a Clinova task agent. Read AGENTS.md, then orchestration/PROTOCOL.md (section "Task agent contract"), then orchestration/tasks/<ID>.md, and complete the task end to end without asking questions. Finish with scripts/orch.sh done or failed as the contract says. Reply with at most 15 lines: status, what works, numbers produced, background jobs left running, problems.`
   Tasks listed together in your launch prompt run in parallel: spawn them in the same turn.
6. **After a subagent returns:** check `scripts/orch.sh is-done <ID>`.
   - Done: notify `"<agent> ✓ <ID>"` with the tag message.
   - Not done (failed tag, no tag, crash, usage limit): retry once with a fresh subagent and this extra line: `A previous attempt failed: <reason>. Run the Unstick procedure in PROTOCOL.md first, and resume from the existing branch.` If the retry also fails and it is past the task's deadline, have a subagent apply the task's `fallback` and tag `done/<ID>` with `cut:` or `partial:`. Notify either way.
7. **Model access.** If a spawn fails because the account lacks the named model, retry with the best model you can use, at the same effort, and say so in the next notification.
8. **Notify** (via `scripts/orch.sh notify "<title>" "<message>"`): at launch, after each of your tasks, on any retry or cut, when blocked as in rule 3, and when your queue is finished. Keep titles short, prefixed with your name, for example `A-codex ✓ T11`.

## Task agent contract

Every task agent follows these steps. The task file adds the specifics.

1. **Start.** Run `scripts/orch.sh start <ID>`. It prints a directory.
   - Claude subagents already run in an isolated worktree: the script switches it to the task branch and links `.env`. Work in place.
   - Codex subagents run from the main clone: the script creates `.worktrees/<ID>` on the task branch. Run every command with that directory as the working directory (for example `cd <dir> && ...`) and edit files only there.
   - If the branch already exists on origin, you are resuming: read `docs/handoff/<ID>.md` and the git log first.
2. **Read** `AGENTS.md`, the `docs/ARCHITECTURE.md` sections your task names, and (after T00) `docs/CONTRACTS.md`.
3. **Set up Python** once per worktree: `uv sync`. Load settings only through `clinova/common/config.py`.
4. **Stay in your lane.** Edit only paths your task owns (AGENTS.md table). Shared-file changes go in `docs/contract_changes/<ID>.md`.
5. **Fan out.** Where the task lists independent parts, delegate them to parallel subagents of your own if your tool allows nesting; otherwise do them in sequence.
6. **Watch the clock.** `scripts/orch.sh hour` prints hours since kickoff. From `deadline_hour − 0.5`, add no new scope. At `deadline_hour`, ship what works, applying the task's `fallback` for what doesn't.
7. **Background jobs** follow AGENTS.md. Start them as early as the code allows; don't wait for them to finish unless the task says so.
8. **Finish.**
   - `make check` passes (spike tasks before T00 have no Makefile; skip this step).
   - Write `docs/handoff/<ID>.md`: what works, what is stubbed or faked, how to run it, background jobs (command, log path, progress check), numbers produced, decisions made, open issues.
   - `scripts/orch.sh done <ID> ok "<one line>"` (or `partial` / `cut`). This commits, pushes your branch and pushes the `done/<ID>` tag.
9. **Give up only** after the Unstick procedure fails twice: `scripts/orch.sh failed <ID> "<reason>"`, then reply.
10. **Never** push to main (only the integrator does), touch another task's branch, rewrite history on origin, commit `.env`, or print secrets.

## Unstick procedure

Stop writing code. Write down what is failing and what has been tried. List three hypotheses for the root cause, ranked, each with the smallest experiment that confirms or rules it out. Run them in order and fix the confirmed cause. If nothing is confirmed within 20 minutes, apply the task's `fallback`, keeping its public interface intact, and record it in the handoff.

## Integrator rules (A-claude gate tasks)

- Merge `origin/task/*` branches into main with `--no-ff`, one at a time, in the order the gate lists. Resolve conflicts by ownership: the owning task's version wins inside its paths. Run `make check` after each merge and fix breakage before the next.
- Apply every request in `docs/contract_changes/*.md` that is safe; mark each one applied or rejected with a reason.
- Also merge any `done/*` task from an earlier wave that wasn't merged yet.
- **Gate fallback:** wait for required tasks until their deadline plus 30 minutes. Then proceed with what exists: merge `partial` branches if `make check` passes on them, otherwise leave their fixtures in place and list them in the gate report. Never block the build on an optional task (T16).
- Finish every gate with `git push origin main`, `scripts/orch.sh gate <name> "<summary>"`, and a notification whose message holds the headline numbers.

## Rate limits and budget

All four orchestrators share one Tiger Cloud database and, per laptop, one Gemini key. `clinova/common/llm.py` enforces `MAX_GEMINI_CALLS` from the `llm_usage` table for its key and backs off on 429s. If Claude or Codex usage limits pause a subagent, the orchestrator waits and retries; it does not cut a task for a usage limit alone unless the deadline has passed.
