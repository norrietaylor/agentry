# Source: docs/specs/04-spec-agentry-ci/04-spec-agentry-ci.md
# Pattern: CLI/Process + Error handling
# Recommended test type: Integration

Feature: agentry ci generate Command

  Scenario: Generate GitHub Actions YAML for a workflow with default trigger
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github workflows/code-review.yaml"
    Then the command exits with code 0
    And a file is created at ".github/workflows/agentry-code-review.yaml"
    And the generated YAML contains a "name" field derived from the workflow identity
    And the generated YAML "on" section includes "pull_request" as the trigger
    And the generated YAML jobs section includes checkout, setup-python, install agentry, and run agentry steps

  Scenario: Generate YAML with multiple triggers including schedule
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github --triggers pull_request,schedule --schedule '0 2 * * 1' workflows/code-review.yaml"
    Then the command exits with code 0
    And the generated YAML "on" section includes both "pull_request" and "schedule" triggers
    And the schedule trigger has cron expression "0 2 * * 1"

  Scenario: Generate YAML with custom output directory
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github --output-dir ci/workflows/ workflows/code-review.yaml"
    Then the command exits with code 0
    And a file is created at "ci/workflows/agentry-code-review.yaml"

  Scenario: Dry-run prints YAML to stdout without writing file
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github --dry-run workflows/code-review.yaml"
    Then the command exits with code 0
    And stdout contains valid YAML with a "name" field and "jobs" section
    And no file is created in ".github/workflows/"

  Scenario: Generated YAML includes minimal permissions from tool manifest
    Given a workflow definition declaring tools "repository:read" and "pr:comment"
    When the user runs "agentry ci generate --target github workflows/code-review.yaml"
    Then the generated YAML permissions section includes "contents: read"
    And the generated YAML permissions section includes "pull-requests: write"
    And no additional permissions beyond what the tools require are declared

  Scenario: Generated YAML injects secrets as environment variables
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github workflows/code-review.yaml"
    Then the generated YAML run step environment includes "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}"
    And the generated YAML run step environment includes "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}"

  Scenario: Generated YAML uses ubuntu-latest as default runner
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github workflows/code-review.yaml"
    Then the generated YAML jobs.agentry.runs-on is "ubuntu-latest"

  Scenario: Composed workflow is rejected with clear error
    Given a workflow definition with a non-empty "composition.steps" block
    When the user runs "agentry ci generate --target github workflows/composed.yaml"
    Then the command exits with a non-zero code
    And stderr contains "Composed workflow CI generation is not yet supported"
    And stderr contains "Generate CI config for each component workflow individually"

  Scenario: Schedule trigger without --schedule flag produces error
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github --triggers schedule workflows/code-review.yaml"
    Then the command exits with a non-zero code
    And stderr contains an error indicating --schedule is required when "schedule" is in triggers

  Scenario: Generated run step invokes agentry run with correct arguments
    Given a valid workflow definition file at "workflows/code-review.yaml"
    When the user runs "agentry ci generate --target github workflows/code-review.yaml"
    Then the generated YAML run step command includes "agentry run" with the workflow path
