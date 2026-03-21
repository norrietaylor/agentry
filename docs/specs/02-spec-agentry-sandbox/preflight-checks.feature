# Source: docs/specs/02-spec-agentry-sandbox/02-spec-agentry-sandbox.md
# Pattern: CLI/Process + Error Handling
# Recommended test type: Integration

Feature: Preflight Checks

  Scenario: Valid API key passes preflight check
    Given the ANTHROPIC_API_KEY environment variable is set to a valid key
    When the AnthropicAPIKeyCheck runs
    Then the check returns a pass result
    And the diagnostic message confirms the key is valid

  Scenario: Missing API key fails preflight with actionable message
    Given the ANTHROPIC_API_KEY environment variable is not set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the command exits with a non-zero code before any LLM call
    And the output contains "Preflight failed: ANTHROPIC_API_KEY is not set."

  Scenario: Invalid API key fails preflight with actionable message
    Given the ANTHROPIC_API_KEY environment variable is set to a revoked or invalid key
    When the AnthropicAPIKeyCheck makes a lightweight API call to verify the key
    Then the check returns a fail result
    And the diagnostic message indicates the key is invalid or revoked

  Scenario: Docker available check passes when Docker is running
    Given Docker is installed and running on the host
    And the workflow declares trust as "sandboxed"
    When the DockerAvailableCheck runs
    Then the check returns a pass result

  Scenario: Docker available check fails when Docker is not running
    Given Docker is not available on the host
    And the workflow declares trust as "sandboxed"
    When the DockerAvailableCheck runs
    Then the check returns a fail result
    And the diagnostic message indicates Docker is required for sandboxed execution

  Scenario: Filesystem mounts check passes when all paths exist
    Given a workflow with filesystem read paths "/app/src" and write paths "/app/output"
    And both paths exist on the host filesystem
    When the FilesystemMountsCheck runs
    Then the check returns a pass result

  Scenario: Filesystem mounts check fails when a path does not exist
    Given a workflow with filesystem read path "/nonexistent/path"
    When the FilesystemMountsCheck runs
    Then the check returns a fail result
    And the diagnostic message identifies "/nonexistent/path" as the missing path

  Scenario: Multiple preflight failures are reported together
    Given the ANTHROPIC_API_KEY environment variable is not set
    And Docker is not available on the host
    And the workflow declares trust as "sandboxed"
    When the setup phase runs all preflight checks
    Then the output lists both the API key failure and the Docker availability failure
    And the setup phase does not stop after the first failure

  Scenario: Preflight results are recorded in setup manifest
    Given a valid environment where all preflight checks pass
    When the setup phase completes
    Then the setup manifest contains a preflight results section
    And each check includes its name, pass/fail status, and diagnostic message

  Scenario: Skip preflight flag bypasses checks with warning
    Given the ANTHROPIC_API_KEY environment variable is not set
    When the user runs "agentry run workflows/code-review.yaml --skip-preflight"
    Then preflight checks are not executed
    And the output contains a warning that preflight checks were skipped
    And execution proceeds past the preflight stage
