# Clarifying Questions — Round 1

## Q1: Workflow Scope
**Which CI-triggered workflows should we wire up?**
→ **Planning pipeline on all issues.** Replace triage-only with the full planning-pipeline (triage → decompose → summarize) on every new issue.

## Q2: Bug-fix Autonomy
**Should the bug-fix workflow create PRs automatically?**
→ **Diagnose + create PR.** Agent diagnoses the bug and opens a fix PR with the agent-proposed label for human review.

## Q3: Bug-fix Trigger
**How should the bug-fix workflow be triggered?**
→ **Label trigger.** GitHub Actions issues:labeled event fires when category:bug label is applied by triage.
