# Source: docs/specs/03-spec-agentry-composition/03-spec-agentry-composition.md
# Pattern: State + Error handling
# Recommended test type: Unit

Feature: Composition Model Extension & DAG Validation

  Scenario: Composition step parses failure policy fields
    Given a workflow YAML with a composition step that declares failure mode "retry", max_retries 3, and fallback "skip"
    When the workflow definition is loaded and validated
    Then the parsed CompositionStep has failure mode "retry"
    And max_retries is 3
    And fallback is "skip"

  Scenario: Composition step parses input mappings
    Given a workflow YAML with a composition step that declares inputs mapping {"issues": "triage.output"}
    When the workflow definition is loaded and validated
    Then the parsed CompositionStep has an inputs dict with key "issues" mapped to "triage.output"

  Scenario: Cycle in composition graph produces a clear error
    Given a workflow YAML with composition steps A depending on B and B depending on A
    When the workflow definition is loaded and validated
    Then validation fails with an error message that identifies the cycle path including "A" and "B"

  Scenario: Unknown depends_on reference produces a validation error
    Given a workflow YAML with a composition step that depends_on "nonexistent_node"
    When the workflow definition is loaded and validated
    Then validation fails with an error message indicating "nonexistent_node" is not a valid node ID

  Scenario: Input source expression referencing unknown node produces a validation error
    Given a workflow YAML with a composition step whose inputs reference "unknown_node.output"
    When the workflow definition is loaded and validated
    Then validation fails with an error message indicating "unknown_node" is not a valid upstream node

  Scenario: Backward compatible composition steps parse without new fields
    Given a workflow YAML with composition steps using only name, workflow, and depends_on fields
    When the workflow definition is loaded and validated
    Then each step's id defaults to its name value
    And failure mode defaults to "abort"
    And inputs mapping defaults to an empty dict
