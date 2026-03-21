# Source: docs/specs/02-spec-agentry-sandbox/02-spec-agentry-sandbox.md
# Pattern: CLI/Process + Error Handling
# Recommended test type: Integration

Feature: Network Isolation and DNS Filtering

  Scenario: Allowed domain resolves inside sandbox network
    Given a workflow definition with network allow list including "api.anthropic.com"
    And an isolated Docker network is created for the sandbox
    When the sandbox container attempts to resolve "api.anthropic.com"
    Then the DNS query resolves successfully
    And the DNS query is logged in the execution record as "resolved"

  Scenario: Blocked domain returns NXDOMAIN inside sandbox network
    Given a workflow definition with network allow list including only "api.anthropic.com"
    And an isolated Docker network is created for the sandbox
    When the sandbox container attempts to resolve "example.com"
    Then the DNS query returns NXDOMAIN
    And the DNS query is logged in the execution record as "blocked"

  Scenario: LLM API domain is always included in allow list
    Given a workflow definition with an empty network allow list
    And the model configuration uses the Anthropic provider
    When the sandbox network is configured
    Then "api.anthropic.com" is automatically added to the DNS allow list
    And the sandbox container can resolve "api.anthropic.com"

  Scenario: DNS proxy is sole resolver for sandbox container
    Given a workflow definition with trust set to "sandboxed"
    And Docker is available on the host
    When the sandbox container is created on the isolated network
    Then the container DNS configuration points exclusively to the DNS filtering proxy
    And no other DNS resolvers are configured for the container

  Scenario: All DNS queries are logged in the execution record
    Given a workflow that makes DNS queries for allowed and blocked domains
    When the agent execution completes
    Then the execution record at ".agentry/runs/<timestamp>/execution-record.json" contains a "dns_queries" section
    And the section includes entries for both resolved and blocked queries with domain names and timestamps

  Scenario: Network isolation is verified during setup phase
    Given a workflow definition with trust set to "sandboxed"
    And Docker is available on the host
    When the setup phase runs
    Then a known-blocked domain is resolved from inside the container to confirm it fails
    And the setup phase proceeds after confirming network isolation

  Scenario: Setup aborts when network isolation verification fails
    Given a workflow definition with trust set to "sandboxed"
    And the isolated Docker network fails to enforce DNS filtering
    When the setup phase attempts to verify network isolation
    Then the setup phase aborts with a diagnostic message explaining the network isolation failure
    And the agent does not start

  Scenario: Isolated network is torn down after successful execution
    Given a sandbox execution that completes successfully
    When execution cleanup runs
    Then the isolated Docker network is removed

  Scenario: Isolated network is torn down after failed execution
    Given a sandbox execution that fails with an error
    When execution cleanup runs
    Then the isolated Docker network is still removed
