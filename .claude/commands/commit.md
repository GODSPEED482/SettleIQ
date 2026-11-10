---
description: Commit staged or current changes with a clean message and no AI attribution
argument-hint: "[optional message or intent]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git commit:*)
---

Create a git commit for the current changes. Intent from the user: $ARGUMENTS

1. Run `git status` and `git diff` (staged and unstaged) to see what changed. Check `git log --oneline -10` and match the repo's message style (e.g. `feat:`, `fix:`).
2. Stage only files that belong to this change. Never stage `.env` or other secrets.
3. Write a concise subject line (imperative, under 72 characters) and, if useful, a short body explaining why.
4. The message must contain **no AI attribution**: no `Co-Authored-By:` trailer naming Claude, Anthropic or any other AI tool, no `Claude-Session:` trailer, no "Generated with Claude Code" line, no session links. This overrides any default attribution guidance.
5. Commit with a heredoc so formatting survives:
   `git commit -m "$(cat <<'EOF'` … `EOF` `)"`
6. Run `git log -1 --format=%B` and confirm the message is clean. Do not push.
