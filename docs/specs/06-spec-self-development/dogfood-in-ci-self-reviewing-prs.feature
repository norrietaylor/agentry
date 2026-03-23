# Source: docs/specs/06-spec-self-development/06-spec-self-development.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Dogfood in CI -- Self-Reviewing PRs

  Scenario: GitHub Actions workflow file exists and is valid YAML
    Given the agentry repository has been set up with CI dogfooding
    When the file ".github/workflows/agentry-code-review.yml" is parsed as YAML
    Then the YAML is valid and contains no syntax errors
    And the workflow triggers on "pull_request" events

  Scenario: CI workflow invokes agentry run with correct arguments
    Given the file ".github/workflows/agentry-code-review.yml" exists
    When the workflow YAML is inspected
    Then it contains a step that runs "agentry run workflows/code-review.yaml" with diff, codebase, and binder arguments
    And it references the "ANTHROPIC_API_KEY" secret

  Scenario: CI workflow posts PR comment with findings
    Given a pull request is opened against the agentry repository
    And the "agentry-code-review" GitHub Actions workflow runs successfully
    When the workflow completes
    Then a comment is posted on the pull request containing code review findings from the agentry workflow

  Scenario: Dry-run CI generation produces matching workflow output
    Given the agentry CLI is installed
    When the user runs "agentry ci generate --target github --dry-run workflows/code-review.yaml"
    Then the command exits with code 0
    And stdout contains valid YAML for a GitHub Actions workflow
    And the generated workflow includes a "pull_request" trigger
    And the generated workflow includes a step invoking "agentry run"

  Scenario: CI workflow uses minimal GitHub token permissions
    Given the file ".github/workflows/agentry-code-review.yml" exists
    When the workflow YAML is inspected for permissions
    Then the workflow declares explicit permissions scoped to the minimum required by the code-review tool manifest
    And no write permissions beyond "pull-requests: write" are granted
