#!/usr/bin/env bash
# r1-github-tools probes (#346). Read-only: repository settings, Actions run outcomes, ETag polling cost.
# Usage: bash github_env_probe.sh [owner/repo ...]  (default: the two harness-run repos)
set -euo pipefail
owner=Marius-Juston
repos=("${@:-$owner/agent-harness $owner/${REPO_E:-repo-E}}")  # repo-E is private: set REPO_E to its name
for r in ${repos[@]}; do
  echo "== $r"
  gh api "repos/$r" --jq '{private, owner_type: .owner.type, allow_auto_merge, delete_branch_on_merge}'
  gh api "repos/$r/branches/main/protection" --jq '{checks: .required_status_checks.contexts}' 2>/dev/null || echo "no protection readable"
  gh api graphql -f query="{repository(owner:\"${r%/*}\",name:\"${r#*/}\"){issueTypes(first:5){nodes{name}}}}" --jq '.data.repository.issueTypes'
  gh run list -R "$r" -L 40 --json event,conclusion \
    --jq 'group_by(.event)[] | {event: .[0].event, runs: length, success: map(select(.conclusion=="success"))|length}'
  # ETag conditional poll: a 304 should leave X-Ratelimit-Remaining unchanged
  et=$(gh api -i "repos/$r/events?per_page=1" | tr -d '\r' | awk -F': ' 'tolower($1)=="etag"{print $2}')
  for i in 1 2; do
    gh api -i -H "If-None-Match: $et" "repos/$r/events?per_page=1" 2>/dev/null | tr -d '\r' \
      | grep -i '^HTTP\|^x-ratelimit-remaining\|^x-poll-interval' || true
  done
done
