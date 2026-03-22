# Source: docs/specs/06-spec-agent-runtime/06-spec-agent-runtime.md
# Pattern: Schema + CLI + Backward Compatibility
# Recommended test type: Unit

Feature: Workflow Schema and CLI Update

  Scenario: Workflow YAML accepts agent block
    Given a workflow YAML file with an agent block:
      """
      agent:
        runtime: claude-code
        model: claude-sonnet-4-20250514
        system_prompt: prompts/code-review.md
        max_iterations: 20
      """
    When the workflow is parsed
    Then the WorkflowDefinition contains an agent field of type AgentBlock
    And AgentBlock.runtime is "claude-code"
    And AgentBlock.model is "claude-sonnet-4-20250514"
    And AgentBlock.system_prompt is "prompts/code-review.md"
    And AgentBlock.max_iterations is 20

  Scenario: Model block is accepted as deprecated alias
    Given a workflow YAML file with a model block but no agent block
    When the workflow is parsed
    Then the model block is auto-converted to an AgentBlock
    And AgentBlock.runtime is set to "claude-code"
    And AgentBlock.model is set from the model block's model identifier
    And AgentBlock.system_prompt is set from the model block's system_prompt

  Scenario: Agent block takes precedence over model block
    Given a workflow YAML file with both an agent block and a model block
    When the workflow is parsed
    Then the agent block is used
    And the model block is ignored

  Scenario: AgentBlock includes optional config dict for runtime-specific settings
    Given a workflow YAML file with an agent block containing a config section
    When the workflow is parsed
    Then AgentBlock.config is a dict containing the runtime-specific settings

  Scenario: CLI run command resolves agent runtime from workflow
    Given a workflow definition with agent runtime "claude-code"
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the CLI resolves the agent runtime from the workflow definition
    And passes it to RunnerDetector for execution

  Scenario: CLI verifies agent availability during preflight
    Given a workflow definition with agent runtime "claude-code"
    And the "claude" binary is not on PATH
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the preflight check fails
    And the error message indicates that the "claude" binary is required for the "claude-code" runtime

  Scenario: agentry validate validates agent block
    Given a workflow YAML file with a valid agent block
    When the user runs "agentry validate" on the file
    Then the command exits with code 0
    And no validation errors are reported

  Scenario: agentry validate reports error for unknown runtime
    Given a workflow YAML file with agent runtime set to "nonexistent-agent"
    When the user runs "agentry validate" on the file
    Then the command exits with a non-zero code
    And the error message indicates "nonexistent-agent" is not a recognized runtime

  Scenario: Updated code-review workflow uses agent block
    Given the file "workflows/code-review.yaml" has been updated
    When the file is inspected
    Then it contains an agent block with runtime "claude-code"
    And it does not rely solely on the deprecated model block

  Scenario: WorkflowDefinition Pydantic model validates AgentBlock fields
    Given an AgentBlock with runtime missing
    When the WorkflowDefinition is validated
    Then a validation error is raised indicating runtime is required
