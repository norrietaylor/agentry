# Source: docs/specs/03-spec-agentry-composition/03-spec-agentry-composition.md
# Pattern: Async + CLI/Process
# Recommended test type: Integration

Feature: DAG Execution Engine

  Scenario: Sequential chain executes nodes in dependency order
    Given a composition with three nodes A, B, C where B depends on A and C depends on B
    When the composition engine executes the workflow
    Then node A completes before node B starts
    And node B completes before node C starts
    And all three nodes have status "completed" in the composition record

  Scenario: Independent nodes in a fan-out execute concurrently
    Given a composition with node A, then nodes B and C both depending on A, then node D depending on B and C
    When the composition engine executes the workflow
    Then nodes B and C start after A completes
    And nodes B and C execute concurrently
    And the wall-clock time for B and C together is less than the sum of their individual durations
    And node D starts only after both B and C complete

  Scenario: Single-node composition executes as degenerate case
    Given a composition with a single node A and no dependencies
    When the composition engine executes the workflow
    Then node A completes with status "completed"
    And the composition record shows overall status "completed"

  Scenario: Each node gets its own provisioned runner
    Given a composition with two nodes A and B where B depends on A
    When the composition engine executes the workflow
    Then each node is provisioned with a separate runner instance
    And each runner is torn down after its node completes

  Scenario: Runner is torn down even when a node fails
    Given a composition with a node that will fail during execution
    When the composition engine executes the workflow
    Then the failing node's runner is torn down in the finally block
    And the runner teardown completes regardless of the execution error

  Scenario: Node output is written to the run directory
    Given a composition with node A that produces output data
    When the composition engine executes node A
    Then a file exists at "<run_dir>/A/result.json" containing the node's output

  Scenario: Composition record contains per-node status map and timing
    Given a composition with nodes A, B, C where B depends on A and C depends on B
    When the composition engine executes the workflow and node B fails with abort policy
    Then the composition record contains node A with status "completed"
    And node B with status "failed"
    And node C with status "not_reached"
    And the composition record includes wall-clock timing for the full composition
    And the composition record is saved to ".agentry/runs/<timestamp>/composition-record.json"
