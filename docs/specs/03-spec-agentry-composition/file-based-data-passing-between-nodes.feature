# Source: docs/specs/03-spec-agentry-composition/03-spec-agentry-composition.md
# Pattern: State + Async
# Recommended test type: Integration

Feature: File-Based Data Passing Between Nodes

  Scenario: Upstream node output is written to result.json after validation
    Given a composition node A that produces validated output {"summary": "done", "count": 3}
    When node A completes execution
    Then the file "<run_dir>/A/result.json" contains {"summary": "done", "count": 3}

  Scenario: Downstream node receives upstream output path as resolved input
    Given a composition with nodes A and B where B depends on A and B declares inputs {"data": "A.output"}
    When node A completes and the engine prepares node B for execution
    Then node B's provided_values includes "data" mapped to the absolute path of A's result.json

  Scenario: Field extraction resolves a specific field from upstream output
    Given node A has produced output {"summary": "done", "count": 3} at its result.json path
    And node B declares inputs {"total": "A.output.count"}
    When the engine resolves node B's inputs
    Then node B receives the value 3 for the "total" input

  Scenario: Failed upstream with skip policy passes NodeFailure path to downstream
    Given a composition with nodes A and B where A has failure mode "skip" and B depends on A
    When node A fails during execution
    Then A's output path contains a NodeFailure JSON with "_failure" sentinel field set to true
    And node B receives the path to the NodeFailure JSON as its input

  Scenario: Engine validates upstream completed before passing output
    Given a composition with nodes A and B where B depends on A and references A.output
    When node A fails with abort policy
    Then node B does not execute
    And no attempt is made to resolve A's output path for B

  Scenario: Three-node pipeline passes data end-to-end
    Given a composition with nodes A, B, C in sequence where B receives A's output and C receives B's output
    When the composition executes end-to-end
    Then node B's input contains the path to A's result.json
    And node C's input contains the path to B's result.json
    And each node's result.json exists in the run directory
