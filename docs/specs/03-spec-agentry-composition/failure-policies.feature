# Source: docs/specs/03-spec-agentry-composition/03-spec-agentry-composition.md
# Pattern: Error handling + Async
# Recommended test type: Integration

Feature: Failure Policies

  Scenario: Abort policy halts the entire composition on node failure
    Given a composition with nodes A, B, C in sequence where node B has failure mode "abort"
    When node A completes successfully and node B fails
    Then node C does not execute
    And node C has status "not_reached" in the composition record
    And the composition overall status is "failed"

  Scenario: Skip policy propagates failure object to downstream nodes
    Given a composition with nodes A, B, C in sequence where node B has failure mode "skip"
    When node A completes successfully and node B fails
    Then a NodeFailure object containing node_id "B" and the error is written to B's output path
    And node C receives the NodeFailure object as its input
    And node C executes with the failure object available

  Scenario: Retry policy re-executes a failed node up to max_retries times
    Given a composition with node A that has failure mode "retry", max_retries 3, and fallback "abort"
    And node A will fail on the first 2 attempts and succeed on the third
    When the composition engine executes the workflow
    Then node A is executed 3 times total
    And each retry provisions a fresh runner
    And node A ultimately has status "completed"

  Scenario: Retry policy falls back to abort when all retries exhausted
    Given a composition with node A that has failure mode "retry", max_retries 2, and fallback "abort"
    And node A will fail on all attempts
    When the composition engine executes the workflow
    Then node A is executed 3 times total (1 initial + 2 retries)
    And after all retries fail the abort policy activates
    And downstream nodes have status "not_reached"
    And the composition overall status is "failed"

  Scenario: Retry policy falls back to skip when all retries exhausted
    Given a composition with nodes A and B in sequence where A has failure mode "retry", max_retries 1, and fallback "skip"
    And node A will fail on all attempts
    When the composition engine executes the workflow
    Then node A is executed 2 times total (1 initial + 1 retry)
    And a NodeFailure object is propagated to node B
    And node B executes with the failure object

  Scenario: Retry count and per-attempt errors are recorded in the composition record
    Given a composition with node A that has failure mode "retry" and max_retries 2
    And node A fails on the first attempt and succeeds on the second
    When the composition engine executes the workflow
    Then the composition record for node A includes retry count 1
    And the first attempt's error message is recorded

  Scenario: Successful node outputs are preserved when a downstream node fails
    Given a composition with nodes A and B in sequence where B has failure mode "abort"
    When node A completes successfully and node B fails
    Then node A's output file remains in the run directory
    And node A's execution record is present in the composition record
    And node A's status is "completed"

  Scenario: Failure policy decision is logged
    Given a composition with node B that has failure mode "skip"
    When node B fails during execution
    Then the engine logs a message containing "Node 'B' failed" and "Policy: skip" and the downstream node names
