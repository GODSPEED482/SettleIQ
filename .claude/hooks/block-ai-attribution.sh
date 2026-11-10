#!/bin/sh
# PreToolUse hook (Bash). Denies git commits and GitHub PR commands whose text
# carries AI-agent attribution: Co-Authored-By trailers naming an AI tool,
# Claude-Session trailers, "Generated with Claude Code" lines or session links.
# Human Co-Authored-By trailers are allowed.
cmd=$(jq -r '.tool_input.command // ""')

case "$cmd" in
  *"git "*"commit"*|*"gh pr create"*|*"gh pr edit"*|*"gh pr merge"*) ;;
  *) exit 0 ;;
esac

ai='claude|anthropic|openai|codex|chatgpt|gpt-[0-9]|copilot|cursor|gemini|devin|aider|windsurf|codeium'
if printf '%s' "$cmd" | grep -qiE "co-authored-by:[^\"'\n]*($ai)|claude-session:|generated with \[?claude|claude\.ai/code/session|claude\.com/claude-code"; then
  jq -n '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: "This repo forbids AI attribution in commits and PRs: no Co-Authored-By trailers naming an AI agent, no Claude-Session trailers, no \"Generated with Claude Code\" lines or session links. Remove them and run the command again."}}'
fi
exit 0
