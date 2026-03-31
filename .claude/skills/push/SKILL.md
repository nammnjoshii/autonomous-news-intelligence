---
name: push
description: Use this skill whenever the user wants to commit and push changes — even if they say "push", "ship it", "commit this", "save and push", or just "done, push". Stages all changes, generates a commit message from the diff, commits, pushes to the current branch, and confirms with the commit hash. Invoke this proactively after completing any meaningful code change if the user seems ready to save work.
---

# Push Skill

Stage all changes, generate a commit message from the diff, commit, push, and confirm.

## Steps

1. **Check current branch and status**
   ```bash
   git branch --show-current
   git status
   ```

2. **Get the diff to generate a commit message from**
   ```bash
   git diff
   git diff --staged
   ```
   Read the diff. Infer the intent — what changed and why. Do not summarise file names mechanically ("updated main.py"). Describe what the change *does*.

3. **Stage all changes**
   ```bash
   git add -A
   ```

4. **Commit with a generated message**
   Follow the project's commit convention (check recent `git log --oneline -5` if unsure).
   For this project, use lowercase conventional commits:
   ```
   <type>: <what this change does>
   ```
   Types: `feat`, `fix`, `config`, `feeds`, `docs`, `chore`

   Write the message to reflect intent, not mechanics. Examples:
   - `fix: handle missing active_url when all pool URLs fail`
   - `docs: split field notes into worked/not-tried sections`
   - `chore: remove auto-commit step from daily workflow`

5. **Pull with rebase to sync before pushing**
   ```bash
   git pull --rebase origin <current-branch>
   ```
   This prevents the "fetch first" rejection if the remote has moved ahead.

6. **Push to current branch**
   ```bash
   git push origin <current-branch>
   ```

7. **Confirm with commit hash**
   ```bash
   git log --oneline -1
   ```
   Report the hash and commit message to the user.

## Output format

End with a single confirmation line:
```
Pushed: <hash> — <commit message>
```

## Notes

- If `git pull --rebase` produces conflicts, stop and surface them to the user — do not resolve automatically.
- If there is nothing to commit (`git status` shows clean), tell the user and stop.
- Never force push (`--force`) unless the user explicitly asks.
