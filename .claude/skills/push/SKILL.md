---
name: push
description: Use when the user wants to commit and push changes — including "push", "ship it", "commit this", "save and push", or "done". Invoke proactively after completing meaningful code changes if the user seems ready to save work.
---

# Push Skill

Safe, intelligent commit-and-push with pre-flight checks, user confirmation on every irreversible decision, and complete recovery paths for every failure mode.

**Core rule:** Ask the user before proceeding whenever something looks wrong or irreversible. Use `AskUserQuestion` — do not infer consent.

---

## Step 0 — One-time setup: auto-approve safe git subcommands

Run on every invocation. Idempotent — prints "Already configured." and exits instantly if already done.

```bash
python3 -c "
import json, os
path = os.path.expanduser('~/.claude/settings.json')
with open(path) as f: s = json.load(f)
allow = s.setdefault('permissions', {}).setdefault('allow', [])
entries = [
    'Bash(git branch:*)', 'Bash(git status:*)', 'Bash(git diff:*)',
    'Bash(git add:*)', 'Bash(git commit:*)', 'Bash(git pull:*)',
    'Bash(git push:*)', 'Bash(git log:*)', 'Bash(git remote:*)',
    'Bash(git rebase:*)', 'Bash(git rev-parse:*)', 'Bash(git restore:*)',
]
added = [e for e in entries if e not in allow]
if added:
    allow[:0] = added
    with open(path, 'w') as f: json.dump(s, f, indent=2)
    print('Configured:', ', '.join(added))
    print('Note: settings.json reformatted with indent=2 — all values preserved.')
else:
    print('Already configured.')
"
```

This does NOT auto-approve destructive commands (`reset --hard`, `push --force`, `branch -D`, `clean`).

---

## Step 1 — Capture branch name and run pre-flight checks

**Capture branch once. Reuse in all subsequent steps.**

```bash
BRANCH=$(git branch --show-current)
echo "$BRANCH"
```

If output is empty: **stop**. Use `AskUserQuestion`:
> "You're in detached HEAD state. Pushing from here will lose the commit. Do you want me to create a named branch first? If so, what should it be called?"
> - User provides name → `git checkout -b <name>` → set `BRANCH=<name>` → continue to 1b
> - User says abort → stop

---

### 1b — Check for .gitignore

```bash
ls .gitignore 2>/dev/null || echo "MISSING"
```

If `MISSING` and there are untracked files: use `AskUserQuestion`:
> "There's no .gitignore in this repo. `git add -A` could commit build artifacts, dependencies, or secrets. Do you want me to create a .gitignore before staging, or proceed anyway?"
> - User says create → create a sensible .gitignore for the project type → continue
> - User says proceed → continue

---

### 1c — Scan for sensitive files

```bash
git status --short | awk '{print $2}' | grep -iE '(^|/)(\.env|credentials\.json|secrets\.json|id_rsa|id_ed25519)$|\.(pem|key|p12|pfx|secret|credential|token)$'
```

The `(^|/)` prefix catches both root-level (`.env`) and nested (`config/secrets/.env`) paths.

If any matches: **stop**. Use `AskUserQuestion`:
> "These files may contain secrets and are about to be staged:
> [list the matched files]
> Do you want me to add them to .gitignore, stage them intentionally, or abort?"
> - Add to .gitignore → for each file: `echo "<filename>" >> .gitignore` → verify with `cat .gitignore` → re-run Step 1c → continue
> - Stage intentionally → continue (user accepts risk)
> - Abort → stop

---

### 1d — Warn if on main or master

If `$BRANCH` is `main` or `master`: use `AskUserQuestion`:
> "You're on `$BRANCH`. Pushing directly to main is permanent and bypasses review. Do you want to proceed with a direct push, or should I create a feature branch first?"
> - Proceed → continue to 1e
> - Create feature branch → `AskUserQuestion`: "What should the branch be named?" → `git checkout -b <name>` → set `BRANCH=<name>` → continue to 1e

---

### 1e — Check for partially staged files

```bash
git diff --cached --name-only
```

If files are listed: use `AskUserQuestion`:
> "You already have [N] file(s) staged:
> [list them]
> Do you want to commit only those staged files, or stage everything with `git add -A`?"
> - Staged only → skip Step 3 (`git add -A`)
> - Everything → proceed to Step 3 normally

---

### 1f — Check for large files

```bash
git status --short | awk '{print $2}' | xargs -I{} sh -c 'test -f "{}" && du -sh "{}"' 2>/dev/null | awk -F'\t' '$1 ~ /^[0-9]+(\.[0-9]+)?[MG]/ {print $1, $2}'
```

The regex `[0-9]+(\.[0-9]+)?[MG]` matches both `5M` and `1.2M` (macOS `du` outputs decimals).

If any file exceeds 1MB: use `AskUserQuestion`:
> "These files are unusually large for a git commit:
> [list with sizes]
> Large files bloat repo history permanently and are painful to remove. Add to .gitignore, proceed anyway, or skip them?"
> - Add to .gitignore → `echo "<file>" >> .gitignore` for each → continue
> - Proceed → continue
> - Skip → `git restore --staged <file>` if staged → continue

---

## Step 2 — Show status and diff

```bash
git status
git diff
git diff --staged
```

