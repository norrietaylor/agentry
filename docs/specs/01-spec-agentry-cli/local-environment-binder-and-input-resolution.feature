# Source: docs/specs/01-spec-agentry-cli/01-spec-agentry-cli.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Local Environment Binder & Input Resolution

  Scenario: Git-diff input resolves by running git diff in target directory
    Given a git repository at "/tmp/test-repo" with commits and a diff between HEAD~1 and HEAD
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1 --target /tmp/test-repo"
    Then the agent receives the git diff output as input content
    And the execution record contains the resolved diff text

  Scenario: Repository-ref input resolves to absolute path of target directory
    Given a git repository at "/tmp/test-repo"
    When the user runs "agentry run workflows/triage.yaml --input issue-description='test' --target /tmp/test-repo"
    Then the agent receives the absolute path "/tmp/test-repo" as the repository reference
    And the execution record shows the resolved repository path

  Scenario: Repository read tool restricts access to files within repo root
    Given a git repository at "/tmp/test-repo" and a workflow that uses repository:read
    When the agent attempts to read a file using path "../../etc/passwd"
    Then the tool invocation is rejected
    And the error indicates path traversal is not permitted

  Scenario: Shell execute tool allows only allowlisted commands
    Given a workflow that uses shell:execute
    When the agent attempts to execute "rm -rf /tmp/test-repo"
    Then the command is rejected
    And the error indicates the command is not in the allowlist

  Scenario: Shell execute tool runs allowlisted read-only commands
    Given a git repository at "/tmp/test-repo" and a workflow that uses shell:execute
    When the agent executes "git log --oneline -5" via the shell:execute tool
    Then the command output is returned to the agent
    And the output contains recent commit summaries

  Scenario: Outputs are written to local runs directory with timestamp
    Given a successful workflow execution against "/tmp/test-repo"
    When the execution completes
    Then a directory ".agentry/runs/<timestamp>/" exists within the target directory
    And it contains "execution-record.json" with resolved inputs, tool invocations, and timing

  Scenario: Binder discovery uses entry points and defaults to local
    Given no --environment flag is provided
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the local environment binder is selected automatically

  Scenario: Non-git directory fails with clear error for git-dependent inputs
    Given a directory at "/tmp/not-a-repo" that is not a git repository
    And a workflow that declares a git-diff input
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1 --target /tmp/not-a-repo"
    Then the command exits with code 1
    And stderr contains an error indicating the target directory is not a git repository
