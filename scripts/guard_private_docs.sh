#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# guard-private-docs (F14.AC8, CLAUDE.md invariant 8)
#
# docs/private/ is an uncommitted explainer track for the repository owner.
# Accidentally committing it is the only real failure mode, so it is protected
# three ways:
#
#   1. docs/private/ in the root .gitignore
#   2. docs/private/.gitignore containing "*" and "!.gitignore" — self-ignoring
#      even if the root file is edited or replaced
#   3. this script, run in CI, which FAILS THE BUILD if anything under
#      docs/private/ is tracked by git
#
# Layer 3 exists because layers 1 and 2 are both defeated by `git add -f`.
# ---------------------------------------------------------------------------
set -eu

tracked=$(git ls-files -- 'docs/private' 'docs/private/**' 2>/dev/null || true)

if [ -n "$tracked" ]; then
    printf '\033[31mFAIL: docs/private/ must never be committed.\033[0m\n' >&2
    echo "" >&2
    echo "The following paths are tracked by git and must be removed:" >&2
    echo "$tracked" | sed 's/^/  /' >&2
    echo "" >&2
    echo "Remove them from the index, keeping the files on disk:" >&2
    echo "" >&2
    echo "  git rm --cached -r docs/private" >&2
    echo "" >&2
    echo "Then confirm docs/private/ is present in .gitignore." >&2
    exit 1
fi

printf '\033[32mOK: nothing under docs/private/ is tracked.\033[0m\n'
