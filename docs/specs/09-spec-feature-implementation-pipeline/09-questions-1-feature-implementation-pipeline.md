# Clarifying Questions — Round 1

## Q1: Trigger
**What should trigger the feature implementation workflow?**
→ **category:feature label.** Same pattern as bug-fix — triage applies category:feature, which triggers the implementation workflow.

## Q2: Autonomy
**How much autonomy should the feature agent have?**
→ **Implement + PR.** Agent reads the decomposed tasks from planning-pipeline, implements the feature, and opens a PR.

## Q3: Input
**Should the feature agent use task-decompose output or re-analyze independently?**
→ **Use decomposed tasks.** Read the planning-pipeline output (triage + decomposed tasks) from the issue comments as context.

## Q4: Guardrails
**What scope limits should the feature agent have?**
→ **Create scoped sub-issues if the feature is bigger than the blast radius.** Agent self-assesses scope; if too large, it creates focused sub-issues rather than attempting a monolithic PR.
