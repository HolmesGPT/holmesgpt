#!/usr/bin/env bash
# Rebuild every version listed in gh-pages' versions.json with the docs toolchain installed here, each from its own tag (dev from HEAD), and commit each to the local gh-pages branch with mike.
# Titles and aliases are kept: mike preserves them when a version is redeployed without them.
# Pushing is left to the caller (git push origin gh-pages).
#
# Usage: scripts/rebuild-published-docs.sh   (run with mike on PATH, e.g. under poetry run)
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
worktrees=$(mktemp -d)
trap 'rm -rf "$worktrees"; git -C "$repo" worktree prune' EXIT

versions=$(git -C "$repo" show origin/gh-pages:versions.json |
    python3 -c 'import json, sys; print("\n".join(v["version"] for v in json.load(sys.stdin)))')

for version in $versions; do
    if [ "$version" = dev ]; then ref=HEAD; else ref="refs/tags/$version"; fi
    dir="$worktrees/$version"
    git -C "$repo" worktree add --quiet --detach "$dir" "$ref"
    echo "::group::$version ($(git -C "$dir" rev-parse --short HEAD))"
    # mkdocs.yml loads docs.custom_fences by module name, which otherwise resolves to the installed checkout's copy; older tags' mkdocs.yml name fence functions that copy lacks or renders differently.
    (cd "$dir" && PYTHONPATH="$dir" mike deploy "$version")
    echo "::endgroup::"
    git -C "$repo" worktree remove --force "$dir"
done
