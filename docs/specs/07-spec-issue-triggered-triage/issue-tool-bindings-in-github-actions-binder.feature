# Source: docs/specs/07-spec-issue-triggered-triage/07-spec-issue-triggered-triage.md
# Pattern: API + CLI/Process
# Recommended test type: Integration

Feature: Issue Tool Bindings in GitHubActionsBinder

  Scenario: issue:comment tool posts a comment to the triggering issue
    Given a GitHubActionsBinder configured with an issues event payload for issue number 42
    And the workflow declares "issue:comment" in tools.capabilities
    When the issue:comment tool is invoked with the message "Triage complete: severity high"
    Then a POST request is sent to /repos/{owner}/{repo}/issues/42/comments
    And the request body contains "Triage complete: severity high"
    And the tool invocation returns a success status

  Scenario: issue:label tool applies labels to the triggering issue
    Given a GitHubActionsBinder configured with an issues event payload for issue number 42
    And the workflow declares "issue:label" in tools.capabilities
    When the issue:label tool is invoked with labels "severity:high" and "category:bug"
    Then a POST request is sent to /repos/{owner}/{repo}/issues/42/labels
    And the request body contains the labels "severity:high" and "category:bug"
    And the tool invocation returns a success status

  Scenario: issue:label tool creates labels that do not yet exist in the repository
    Given a GitHubActionsBinder configured with an issues event payload
    And the repository has no label named "severity:critical"
    When the issue:label tool is invoked with label "severity:critical"
    Then a POST request is sent to the labels endpoint
    And the label "severity:critical" is created and applied to the issue

  Scenario: Issue number is extracted from the issues event payload
    Given an issues event payload with issue number 99
    When the GitHubActionsBinder processes the event payload for tool binding
    Then the issue:comment tool targets issue number 99
    And the issue:label tool targets issue number 99

  Scenario: issue:comment tool raises error outside issues event context
    Given a GitHubActionsBinder configured with a pull_request event payload
    When the issue:comment tool is invoked
    Then the tool raises an error indicating it requires an issues event context
    And the error message clearly identifies the wrong event type

  Scenario: issue:label tool raises error outside issues event context
    Given a GitHubActionsBinder configured with a workflow_dispatch event payload
    When the issue:label tool is invoked with labels "severity:low"
    Then the tool raises an error indicating it requires an issues event context

  Scenario: bind_tools wires issue tools when declared in workflow capabilities
    Given a workflow definition with tools.capabilities containing "issue:comment" and "issue:label"
    And a GitHubActionsBinder configured with an issues event payload
    When bind_tools is called on the binder
    Then the bound tool set includes callable handlers for "issue:comment" and "issue:label"
