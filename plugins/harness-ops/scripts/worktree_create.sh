#!/usr/bin/env bash
# WorktreeCreate: agent worktrees go beside the repository (<repo>-worktrees/<name>), not inside it.
# Inside (.claude/worktrees, gitignored) the LSP drops every result from an ignored path, and the main checkout's
# grep/Glob/ruff would see every agent's copy. Branch worktree-<name> from HEAD. Prints the path on stdout.
# Refuses (nothing created): a bad name, an existing non-empty folder, or an existing branch holding commits HEAD lacks.
set -euo pipefail
fail() { echo "worktree-create: $*" >&2; exit 1; }
input=$(cat)
name=$(jq -r '.name // ""' <<<"$input"); cwd=$(jq -r '.cwd // ""' <<<"$input")
[[ "$name" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*$ && "$name" != *..* ]] || fail "bad worktree name '$name'"
common=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || fail "'$cwd' is not in a git repository"
main=$(dirname "$common")
dir="${DEVKIT_WORKTREES:-$(dirname "$main")/$(basename "$main")-worktrees}/$name"
branch="worktree-$name"
[[ -e "$dir" && -n "$(ls -A "$dir" 2>/dev/null)" ]] && fail "$dir exists and is not empty"
if git -C "$cwd" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
  n=$(git -C "$cwd" rev-list --count "HEAD..refs/heads/$branch")
  [[ "$n" == 0 ]] || fail "branch $branch has $n unmerged commit(s): integrate or delete it first"
fi
mkdir -p "$(dirname "$dir")"
git -C "$cwd" worktree add --no-track -B "$branch" "$dir" HEAD >&2 || fail "git worktree add failed"
# Ignored files a worktree needs (gitignore syntax list, like Claude Code's own .worktreeinclude).
if [[ -f "$main/.worktreeinclude" ]]; then
  (cd "$main" && git ls-files --others --ignored --exclude-from=.worktreeinclude 2>/dev/null) | while read -r f; do
    mkdir -p "$dir/$(dirname "$f")" && cp -p "$main/$f" "$dir/$f"
  done
fi
echo "$dir"
