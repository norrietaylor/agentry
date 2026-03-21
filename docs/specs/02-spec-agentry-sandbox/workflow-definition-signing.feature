# Source: docs/specs/02-spec-agentry-sandbox/02-spec-agentry-sandbox.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Workflow Definition Signing

  Scenario: Generate Ed25519 keypair with agentry keygen
    Given the directory "~/.agentry/" exists or will be created
    When the user runs "agentry keygen"
    Then a private key is saved to "~/.agentry/signing-key.pem"
    And a public key is saved to ".agentry/public-key.pem"
    And the output instructs the user to commit the public key to the repository

  Scenario: Sign workflow safety block with agentry sign
    Given a workflow definition at "workflows/code-review.yaml" with a safety block
    And a private signing key exists at "~/.agentry/signing-key.pem"
    When the user runs "agentry sign workflows/code-review.yaml"
    Then a "signature" block is appended to the workflow YAML
    And the signature block contains algorithm "ed25519"
    And the signature block contains signed_blocks listing "safety" and "output.side_effects"
    And the signature block contains a hex-encoded signature
    And the signature block contains an ISO 8601 timestamp

  Scenario: Only safety and side_effects blocks are signed
    Given a workflow definition with safety, output.side_effects, prompts, and model blocks
    And a private signing key exists
    When the user runs "agentry sign workflows/code-review.yaml"
    And then modifies the prompts block without changing safety or side_effects
    And then runs "agentry validate workflows/code-review.yaml"
    Then the signature verification succeeds
    And the command reports the signature is valid

  Scenario: Signature verification fails when safety block is modified
    Given a signed workflow definition at "workflows/code-review.yaml"
    And a public key is available at ".agentry/public-key.pem"
    When the safety block is modified after signing
    And the user runs "agentry validate workflows/code-review.yaml"
    Then the command exits with a non-zero code
    And the output contains "Safety block signature invalid. The safety block was modified since it was signed on"

  Scenario: Signature verification succeeds for unmodified signed workflow
    Given a signed workflow definition at "workflows/code-review.yaml"
    And a public key is available at ".agentry/public-key.pem"
    When the user runs "agentry validate workflows/code-review.yaml"
    Then the command exits with code 0
    And the output confirms the signature is valid

  Scenario: Unsigned workflow skips verification without error
    Given a workflow definition without a signature block
    When the user runs "agentry validate workflows/code-review.yaml"
    Then no signature verification is performed
    And the command does not fail due to missing signature

  Scenario: Security audit warns when workflow lacks signature
    Given a workflow definition without a signature block
    When the user runs "agentry validate --security-audit workflows/code-review.yaml"
    Then the output contains a warning that the workflow is not signed

  Scenario: Deterministic serialization produces consistent signatures
    Given a workflow definition with a safety block
    And a private signing key exists
    When the user signs the workflow twice without modifying it
    Then both signatures are identical
    And verification succeeds for both

  Scenario: Security audit diffs security-relevant fields between versions
    Given two workflow definitions "workflows/v1.yaml" and "workflows/v2.yaml"
    And v2 has a different trust level and network allowlist compared to v1
    When the user runs "agentry validate --security-audit workflows/v1.yaml workflows/v2.yaml"
    Then the output shows a diff of the trust level change
    And the output shows a diff of the network allowlist change
    And the output shows a diff of any side_effects changes

  Scenario: Signature is verified during setup phase for signed workflows
    Given a signed workflow definition with a valid signature
    And a public key is available
    When the user runs "agentry setup workflows/code-review.yaml"
    Then the setup phase verifies the signature before proceeding
    And setup completes successfully when the signature is valid

  Scenario: Setup phase aborts when signature verification fails
    Given a signed workflow definition with a tampered safety block
    And a public key is available
    When the user runs "agentry setup workflows/code-review.yaml"
    Then the setup phase aborts with "Safety block signature invalid"
    And the agent does not start
