# Source: docs/specs/03-spec-agentry-composition/03-spec-agentry-composition.md
# Pattern: CLI/Process
# Recommended test type: Integration

Feature: CLI Integration & Composed Workflow Execution

  Scenario: agentry run dispatches composed workflow through the DAG engine
    Given a workflow file with a non-empty composition.steps list
    When the user runs "agentry run workflows/planning-pipeline.yaml --input issues='bug list'"
    Then the command dispatches through the CompositionEngine
    And the composition executes all nodes in dependency order
    And the command exits with code 0

  Scenario: agentry run dispatches single-agent workflow through existing path
    Given a workflow file with an empty composition block
    When the user runs "agentry run workflows/triage.yaml --input issues='bug list'"
    Then the command dispatches through the single-agent execution path
    And the command exits with code 0

  Scenario: Composition progress is displayed to the terminal
    Given a composed workflow with three nodes
    When the user runs the composition in a TTY terminal
    Then each node start event is displayed
    And each node completion event shows a status line
    And failed nodes display the error inline

  Scenario: Combined execution summary is printed after composition completes
    Given a composed workflow that completes with mixed node statuses
    When the composition finishes executing
    Then the terminal displays overall composition status
    And per-node status for each node
    And total wall-clock time
    And per-node timing

  Scenario: --node flag executes a single composition node in isolation
    Given a composed workflow with nodes triage, decompose, and summary
    When the user runs "agentry run workflows/planning-pipeline.yaml --node triage"
    Then only the triage node executes
    And no upstream data is provided
    And no downstream propagation occurs
    And the command exits with code 0

  Scenario: Standard library planning-pipeline workflow executes end-to-end
    Given the standard library file "workflows/planning-pipeline.yaml" exists with a valid composition block
    And the standard library file "workflows/task-decompose.yaml" exists with identity, inputs, tools, model, and output blocks
    When the user runs "agentry run workflows/planning-pipeline.yaml --input issues='bug list'"
    Then the triage node executes first
    And the task-decompose node receives triage output and executes second
    And the summary node receives task-decompose output and executes last
    And the composition completes with overall status "completed"
