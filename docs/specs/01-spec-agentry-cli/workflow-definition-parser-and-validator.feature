# Source: docs/specs/01-spec-agentry-cli/01-spec-agentry-cli.md
# Pattern: CLI/Process + Error handling
# Recommended test type: Integration

Feature: Workflow Definition Parser & Validator

  Scenario: Valid workflow YAML is parsed into typed models with all seven blocks
    Given a valid workflow YAML file containing identity, inputs, tools, model, safety, output, and composition blocks
    When the user runs "agentry validate workflows/code-review.yaml"
    Then the command exits with code 0
    And stdout contains "Validation successful"

  Scenario: Unknown keys at any nesting level are rejected with key path
    Given a workflow YAML file containing an unknown key "extra_field" nested under the "model" block
    When the user runs "agentry validate tests/fixtures/invalid-workflow.yaml"
    Then the command exits with code 1
    And stderr contains the key path "model.extra_field" in the error message
    And stderr contains a remediation suggestion

  Scenario: Discriminated unions enforce per-type validation for inputs
    Given a workflow YAML file with an input of type "git-diff" missing the required ref field
    When the user runs "agentry validate" against that file
    Then the command exits with code 1
    And stderr contains an error indicating the missing field for the "git-diff" input type

  Scenario: Required inputs are validated and variable references are resolved
    Given a workflow YAML file referencing "$undefined_var" which is not declared as an input or well-known runtime variable
    When the user runs "agentry validate" against that file
    Then the command exits with code 1
    And stderr contains an error indicating that "$undefined_var" does not resolve to a declared input or well-known variable

  Scenario: Version field is enforced as semantic versioning
    Given a workflow YAML file with version field set to "not-a-version"
    When the user runs "agentry validate" against that file
    Then the command exits with code 1
    And stderr contains an error about invalid semantic versioning format

  Scenario: Validation errors are printed to stderr and success to stdout
    Given a workflow YAML file with multiple validation errors
    When the user runs "agentry validate" against that file
    Then the command exits with code 1
    And all error messages appear on stderr with file path and location context
    And stdout does not contain error messages
