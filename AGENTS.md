# Project working instructions

## Issues and pull requests

GitHub issues in `jublaze/llm-diplomacy` are the work tracker. Check for an existing issue before creating one, and link each issue-driven change to its issue. For a logged issue, agents are authorized to create a scoped topic branch, commit and push the tested change, and open a linked pull request without repeated chat permission. Keep unrelated work and experiment artifacts out of the branch.

This authorization does not grant pull-request approval or merge, export of credentials, publication of unrelated data, or permission to bypass tool approval checks. If an approval review denies an action, report the denial and do not retry through another route without resolving it. Follow the project's existing independent-review requirements and any more specific owner instructions.

## Implementation and safety

Keep rules, adjudication, exact evidence lookups and calculations in deterministic code. Use offline fake or mock providers for validation unless the owner explicitly authorizes a bounded live run. Do not make paid inference without explicit authorization. Isolate changes from active experiment writers; preserve their source identities, checkpoints, history and accounting.

For code changes, run relevant tests and `framework/scripts/security_scan.ps1`, address findings, summarize changed files and risk, and decide whether the scoped branch is ready to push. Stage intentional files only. Never commit or push keys, tokens, credentials or raw sensitive experiment artifacts.

## Experiment completion and viewer

At experiment completion, generate or update the viewer data and validate it against the completed run IDs and hashes. Check the deployed viewer's freshness against those same identifiers and hashes. If generation, approval or publication is blocked, record an issue or completion follow-up; do not claim the viewer is current until verified.

The previously reviewed `luna1912-1018-r2` full postgame release and Pages deployment are authorized within the normalized-data boundary in [publication/README.md](publication/README.md). Preserve the standard map's CC BY-SA attribution. That approval does not authorize publication of private artifacts from ongoing or new experiments. Obtain explicit approval for sensitive or otherwise unapproved datasets before publishing; do not silently update existing public data.
