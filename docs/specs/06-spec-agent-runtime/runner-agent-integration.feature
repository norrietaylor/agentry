# Source: docs/specs/06-spec-agent-runtime/06-spec-agent-runtime.md
# Pattern: Integration + Delegation
# Recommended test type: Integration

Feature: Runner-Agent Integration

  Scenario: InProcessRunner delegates execution to AgentProtocol instance
    Given an InProcessRunner configured with a mock AgentProtocol
    And an AgentTask with system_prompt and task_description
    When InProcessRunner.execute is called with agent_config
    Then the runner delegates to agent.execute with the AgentTask
    And the runner returns an ExecutionResult populated from the AgentResult

  Scenario: InProcessRunner no longer imports or uses AgentExecutor
    Given the InProcessRunner implementation
    When the module is inspected
    Then there are no imports of AgentExecutor
    And there are no references to LLMClient

  Scenario: InProcessRunner does not require llm_client in constructor
    Given a caller constructing an InProcessRunner
    When __init__ is called without an llm_client parameter
    Then the runner initializes successfully
    And the runner is ready to accept agent execution requests

  Scenario: RunnerProtocol.execute accepts agent_config parameter
    Given the updated RunnerProtocol definition
    When execute is called
    Then it accepts agent_config of type AgentConfig
    And AgentConfig includes agent_name identifying the runtime

  Scenario: AgentConfig replaces llm_config with agent fields
    Given an AgentConfig is constructed
    When it is validated
    Then it contains agent_name as a string
    And it contains agent_config as a dict for runtime-specific configuration
    And it does not contain an llm_config field

  Scenario: RunnerDetector resolves agent runtime from workflow configuration
    Given a workflow configuration with agent runtime "claude-code"
    And an AgentRegistry with "claude-code" registered
    When RunnerDetector resolves the agent
    Then it creates a ClaudeCodeAgent instance
    And it injects the agent into the runner

  Scenario: ExecutionResult is populated from AgentResult fields
    Given an AgentResult with output, token_usage, tool_invocations, and timing data
    When the runner maps AgentResult to ExecutionResult
    Then ExecutionResult.output matches AgentResult.output
    And ExecutionResult contains token usage from AgentResult
    And ExecutionResult contains tool invocations from AgentResult
