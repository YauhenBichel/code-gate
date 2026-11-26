---
description: Review the files changed in this session with code-gate, static checks plus the local model
---

Review the code written in this session.

1. List the source files you created or changed in this session (or the files named below, if any).
2. Call the `review_files` tool of the code-gate server with those absolute paths and `deep: true`.
3. Fix every `high` finding. For `medium` findings, fix the real ones and say in one line why you leave any other.
4. Findings marked `model` come from a local model and can be wrong: read the line before you act on one.
5. Call `review_files` again (without `deep`) to confirm, then report: what was found, what was fixed, what was left and why.

$ARGUMENTS
