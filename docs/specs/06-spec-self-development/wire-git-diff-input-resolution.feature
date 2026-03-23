# Source: docs/specs/06-spec-self-development/06-spec-self-development.md
# Pattern: CLI/Process + Error Handling
# Recommended test type: Integration

Feature: Wire Git-Diff Input Resolution

  Scenario: Git ref input resolves to actual diff content
    Given a workflow file "workflows/code-review.yaml" with an input declared as type "git-diff"
    And the target directory is a git repository with at least two commits
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1 --input codebase=. --target ."
    Then the command exits with code 0
    And the agent receives the resolved diff content from "git diff HEAD~1" rather than the literal string "HEAD~1"

  Scenario: Branch range ref resolves to diff between branches
    Given a workflow file with a "git-diff" type input
    And the target directory is a git repository with branches "main" and "feature"
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=main..feature --input codebase=. --target ."
    Then the command exits with code 0
    And the agent receives the diff between "main" and "feature" branches

  Scenario: Raw diff text is passed through when value is not a git ref
    Given a workflow file with a "git-diff" type input
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs the workflow with "--input diff='--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new'"
    Then the command exits with code 0
    And the agent receives the raw diff text as provided

  Scenario: Invalid git ref falls back to raw text treatment
    Given a workflow file with a "git-diff" type input
    And the target directory is a git repository
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs the workflow with "--input diff=not-a-valid-ref-xyz"
    Then the agent receives the literal string "not-a-valid-ref-xyz" as the diff input
    And no error is raised about the invalid ref

  Scenario: Non-git directory with git-diff input raises clear error
    Given a workflow file with a "git-diff" type input
    And the target directory is not a git repository
    When the user runs the workflow with "--input diff=HEAD~1 --target /tmp/not-a-repo"
    Then the command exits with a non-zero exit code
    And stderr contains an error message indicating the target directory is not a git repository

  Scenario: Target flag specifies the repository directory for diff resolution
    Given a workflow file with a "git-diff" type input
    And a git repository exists at "/tmp/test-repo" with commits
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1 --input codebase=. --target /tmp/test-repo"
    Then the diff is resolved by running "git diff HEAD~1" inside "/tmp/test-repo"
    And the command exits with code 0
