# Source: docs/specs/01-spec-agentry-cli/01-spec-agentry-cli.md
# Pattern: CLI/Process
# Recommended test type: Integration

Feature: CLI Framework & Output Formatting

  Scenario: CLI group exposes all subcommands in help output
    Given the agentry package is installed via "pip install -e ."
    When the user runs "agentry --help"
    Then stdout lists the commands "run", "validate", "setup", "ci", and "registry" with descriptions
    And the command exits with code 0

  Scenario: Stub commands print not-yet-implemented and exit cleanly
    Given the agentry CLI is available
    When the user runs "agentry setup"
    Then stdout contains "Not yet implemented"
    And the command exits with code 0

  Scenario: Global --verbose flag increases log verbosity
    Given a valid workflow YAML file
    When the user runs "agentry validate workflows/code-review.yaml --verbose"
    Then the command exits with code 0
    And stderr contains debug-level log messages not present without --verbose

  Scenario: TTY mode produces human-readable colored output
    Given a valid workflow and the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1" in an interactive terminal
    Then stdout contains human-readable formatted output with color codes
    And the command exits with code 0

  Scenario: Piped mode produces JSON output
    Given a valid workflow and the ANTHROPIC_API_KEY environment variable is set
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1 | cat"
    Then the output is valid JSON matching the workflow's declared output schema
    And the command exits with code 0

  Scenario: Run command accepts repeatable --input flags and --target option
    Given a valid workflow and a git repository at "/tmp/test-repo"
    When the user runs "agentry run workflows/bug-fix.yaml --input issue-description='Login fails' --target /tmp/test-repo"
    Then the workflow receives both input values
    And the execution uses "/tmp/test-repo" as the working directory

  Scenario: Keyboard interrupt prints partial results and exits 130
    Given a workflow execution is in progress with an active LLM call
    When the user sends a keyboard interrupt (Ctrl+C)
    Then stdout contains a summary of any partial results
    And the command exits with code 130

  Scenario: Every command provides --help with usage examples
    Given the agentry CLI is available
    When the user runs "agentry validate --help"
    Then stdout contains validation-specific options
    And stdout contains at least one usage example

  Scenario: The agentry command is available after pip install
    Given the project pyproject.toml defines "agentry" in [project.scripts]
    When the user runs "pip install -e ." and then "agentry --version"
    Then the agentry command is found on PATH
    And the command exits with code 0
