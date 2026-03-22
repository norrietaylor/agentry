# Source: docs/specs/06-spec-agent-runtime/06-spec-agent-runtime.md
# Pattern: Protocol + Process + Registry
# Recommended test type: Unit

Feature: AgentProtocol and ClaudeCodeAgent

  Scenario: ClaudeCodeAgent satisfies AgentProtocol at runtime
    Given the AgentProtocol is defined as a PEP-544 runtime-checkable protocol
    And ClaudeCodeAgent is implemented
    When runtime_checkable is evaluated for ClaudeCodeAgent against AgentProtocol
    Then ClaudeCodeAgent satisfies the protocol check
    And ClaudeCodeAgent exposes an execute method accepting AgentTask and returning AgentResult

  Scenario: AgentTask carries all required fields
    Given an AgentTask is constructed with system_prompt, task_description, tool_names, and working_directory
    When the AgentTask is validated
    Then it contains system_prompt as a string
    And it contains task_description as a string
    And it contains tool_names as a list
    And it contains working_directory as a string
    And it contains optional fields output_schema, timeout, and max_iterations

  Scenario: AgentResult carries execution metadata
    Given an agent execution has completed
    When the AgentResult is constructed
    Then it contains output as a structured dict or None
    And it contains raw_output as a string
    And it contains exit_code as an integer
    And it contains token_usage with input and output token counts
    And it contains tool_invocations as a list
    And it contains timed_out as a boolean
    And it contains error as a string or None

  Scenario: ClaudeCodeAgent invokes claude CLI in print mode
    Given a ClaudeCodeAgent is configured with model "claude-sonnet-4-20250514"
    And an AgentTask with system_prompt "Review this code" and task_description "Check for bugs"
    When ClaudeCodeAgent.execute is called
    Then a subprocess is launched with "claude" and the "-p" flag
    And the "--model" flag is set to "claude-sonnet-4-20250514"
    And the system prompt and task description are passed to the subprocess

  Scenario: ClaudeCodeAgent passes output-format json when output schema is defined
    Given a ClaudeCodeAgent instance
    And an AgentTask with an output_schema defined
    When ClaudeCodeAgent.execute is called
    Then the subprocess command includes "--output-format json"
    And the structured JSON response is parsed into AgentResult.output

  Scenario: ClaudeCodeAgent enforces timeout by killing subprocess
    Given a ClaudeCodeAgent instance
    And an AgentTask with timeout set to 5 seconds
    When the subprocess exceeds 5 seconds of execution
    Then the subprocess is killed
    And the AgentResult has timed_out set to true
    And the AgentResult has an error message indicating timeout

  Scenario: ClaudeCodeAgent captures token usage from JSON output
    Given a ClaudeCodeAgent instance
    And the claude CLI returns JSON output with token usage metadata
    When the output is parsed
    Then AgentResult.token_usage contains input_tokens and output_tokens
    And the values match those reported by the claude CLI

  Scenario: ClaudeCodeAgent.check_available succeeds when claude binary is on PATH
    Given the "claude" binary is installed and on PATH
    When ClaudeCodeAgent.check_available is called
    Then it returns true

  Scenario: ClaudeCodeAgent.check_available fails when claude binary is missing
    Given the "claude" binary is not on PATH
    When ClaudeCodeAgent.check_available is called
    Then it returns false

  Scenario: AgentRegistry maps runtime names to factory functions
    Given an AgentRegistry with "claude-code" registered
    When a caller requests the agent for runtime "claude-code"
    Then the registry returns a ClaudeCodeAgent factory
    And invoking the factory produces a ClaudeCodeAgent instance

  Scenario: AgentRegistry raises error for unknown runtime
    Given an AgentRegistry with "claude-code" registered
    When a caller requests the agent for runtime "unknown-agent"
    Then the registry raises an error indicating the runtime is not registered
