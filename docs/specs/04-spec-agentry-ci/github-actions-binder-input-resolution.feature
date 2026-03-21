# Source: docs/specs/04-spec-agentry-ci/04-spec-agentry-ci.md
# Pattern: API + CLI/Process + Error handling
# Recommended test type: Unit

Feature: GitHubActionsBinder -- Input Resolution

  Scenario: Repository-ref input resolves to GITHUB_WORKSPACE path
    Given a workflow definition with a "repository-ref" input
    And the environment variable GITHUB_WORKSPACE is set to "/home/runner/work/my-repo/my-repo"
    When the GitHubActionsBinder resolves inputs for the workflow
    Then the "repository-ref" input value is "/home/runner/work/my-repo/my-repo"

  Scenario: Git-diff input fetches PR diff from GitHub API
    Given a workflow definition with a "git-diff" input
    And the environment variable GITHUB_EVENT_PATH points to a pull_request event payload with PR number 42
    And the environment variable GITHUB_REPOSITORY is set to "owner/repo"
    And the environment variable GITHUB_TOKEN is set to a valid token
    When the GitHubActionsBinder resolves inputs for the workflow
    Then a GET request is made to "/repos/owner/repo/pulls/42" with Accept header "application/vnd.github.diff"
    And the "git-diff" input value contains the PR diff content

  Scenario: String input resolves from workflow_dispatch inputs
    Given a workflow definition with a "string" input named "review-scope"
    And the environment variable GITHUB_EVENT_NAME is set to "workflow_dispatch"
    And the event payload contains inputs with "review-scope" set to "full"
    When the GitHubActionsBinder resolves inputs for the workflow
    Then the "review-scope" input value is "full"

  Scenario: String input resolves from event payload field mapping
    Given a workflow definition with a "string" input mapped to "issue.title"
    And the environment variable GITHUB_EVENT_NAME is set to "issues"
    And the event payload contains an issue with title "Bug: login broken"
    When the GitHubActionsBinder resolves inputs for the workflow
    Then the input value is "Bug: login broken"

  Scenario: Git-diff input on non-PR event raises clear error
    Given a workflow definition with a "git-diff" input
    And the environment variable GITHUB_EVENT_NAME is set to "push"
    And the event payload does not contain a pull request
    When the GitHubActionsBinder attempts to resolve inputs
    Then an error is raised indicating "git-diff" input requires a pull_request event
    And the error message includes the current event type "push"

  Scenario: Missing GITHUB_TOKEN produces actionable error
    Given a workflow definition that requires GitHub API access
    And the environment variable GITHUB_TOKEN is not set
    When the GitHubActionsBinder attempts to resolve inputs
    Then an error is raised with a message containing "GITHUB_TOKEN"
    And the error message includes remediation guidance for setting the token

  Scenario: Missing required input produces actionable error
    Given a workflow definition with a required "string" input named "target-branch"
    And the event payload does not contain a value for "target-branch"
    When the GitHubActionsBinder attempts to resolve inputs
    Then an error is raised identifying "target-branch" as unresolvable
    And the error message describes the available event context

  Scenario: Binder registers under the github-actions name
    Given the agentry.binders entry point group is loaded
    When the entry points are enumerated
    Then an entry named "github-actions" is present
    And instantiating it produces a GitHubActionsBinder that conforms to the EnvironmentBinder protocol
