---
description: Enable the repo's git hooks that strip AI attribution from commit messages
allowed-tools: Bash(git config:*), Bash(chmod:*), Bash(ls:*)
---

Enable the versioned git hooks in `.githooks/` for this clone:

1. `chmod +x .githooks/*`
2. `git config core.hooksPath .githooks`
3. Confirm with `git config --get core.hooksPath` and `ls -l .githooks`, then tell the user that every commit made in this clone, by a person or any AI agent, now has AI `Co-Authored-By:` / `Claude-Session:` trailers and "Generated with Claude Code" lines removed automatically.
