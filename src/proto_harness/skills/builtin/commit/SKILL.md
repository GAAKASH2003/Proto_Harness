---
name: commit
description: Inspect git status and diffs to create clean conventional commits
---
# Conventional Git Commit Workflow

When creating a commit, follow these rules:

1. **Inspect Changes**:
   - Run `git status` to see unstaged and staged files.
   - Run `git diff` to inspect exact changes.

2. **Stage Intelligently**:
   - Stage relevant files using `git add <files>`.
   - Avoid staging unintended files (e.g., temporary files, secrets, `.env`).

3. **Format Commit Message**:
   Follow Conventional Commits:
   - `feat: <short summary>`
   - `fix: <short summary>`
   - `refactor: <short summary>`
   - `docs: <short summary>`
   - `test: <short summary>`

4. **Execute**:
   - Run `git commit -m "<message>"`.
   - Confirm with `git status` or `git log -1`.
