---
name: git-workflow
description: Safe, clean git usage. Use whenever you commit, branch, push, pull, or resolve a merge conflict.
---

# Git workflow

## Before committing

1. Run `git status` and `git diff` so you know exactly what you're committing.
2. Stage specific files by name. Only use `git add -A` after checking the status.
3. Never commit secrets: `.env` files, keys, tokens, or credentials. If one is staged, unstage it and add it to `.gitignore`.
4. Run the program or its tests before committing.

## Commits

- One logical change per commit.
- Start the message with a summary line under 72 characters saying what changed and why. Add details after a blank line if needed.

## Branches and pushing

- Use a branch for anything non-trivial: `git switch -c <short-name>`.
- Pull before pushing: `git pull --rebase`.
- Never force-push a shared branch, and never rewrite history others may have pulled.

## Conflicts

- Read both sides of each conflict and keep what both changes intended, not just one side.
- Run the tests after resolving, before committing the merge.

## Ask first

- Before deleting branches, `git reset --hard`, `git clean`, or anything else that discards uncommitted work.
- Before pushing to a remote for the first time.
