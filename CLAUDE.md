@AGENTS.md

Claude-only notes:

- Use plan mode for the SQL functions, the selection rule and the decision engine.
- One session per git worktree (`claude -w <branch>`), one to three per person, never two on one branch.
- `.claude/settings.json` denies reads of `.env` files and edits to generated contract code. Do not work around it.
- Before opening a pull request, run the commands in AGENTS.md for the area you touched and paste their output into the PR.
- When a design question is not answered by `docs/Dialback_System_Design.pdf` or the folder's README, write it into the spec as an open question instead of guessing.
