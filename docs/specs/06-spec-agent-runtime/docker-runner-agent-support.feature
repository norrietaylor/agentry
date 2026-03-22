# Source: docs/specs/06-spec-agent-runtime/06-spec-agent-runtime.md
# Pattern: Docker + Process + Parsing
# Recommended test type: Integration

Feature: DockerRunner Agent Support

  Scenario: DockerRunner launches configured agent inside container
    Given a workflow definition with trust set to "sandboxed"
    And the agent runtime is configured as "claude-code"
    And Docker is available on the host
    When DockerRunner.execute is called
    Then the container command invokes "claude -p" with the system prompt and task description
    And the agent executes inside the provisioned Docker container

  Scenario: DockerRunner passes agent configuration to container
    Given a workflow with agent runtime "claude-code" and model "claude-sonnet-4-20250514"
    And a timeout of 60 seconds and tool names "read_file" and "write_file"
    When the Docker container is created
    Then agent configuration is passed as environment variables or a mounted config file
    And the model identifier is available to the agent inside the container

  Scenario: DockerRunner parses container stdout JSON into AgentResult
    Given a Docker container running Claude Code that produces JSON output on stdout
    When the container execution completes
    Then the stdout JSON is parsed into an AgentResult
    And the parsing logic matches ClaudeCodeAgent's output parser

  Scenario: DockerRunner enforces timeout by killing container
    Given a workflow with a timeout of 5 seconds
    And Docker is available on the host
    When the agent execution inside the container exceeds 5 seconds
    Then the container is killed with SIGKILL
    And the AgentResult has timed_out set to true
    And the execution record reports a timeout error

  Scenario: DockerRunner uses image with Claude Code pre-installed
    Given a workflow safety block specifying a sandbox base image
    When the Docker container is provisioned
    Then the image includes the Claude Code CLI
    And "claude" is executable inside the container

  Scenario: Shim module launches agent runtime instead of AgentExecutor
    Given the updated shim module at src/agentry/runners/shim.py
    When the shim is invoked inside the container
    Then it launches the configured agent runtime
    And it does not reference AgentExecutor
    And it writes agent output to the expected output path

  Scenario: ANTHROPIC_API_KEY is injected as container environment variable
    Given a workflow with trust set to "sandboxed"
    And ANTHROPIC_API_KEY is set on the host
    When the Docker container is created
    Then ANTHROPIC_API_KEY is passed as a container environment variable
    And the key is not included in the container image or command-line arguments
