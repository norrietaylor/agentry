# Source: docs/specs/08-spec-ci-self-development-loop/08-spec-ci-self-development-loop.md
# Pattern: CLI/Process
# Recommended test type: Integration

Feature: Label-Triggered Bug-Fix CI Workflow

  Scenario: Bug-fix CI workflow triggers when category:bug label is applied
    Given the GitHub Actions workflow file ".github/workflows/agentry-bug-fix.yml" is deployed
    When the label "category:bug" is applied to an issue
    Then the agentry-bug-fix workflow is triggered
    And the workflow runs the command "agentry --output-format json run workflows/bug-fix.yaml --input repository-ref=. --binder github-actions"

  Scenario: Bug-fix CI workflow does not trigger on other labels
    Given the GitHub Actions workflow file ".github/workflows/agentry-bug-fix.yml" is deployed
    When the label "category:feature" is applied to an issue
    Then the agentry-bug-fix workflow is not triggered

  Scenario: Bug-fix CI workflow has correct permissions for PR creation
    Given the GitHub Actions workflow file ".github/workflows/agentry-bug-fix.yml" is deployed
    When the workflow is parsed by GitHub Actions
    Then the workflow declares "contents: write" permission
    And the workflow declares "issues: write" permission
    And the workflow declares "pull-requests: write" permission

  Scenario: Bug-fix CI workflow requires expected secrets
    Given the GitHub Actions workflow file ".github/workflows/agentry-bug-fix.yml" is deployed
    When the workflow configuration is inspected
    Then the workflow references the "CLAUDE_CODE_OAUTH_TOKEN" secret
    And the workflow references the "GITHUB_TOKEN" secret

  Scenario: Bug-fix CI workflow resolves issue body via source mapping without explicit input
    Given the GitHub Actions workflow file ".github/workflows/agentry-bug-fix.yml" is deployed
    And the bug-fix workflow YAML has source mapping configured for issue-description
    When the workflow runs against an issue with body "NullPointerException in parser"
    Then the agent receives "NullPointerException in parser" as the issue description without an explicit --input flag

  Scenario: Bug-fix CI workflow follows established workflow structure
    Given the GitHub Actions workflow file ".github/workflows/agentry-bug-fix.yml" is deployed
    And the existing code-review workflow at ".github/workflows/agentry-code-review.yml"
    When both workflow files are compared structurally
    Then the bug-fix workflow follows the same job structure pattern as the code-review workflow
    And both workflows use the same checkout and agentry setup steps
