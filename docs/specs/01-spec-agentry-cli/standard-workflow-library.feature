# Source: docs/specs/01-spec-agentry-cli/01-spec-agentry-cli.md
# Pattern: CLI/Process
# Recommended test type: Integration

Feature: Standard Workflow Library

  Scenario: Code review workflow validates successfully
    Given the workflows directory contains "code-review.yaml"
    When the user runs "agentry validate workflows/code-review.yaml"
    Then the command exits with code 0
    And stdout contains "Validation successful"

  Scenario: Bug fix workflow validates successfully
    Given the workflows directory contains "bug-fix.yaml"
    When the user runs "agentry validate workflows/bug-fix.yaml"
    Then the command exits with code 0
    And stdout contains "Validation successful"

  Scenario: Triage workflow validates successfully
    Given the workflows directory contains "triage.yaml"
    When the user runs "agentry validate workflows/triage.yaml"
    Then the command exits with code 0
    And stdout contains "Validation successful"

  Scenario: Code review workflow produces structured output with findings
    Given a git repository with changes between HEAD~1 and HEAD
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1" against the repository
    Then the output contains a "findings" array with entries having file, line, severity, category, and description fields
    And the output contains a "summary" string
    And the output contains a "confidence" numeric score
    And the findings count does not exceed the max_findings budget of 10

  Scenario: Triage workflow produces classification output
    Given the ANTHROPIC_API_KEY environment variable is set
    And a git repository as the target
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Login page returns 500 error when email contains a plus sign'"
    Then the output contains "severity" with value matching one of critical, high, medium, or low
    And the output contains "category", "affected_components", "recommended_assignee", and "reasoning" fields

  Scenario: Each workflow references a system prompt file that exists
    Given the workflows directory contains code-review.yaml, bug-fix.yaml, and triage.yaml
    When the model.system_prompt path is read from each workflow definition
    Then each referenced prompt file exists in "workflows/prompts/"
    And each file contains non-empty content

  Scenario: Bug fix workflow accepts issue description and produces diagnosis
    Given the ANTHROPIC_API_KEY environment variable is set
    And a git repository as the target
    When the user runs "agentry run workflows/bug-fix.yaml --input issue-description='NullPointerException in UserService.getProfile'"
    Then the output contains "diagnosis", "root_cause", and "suggested_fix" fields
    And the "suggested_fix" contains file, line, and change sub-fields
    And the output contains a "confidence" score
