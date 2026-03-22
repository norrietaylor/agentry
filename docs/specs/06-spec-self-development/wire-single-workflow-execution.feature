# Source: docs/specs/06-spec-self-development/06-spec-self-development.md
# Pattern: CLI/Process + Error Handling
# Recommended test type: Integration

Feature: Wire Single-Workflow Execution

  Scenario: Triage workflow returns structured JSON output
    Given a workflow file "workflows/triage.yaml" exists with a valid agent configuration
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Login fails on Safari' --input repository-ref=. --target . --output-format json"
    Then the command exits with code 0
    And stdout contains valid JSON with a "status" field
    And the JSON output contains an "output" field with classification content
    And the JSON output contains a "token_usage" field

  Scenario: Code-review workflow returns findings in JSON format
    Given a workflow file "workflows/code-review.yaml" exists with a valid agent configuration
    And the ANTHROPIC_API_KEY environment variable is set
    And the target repository has at least one commit
    When the user runs "agentry run workflows/code-review.yaml --input diff='$(git diff HEAD~1)' --input codebase=. --target . --output-format json"
    Then the command exits with code 0
    And stdout contains valid JSON with a "findings" array

  Scenario: Text output format produces human-readable summary
    Given a workflow file "workflows/triage.yaml" exists with a valid agent configuration
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Button misaligned' --input repository-ref=. --target . --output-format text"
    Then the command exits with code 0
    And stdout contains a human-readable summary without raw JSON braces

  Scenario: Execution record is written after successful run
    Given a workflow file "workflows/triage.yaml" exists with a valid agent configuration
    And the ANTHROPIC_API_KEY environment variable is set
    And the ".agentry/runs/" directory is empty or does not exist
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Test issue' --input repository-ref=. --target . --output-format json"
    Then the command exits with code 0
    And a new directory appears under ".agentry/runs/" with a timestamp-based name
    And the directory contains an "execution-record.json" file with run metadata

  Scenario: Skip-preflight flag is respected during execution
    Given a workflow file "workflows/triage.yaml" exists with a valid agent configuration
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Test' --input repository-ref=. --target . --skip-preflight --output-format json"
    Then the command exits with code 0
    And preflight checks are not executed before the agent runs

  Scenario: Missing agent binary produces informative error
    Given a workflow file "workflows/triage.yaml" exists with a valid agent configuration
    And the "claude" binary is not on PATH
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Test' --input repository-ref=. --target . --output-format json"
    Then the command exits with a non-zero exit code
    And stderr contains an error message indicating the agent binary was not found

  Scenario: Agent execution timeout produces informative error
    Given a workflow file with agent timeout set to 1 second
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run" with that workflow and valid inputs
    Then the command exits with a non-zero exit code
    And stderr contains an error message indicating the agent execution timed out
