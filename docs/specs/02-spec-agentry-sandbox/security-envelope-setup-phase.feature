# Source: docs/specs/02-spec-agentry-sandbox/02-spec-agentry-sandbox.md
# Pattern: CLI/Process + State + Error Handling
# Recommended test type: Integration

Feature: Security Envelope and Setup Phase

  Scenario: Security envelope strips tools not in workflow manifest
    Given a workflow definition declaring tools "read_file" and "write_file" only
    And the agent runtime has additional tools "shell_exec" and "http_request" available
    When the SecurityEnvelope wraps the AgentExecutor
    Then only "read_file" and "write_file" are available to the agent
    And "shell_exec" and "http_request" are not accessible during execution

  Scenario: Setup phase executes all preparation steps in sequence
    Given a workflow definition with trust set to "sandboxed"
    And Docker is available on the host
    And a valid ANTHROPIC_API_KEY is set
    When the user runs "agentry setup workflows/code-review.yaml"
    Then the setup phase detects the sandbox tier
    And provisions the container with correct mounts and limits
    And verifies network isolation
    And runs preflight checks
    And compiles the output validator
    And generates the setup manifest
    And the command exits with code 0 without starting the agent

  Scenario: Setup manifest contains all required fields
    Given a workflow definition with trust set to "sandboxed"
    And a valid environment with Docker and API credentials
    When the setup phase completes
    Then a setup manifest is saved at ".agentry/runs/<timestamp>/setup-manifest.json"
    And the manifest contains the workflow definition version
    And the manifest contains the container image used
    And the manifest contains mounted filesystem paths for read and write
    And the manifest contains network egress rules
    And the manifest contains resource limits for CPU, memory, and timeout
    And the manifest contains a SHA-256 credential fingerprint of the API key
    And the manifest contains the detected sandbox tier and a timestamp

  Scenario: Setup manifest uses credential fingerprint not actual key
    Given an ANTHROPIC_API_KEY set to a known value
    When the setup phase generates the setup manifest
    Then the manifest contains a SHA-256 hash of the API key as the credential fingerprint
    And the manifest does not contain the actual API key value

  Scenario: Setup phase aborts on failure and prevents agent execution
    Given a workflow definition with trust set to "sandboxed"
    And Docker is not available on the host
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the setup phase fails with a message identifying the failed check
    And the message suggests remediation steps
    And the agent never starts execution

  Scenario: agentry setup command runs without executing the agent
    Given a valid workflow definition and environment
    When the user runs "agentry setup workflows/code-review.yaml"
    Then the sandbox is provisioned and all checks pass
    And a setup manifest is produced
    And the command exits without running any agent
    And no LLM API calls are made

  Scenario: agentry setup reports preflight failure with diagnostic
    Given a workflow definition with trust set to "sandboxed"
    And the ANTHROPIC_API_KEY environment variable is set to an invalid value
    When the user runs "agentry setup workflows/code-review.yaml"
    Then the command exits with a non-zero code
    And the output contains "Preflight check failed: ANTHROPIC_API_KEY is invalid."

  Scenario: Sandboxed run executes setup phase before agent
    Given a workflow definition with trust set to "sandboxed"
    And a valid environment with Docker and API credentials
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the setup phase completes first
    And the agent executes inside the sandbox after setup succeeds
    And agent output passes through the validation pipeline before emission

  Scenario: Elevated trust run skips sandbox but runs preflight and produces manifest
    Given a workflow definition with trust set to "elevated"
    And a valid ANTHROPIC_API_KEY is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then sandbox provisioning is skipped
    And preflight checks still execute
    And a setup manifest is still generated at ".agentry/runs/<timestamp>/setup-manifest.json"
