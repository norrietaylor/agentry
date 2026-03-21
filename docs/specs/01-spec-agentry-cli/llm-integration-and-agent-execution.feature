# Source: docs/specs/01-spec-agentry-cli/01-spec-agentry-cli.md
# Pattern: CLI/Process + Error handling
# Recommended test type: Integration

Feature: LLM Integration & Agent Execution

  Scenario: Agent execution sends correct parameters to Claude API
    Given a workflow definition specifying Claude Sonnet, temperature 0.2, and max_tokens 4096
    And the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run" against that workflow with valid inputs
    Then the LLM call is made with model "claude-sonnet", temperature 0.2, and max_tokens 4096
    And the execution record logs the model parameters used

  Scenario: System prompt is loaded from the referenced file path
    Given a workflow with model.system_prompt pointing to "workflows/prompts/code-review.md"
    When the user runs "agentry run" against that workflow
    Then the LLM receives the content of "workflows/prompts/code-review.md" as the system prompt

  Scenario: Resolved inputs are formatted as user messages to the LLM
    Given a workflow with git-diff and repository-ref inputs
    And the inputs resolve to actual diff text and a repository path
    When the agent execution begins
    Then the LLM receives the resolved diff content and repository path as user messages

  Scenario: Retry logic applies exponential backoff on transient failures
    Given a workflow with retry config of max_attempts=3 and exponential backoff
    And the LLM API returns a transient error on the first two attempts
    When the user runs "agentry run" against that workflow
    Then the system retries up to 3 times with increasing delays
    And the execution record logs each retry attempt with timing

  Scenario: Execution timeout cancels the LLM call
    Given a workflow with safety.resources.timeout set to 5 seconds
    And the LLM API takes longer than 5 seconds to respond
    When the user runs "agentry run" against that workflow
    Then the LLM call is cancelled after 5 seconds
    And the command exits with a timeout error message
    And the execution record shows the timeout event

  Scenario: Token usage and timing are recorded in the execution record
    Given a successful workflow execution
    When the execution completes
    Then the file ".agentry/runs/<timestamp>/execution-record.json" contains input_tokens and output_tokens counts
    And the file contains wall_clock_timing with start and end timestamps

  Scenario: Missing ANTHROPIC_API_KEY produces actionable error
    Given the ANTHROPIC_API_KEY environment variable is not set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the command exits with code 1
    And stderr contains an error message suggesting the user set ANTHROPIC_API_KEY

  Scenario: Agent structured output is collected and passed to validation
    Given a workflow with a declared output schema
    And the LLM returns a structured response matching the schema
    When the execution completes
    Then the agent output is passed through the output validation pipeline before emission
    And the validated output appears in the terminal