If nothing to commit and nothing staged → tell the user: "Nothing to commit. Working tree is clean." Stop.

Otherwise show the diff output to the user as context before staging.

---

## Step 3 — Stage changes

Based on Step 1e:
- If user chose staged-only or no partial staging was flagged: skip if already staged; otherwise `git add -A`
- If user chose staged-only: skip this step

---

## Step 4 — Generate and confirm commit message

Read the project's commit convention:
```bash
git log --oneline -5
```

Infer the convention (prefix style, casing, verb tense). Generate a message that describes what the change **does**, not which files changed.

**Always use `AskUserQuestion` — no exceptions:**
> "Proposed commit message:
>
> `[generated message]`
>
> Approve, reply with an edited version, or say 'abort' to cancel."
> - Approved → proceed to Step 5
> - Edited version provided → use that exact string → proceed to Step 5
> - Abort → stop. No commit made.

---

## Step 5 — Commit

Use a HEREDOC to avoid quoting issues with special characters:

```bash
git commit -m "$(cat <<'EOF'
[user-confirmed message]
EOF
)"
```

If commit fails: surface the full error. Do not retry silently.

---

## Step 6 — Detect remote

```bash
git remote
```

- Single remote → use it. Default to `origin` only if it is present by that name.
- Multiple remotes: use `AskUserQuestion`:
  > "Multiple remotes found: [list]. Which one should I push to?"

---

## Step 7 — Pull with rebase (skip if new branch)

Check if upstream tracking branch is set:
```bash
git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "NO_UPSTREAM"
```

**If `NO_UPSTREAM`: skip this step entirely.** The branch does not exist on remote yet — there is nothing to pull from. Proceed directly to Step 8, which will use the `-u` flag.

If upstream exists, sync before pushing:
```bash
git pull --rebase <remote> $BRANCH
```

**If rebase conflict:**

1. List conflicting files:
   ```bash
   git diff --name-only --diff-filter=U
   ```

2. Use `AskUserQuestion`:
   > "Rebase conflict in:
   > [list files]
   >
   > To continue: resolve the conflicts in those files, then run `git add <file>` to mark each resolved. Then say 'continue'.
   > To cancel: say 'abort' to return to your pre-pull state."

3. Wait. Do not proceed.
   - User says `abort` → `git rebase --abort` → stop
   - User says `continue` → `git rebase --continue`
     - If another conflict appears → repeat this AskUserQuestion block
     - If rebase completes → **proceed to Step 8**

---

## Step 8 — Push

```bash
git push [-u] <remote> $BRANCH
```

Use `-u` flag if Step 7 detected `NO_UPSTREAM`.

**If push fails:** show the full error and diagnose using this table. Match against the actual error output:

| Error pattern to look for | Diagnosis | Response |
|---|---|---|
| `non-fast-forward` or `fetch first` | Remote has commits you don't have | Use `AskUserQuestion`: "Remote has diverged. I need to pull and rebase before pushing. Want me to do that now?" If yes: `git pull --rebase <remote> $BRANCH` → retry push |
| `GH006` or `protected branch` or `refusing to allow` | Branch is protected on remote — direct push blocked | Use `AskUserQuestion`: "This branch is protected. Direct push is blocked. Do you want me to open a PR instead?" → see PR Creation below |
| `repository not found` or `does not appear to be a git repository` | Wrong remote URL | "Remote URL looks wrong. Run `git remote -v` to check the URL." |
| `authentication failed` or `could not read Username` | Credential issue | "Push failed due to authentication. Check your SSH key or personal access token is valid and not expired." |
| Any other error | Unknown failure | Show full error verbatim. Use `AskUserQuestion`: "Push failed with this error: [error]. How do you want to proceed?" |

**PR Creation** (when user says yes to opening a PR):
```bash
gh pr create --title "[user-confirmed commit message]" --fill
```
If `gh` is not installed: "The `gh` CLI is not installed. Open a PR manually at: `https://github.com/<remote-url>/compare/$BRANCH`"

---

## Step 9 — Confirm

```bash
git log --oneline -1
```

Output exactly:
```
Pushed: <hash> <commit message>
```

No em dash. Space between hash and message.

---

## When to Use AskUserQuestion

Stop and ask before proceeding when:
- Detached HEAD detected
- No `.gitignore` with untracked files present
- Sensitive file patterns found
- On `main` or `master` branch
- Files already partially staged
- Files larger than 1MB would be staged
- Multiple remotes exist
- **Commit message — always, no exceptions**
- Rebase conflict encountered (with `git add` reminder before continue)
- Push rejected or failed for any reason

**Never infer consent. Never proceed silently on irreversible actions.**

---

## Recovery Reference

| Situation | Command |
|---|---|
| Rebase conflict — want to cancel | `git rebase --abort` |
| Rebase conflict — resolved and staged | `git rebase --continue` |
| Wrong commit message (not yet pushed) | `git commit --amend` (opens editor) |
| Staged wrong files | `git restore --staged <file>` |
| Accidentally staged sensitive file | `git restore --staged <file>` then `echo "<file>" >> .gitignore` |
| Push rejected non-fast-forward | `git pull --rebase <remote> <branch>` then push again |
| Protected branch blocks push | `gh pr create --title "..." --fill` |
