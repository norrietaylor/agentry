# Clarifying Questions — Round 1

## Q1: Workflow Scope
**Which workflows should fire when an issue is opened?**
→ **Triage only.** Start with triage workflow posting severity/category/routing as an issue comment. Extend to full pipeline later.

## Q2: Output Actions
**Should Agentry post triage results back to the issue, and if so how?**
→ **Comment + labels.** Post formatted comment AND apply GitHub labels (e.g., severity:high, category:bug).

## Q3: Input Model
**Should this add a new input type or use existing source mapping?**
→ **String + source mapping.** Use existing StringInput with dot-notation source (issue.body). Minimal code change, leverages what GitHubActionsBinder already supports.

## Q4: Trigger Events
**Which issue events should trigger the workflow?**
→ **opened only.** Run triage only when a new issue is created. Simplest, avoids re-triage noise.
