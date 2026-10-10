# One-time setup (about 20 minutes, both people)

After this, each person pastes one prompt into Claude Code and one into Codex, and the build runs itself.

## 1. Secure the secrets (Person A, now)
- Run `git ls-files .env` in the repo. If it prints `.env`, the file is committed: run `git rm --cached .env`, commit, push, and **rotate the Gemini key and the Tiger Cloud password**, because anything pushed to GitHub should be treated as exposed (it stays in history).
- Rename variables in `.env` to match `.env.example` (`GEMINI_API_KEY`, `DATABASE_URL`). Add `NTFY_TOPIC` (any long random string), `MAX_GEMINI_CALLS=12000`.
- Use a different Gemini key on each laptop if you have two; each laptop's budget is tracked separately.
- Share `.env` with your friend over a private channel, never through git.

## 2. Add the orchestration bundle (Person A)
Unzip this bundle into the repo root, then:
```bash
git add -A && git status   # confirm .env is NOT listed
git commit -m "Clinova architecture and orchestration bundle" && git push origin main
```
Add your friend as a GitHub collaborator with write access.

## 3. Each laptop (both people)
- Install: Claude Code, Codex CLI, `uv`, Docker Desktop (running), git with push access to the repo (SSH key or `gh auth login`).
- Phone: install the **ntfy** app and subscribe to your `NTFY_TOPIC`. Every orchestrator notifies there.
- Make two clones, one per tool, and copy `.env` into each:
```bash
git clone <repo-url> ~/clinova-claude && cp .env ~/clinova-claude/
git clone <repo-url> ~/clinova-codex  && cp .env ~/clinova-codex/
cd ~/clinova-claude && chmod +x scripts/orch.sh && scripts/orch.sh preflight
cd ~/clinova-codex  && scripts/orch.sh preflight
```
- Keep the laptop awake and online for 16 hours: plugged in, lid open, `caffeinate -dimsu &` on macOS (`systemd-inhibit --what=sleep sleep 20h &` on Linux).
- Plans: these runs use a lot of tokens. Use the highest Claude and ChatGPT plans you have; if a usage limit pauses a session, it resumes and waits rather than failing.

## 4. Launch (Person A first, then Person B within 10 minutes)

**Claude Code** (in `~/clinova-claude`):
```bash
claude --model opus --effort medium --permission-mode auto
```
Auto mode lets it run unattended while a classifier blocks risky actions. If your plan doesn't offer auto mode, the only fully unattended option is `--dangerously-skip-permissions`; use it only on a machine or VM you're comfortable giving the agent full control of.

**Codex** (in `~/clinova-codex`):
```bash
codex -m gpt-6.1-sol -c model_reasoning_effort=medium \
  --sandbox workspace-write --ask-for-approval never \
  -c sandbox_workspace_write.network_access=true \
  --add-dir ~/.cache/uv --add-dir ~/.local/share/uv
```
When Codex asks whether to trust the folder, say yes (it loads `.codex/agents/`). If preflight inside Codex reports that `git push` fails, the sandbox can't reach your SSH agent or keychain: give the Codex clone a fine-grained GitHub token remote (`git remote set-url origin https://x-access-token:<TOKEN>@github.com/<owner>/<repo>.git`; it stays in that clone's local `.git/config`) and relaunch.

Then paste the one-line prompt for that session:

| Who | Tool | Paste this |
| --- | --- | --- |
| Person A | Claude Code | `Read orchestration/launch/A-claude.md and follow it exactly, end to end, without asking me anything.` |
| Person A | Codex | `Read orchestration/launch/A-codex.md and follow it exactly, end to end, without asking me anything.` |
| Person B | Claude Code | `Read orchestration/launch/B-claude.md and follow it exactly, end to end, without asking me anything.` |
| Person B | Codex | `Read orchestration/launch/B-codex.md and follow it exactly, end to end, without asking me anything.` |

## 5. During the build
- Progress anytime: `scripts/orch.sh status` in any clone.
- Your phone gets a notification for every finished task, every gate (with headline numbers), any failure or long block, and **`Clinova ✓ ALL COMPLETE`** at the end.
- If a session crashes or a laptop reboots: relaunch the tool in the same clone and paste the same one-liner. It resumes from git tags.
- Optional overrides in `.env` (both laptops): `DEMO_NCT_ID=NCT…` to force the demo trial; `SKIP_MIROFISH=1` to skip it.

## 6. After `ALL COMPLETE`
Record the 3-minute video with `docs/video/script.md` and `docs/video/checklist.md`, upload it, and submit using `docs/submission/devpost.md`, the deck in `docs/deck/`, and the release link.
