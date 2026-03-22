# Source: docs/specs/06-spec-agent-runtime/06-spec-agent-runtime.md
# Pattern: Refactor + Protocol Unification
# Recommended test type: Unit

Feature: SecurityEnvelope Cleanup

  Scenario: SecurityEnvelope no longer accepts executor parameter
    Given the updated SecurityEnvelope class
    When __init__ is called without an executor parameter
    Then the envelope initializes successfully
    And __init__ does not accept an executor keyword argument

  Scenario: SecurityEnvelope delegates execution to runner
    Given a SecurityEnvelope configured with a mock runner
    And a valid agent_config
    When SecurityEnvelope.execute is called
    Then it calls self._runner.execute with runner_context and agent_config
    And it does not call any executor.run method

  Scenario: Duplicate RunnerProtocol in security/envelope.py is removed
    Given the security/envelope.py module
    When the module is inspected
    Then it does not define its own RunnerProtocol
    And it imports RunnerProtocol from runners/protocol.py

  Scenario: Envelope RunnerProtocol.provision matches canonical signature
    Given the RunnerProtocol imported by SecurityEnvelope
    When provision is called
    Then it accepts safety_block and resolved_inputs as parameters
    And it returns a RunnerContext

  Scenario: Envelope RunnerProtocol.teardown accepts RunnerContext
    Given the RunnerProtocol imported by SecurityEnvelope
    When teardown is called
    Then it accepts a RunnerContext parameter

  Scenario: EnvelopeResult is populated from runner ExecutionResult
    Given a runner that returns an ExecutionResult with an execution_record
    When SecurityEnvelope.execute completes
    Then EnvelopeResult.execution_record is populated from the runner's ExecutionResult.execution_record

  Scenario: All call sites updated to not pass executor to SecurityEnvelope
    Given the codebase after the refactor
    When all SecurityEnvelope constructor calls are inspected
    Then none of them pass an executor parameter

  Scenario: SecurityEnvelope preflight and validation still work
    Given a SecurityEnvelope configured with a runner
    And a workflow with tool manifest declaring "read_file" and "write_file"
    When SecurityEnvelope.execute is called
    Then preflight checks execute before agent launch
    And only declared tools are available to the agent
    And output validation runs after agent execution completes

  Scenario: agentry validate succeeds with updated envelope
    Given a valid workflow definition "workflows/code-review.yaml"
    And the SecurityEnvelope has been updated to remove executor dependency
    When the user runs "agentry validate workflows/code-review.yaml"
    Then the command exits with code 0
    And no errors are reported about missing executor
