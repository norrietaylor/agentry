# Source: docs/specs/02-spec-agentry-sandbox/02-spec-agentry-sandbox.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Docker Sandbox Engine

  Scenario: Agent executes inside a Docker container with correct isolation
    Given a workflow definition "code-review.yaml" with trust set to "sandboxed"
    And Docker is available on the host
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the agent executes inside a Docker container
    And the container runs as a non-root user with UID 1000
    And the command exits with code 0 and produces agent output

  Scenario: Container applies resource limits from safety block
    Given a workflow definition with resources cpu 0.5, memory "1GB", and timeout 60
    And Docker is available on the host
    When the sandbox container is created for the workflow
    Then the container is configured with a CPU limit of 0.5 cores
    And the container is configured with a memory limit of 1GB
    And the container will be killed after 60 seconds if still running

  Scenario: Codebase is mounted read-only and output directory is read-write
    Given a workflow definition with filesystem read paths including the project directory
    And Docker is available on the host
    When the sandbox container is created
    Then the resolved codebase path is mounted read-only at "/workspace" inside the container
    And the output directory is mounted read-write at "/output" inside the container

  Scenario: Container is killed when execution exceeds timeout
    Given a workflow definition with a timeout of 5 seconds
    And Docker is available on the host
    When the agent execution inside the container exceeds 5 seconds
    Then the container is killed with SIGKILL
    And the execution record reports a timeout error

  Scenario: Container is cleaned up after successful execution
    Given a workflow that completes successfully inside a sandbox container
    When the agent execution finishes
    Then the container is removed from Docker
    And any associated volumes are removed

  Scenario: Container is cleaned up after failed execution
    Given a workflow that fails during execution inside a sandbox container
    When the agent execution fails with an error
    Then the container is still removed from Docker
    And any associated volumes are still removed
    And the cleanup does not raise an additional exception

  Scenario: Sandbox tier detector refuses to run when Docker is unavailable
    Given a workflow definition with trust set to "sandboxed"
    And Docker is not available on the host
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the command exits with a non-zero code
    And stderr contains "Docker is required for sandboxed execution. Install Docker or set trust: elevated."

  Scenario: Elevated trust mode runs agent in-process with warning
    Given a workflow definition with trust set to "elevated"
    When the user runs "agentry run workflows/code-review.yaml --input diff=HEAD~1"
    Then the agent executes in-process without a Docker container
    And stderr contains "Running in elevated trust mode -- no sandbox isolation."
    And the command produces agent output

  Scenario: Runtime shim receives configuration and writes output
    Given a workflow definition with trust set to "sandboxed"
    And Docker is available on the host
    When the agent executes inside the sandbox container
    Then the runtime shim reads LLM client configuration from a mounted JSON file
    And the runtime shim writes agent output to "/output/result.json"
    And the result is collected by the host after container execution completes
