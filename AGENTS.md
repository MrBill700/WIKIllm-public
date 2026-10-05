# Contributor and agent instructions

- Read README.md, SETUP.md and CLAUDE.md before editing.
- Keep raw/ source documents immutable. Put derived scratch files outside the vault.
- Use a task branch or worktree for code changes. Keep any checkout used as a sync source on clean main, equal to fetched origin/main.
- Before writing, inspect the repository root, branch, git status and worktree list. Confirm the checkout belongs to the current task.
- Ask the owner before merging a pull request, publishing a release, or applying changes to other vaults. Existing explicit authorization remains valid.
- Run relevant regression tests and describe verified behavior and remaining limitations.
- Do not commit source documents, credentials, personal paths, generated operational state, or private handoffs.
- For substantial work, keep an explicit checkbox status in the issue or pull request and state whether the owner needs to act.
- File project-wide defects in this repository after checking existing issues. Keep vault-specific questions and content debt within that vault.

No machine-local skills or private repositories are required to contribute.
