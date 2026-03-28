# Source: docs/specs/08-spec-ci-self-development-loop/08-spec-ci-self-development-loop.md
# Pattern: CLI/Process + Async
# Recommended test type: Integration

Feature: Planning Pipeline CI Workflow

  Scenario: Planning pipeline workflow triggers on new issues
    Given the GitHub Actions workflow file ".github/workflows/agentry-planning-pipeline.yml" is deployed
    When a new issue is opened in the repository
    Then the agentry-planning-pipeline workflow is triggered
    And the workflow runs the command "agentry --output-format json run workflows/planning-pipeline.yaml --input repository-ref=. --binder github-actions"

  Scenario: Planning pipeline resolves issue description from source mapping
    Given the planning-pipeline workflow YAML defines input "issue-description" with source "issue.body" and fallback "issue.title"
    When the pipeline runs against an issue with body "Login returns 500 on empty password"
    Then the triage step receives "Login returns 500 on empty password" as the issue description
    And the triage step produces a severity and category classification

  Scenario: Planning pipeline posts separate comments for each step
    Given a newly opened issue triggers the planning pipeline
    When the pipeline completes all three steps (triage, task-decompose, summary)
    Then three separate comments are posted on the issue
    And the first comment contains the triage classification with severity and category
    And the second comment contains the task decomposition
    And the third comment contains the planning summary

  Scenario: Triage step applies severity and category labels to the issue
    Given a newly opened issue triggers the planning pipeline
    When the triage step completes and classifies the issue as severity "high" and category "bug"
    Then the label "severity:high" is applied to the issue
    And the label "category:bug" is applied to the issue

  Scenario: Planning pipeline workflow has correct permissions
    Given the GitHub Actions workflow file ".github/workflows/agentry-planning-pipeline.yml" is deployed
    When the workflow is parsed by GitHub Actions
    Then the workflow declares "contents: read" permission
    And the workflow declares "issues: write" permission

  Scenario: Planning pipeline YAML includes issue tools in capabilities
    Given the planning-pipeline workflow YAML at "workflows/planning-pipeline.yaml"
    When agentry validates the workflow definition
    Then the workflow capabilities include "issue:comment"
    And the workflow capabilities include "issue:label"
