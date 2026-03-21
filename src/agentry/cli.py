"""Agentry CLI entry point.

Usage examples:
    agentry --help
    agentry validate workflows/code-review.yaml
    agentry run workflows/code-review.yaml --input diff=HEAD~1
    agentry run workflows/code-review.yaml --input diff=HEAD~1 --target /path/to/repo
    agentry --verbose validate workflows/code-review.yaml
    agentry --output-format json run workflows/code-review.yaml --input diff=HEAD~1
"""

from __future__ import annotations

import logging
import sys

import click

from agentry import __version__

logger = logging.getLogger(__name__)


class OutputFormat:
    AUTO = "auto"
    JSON = "json"
    TEXT = "text"


OUTPUT_FORMAT_CHOICES = click.Choice(["auto", "json", "text"], case_sensitive=False)


def _is_tty() -> bool:
    """Return True if stdout is a terminal (TTY)."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _configure_logging(verbose: bool) -> None:
    """Configure the root logger based on verbosity flag."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if verbose:
        logger.debug("Verbose logging enabled")


@click.group()
@click.version_option(version=__version__, prog_name="agentry")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Increase log level to DEBUG. Debug messages are written to stderr.",
)
@click.option(
    "--config",
    "-c",
    metavar="PATH",
    default=None,
    type=click.Path(exists=False),
    help="Override the default config file location.",
)
@click.option(
    "--output-format",
    "-f",
    type=OUTPUT_FORMAT_CHOICES,
    default="auto",
    show_default=True,
    help=(
        "Force output format. "
        "'auto' detects TTY: human-readable when interactive, JSON when piped. "
        "'json' always emits JSON. "
        "'text' always emits human-readable text."
    ),
)
@click.pass_context
def main(
    ctx: click.Context,
    verbose: bool,
    config: str | None,
    output_format: str,
) -> None:
    """Agentry: Portable agentic workflow orchestration.

    Treats agentic workflows as portable, declarative definitions that run
    identically on a developer's laptop and in CI.

    \b
    Examples:
      agentry validate workflows/code-review.yaml
      agentry run workflows/code-review.yaml --input diff=HEAD~1
      agentry --verbose run workflows/code-review.yaml --input diff=HEAD~1

    Run 'agentry COMMAND --help' for help on a specific command.
    """
    _configure_logging(verbose)

    # Resolve effective output format
    if output_format == OutputFormat.AUTO:
        effective_format = OutputFormat.TEXT if _is_tty() else OutputFormat.JSON
    else:
        effective_format = output_format

    # Store shared state in click context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config"] = config
    ctx.obj["output_format"] = effective_format
    ctx.obj["is_tty"] = _is_tty()

    if config:
        logger.debug("Using config file: %s", config)


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------

@main.command()
@click.argument("workflow_path", metavar="WORKFLOW_PATH")
@click.pass_context
def validate(ctx: click.Context, workflow_path: str) -> None:
    """Validate a workflow definition YAML file.

    Parses the workflow definition at WORKFLOW_PATH, validates it against the
    Agentry schema, and reports all errors with file path and location context.

    \b
    Exit codes:
      0  Workflow is valid
      1  Validation failed (errors written to stderr)

    \b
    Examples:
      agentry validate workflows/code-review.yaml
      agentry validate workflows/bug-fix.yaml --verbose
    """
    import os

    obj = ctx.ensure_object(dict)

    logger.debug("Validating workflow: %s", workflow_path)

    if not os.path.exists(workflow_path):
        click.echo(
            f"Error: workflow file not found: {workflow_path}",
            err=True,
        )
        sys.exit(1)

    # Attempt to import the parser from T01.3; fall back gracefully if not yet
    # implemented so that the CLI itself remains functional as a skeleton.
    try:
        from agentry.parser import validate_workflow_file

        errors = validate_workflow_file(workflow_path)
        if errors:
            for err in errors:
                click.echo(f"Error: {err}", err=True)
            sys.exit(1)
        else:
            output_format: str = obj.get("output_format", OutputFormat.TEXT)
            if output_format == OutputFormat.JSON:
                import json

                click.echo(json.dumps({"status": "valid", "path": workflow_path}))
            else:
                click.echo(f"Validation successful: {workflow_path}")
    except ImportError:
        # Parser not yet implemented — report the YAML is loadable at minimum.
        logger.debug("Parser module not available; performing basic YAML load check.")
        try:
            import yaml  # type: ignore[import-untyped]

            with open(workflow_path) as fh:
                yaml.safe_load(fh)
            click.echo(
                f"Validation successful (basic): {workflow_path}",
            )
        except Exception as exc:  # noqa: BLE001
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------

@main.command()
@click.argument("workflow_path", metavar="WORKFLOW_PATH")
@click.option(
    "--input",
    "-i",
    "inputs",
    multiple=True,
    metavar="KEY=VALUE",
    help=(
        "Pass an input to the workflow as KEY=VALUE. "
        "Repeatable: --input diff=HEAD~1 --input repo=/path/to/repo"
    ),
)
@click.option(
    "--target",
    "-t",
    default=".",
    show_default=True,
    metavar="PATH",
    type=click.Path(file_okay=False),
    help="Working directory for local execution. Defaults to the current directory.",
)
@click.pass_context
def run(
    ctx: click.Context,
    workflow_path: str,
    inputs: tuple[str, ...],
    target: str,
) -> None:
    """Execute a workflow definition against a local repository.

    Resolves abstract workflow inputs (git-diff, repository-ref) from TARGET,
    calls the configured LLM, validates output, and emits results.

    \b
    Options:
      --input KEY=VALUE   Pass input value (repeatable)
      --target PATH       Repository to run against (default: cwd)

    \b
    Examples:
      agentry run workflows/code-review.yaml --input diff=HEAD~1
      agentry run workflows/code-review.yaml --input diff=HEAD~1 --target /path/to/repo
      agentry run workflows/bug-fix.yaml \\
          --input issue-description='Login fails' \\
          --target /path/to/repo
    """
    import os
    import signal

    obj = ctx.ensure_object(dict)
    output_format: str = obj.get("output_format", OutputFormat.TEXT)

    logger.debug("Running workflow: %s", workflow_path)
    logger.debug("Target: %s", target)
    logger.debug("Inputs: %s", inputs)

    if not os.path.exists(workflow_path):
        click.echo(f"Error: workflow file not found: {workflow_path}", err=True)
        sys.exit(1)

    # Parse KEY=VALUE inputs
    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            click.echo(
                f"Error: --input value must be KEY=VALUE, got: {item!r}",
                err=True,
            )
            sys.exit(1)
        key, _, value = item.partition("=")
        parsed_inputs[key.strip()] = value.strip()

    logger.debug("Parsed inputs: %s", parsed_inputs)

    # Register SIGINT handler for graceful Ctrl+C (exit code 130)
    partial_results: dict[str, object] = {}

    def _handle_interrupt(signum: int, frame: object) -> None:  # noqa: ARG001
        click.echo("\nInterrupted. Partial results:", err=False)
        if partial_results:
            for k, v in partial_results.items():
                click.echo(f"  {k}: {v}")
        else:
            click.echo("  (no partial results collected)")
        sys.exit(130)

    signal.signal(signal.SIGINT, _handle_interrupt)

    # Attempt to run via the execution engine; fall back to a stub message.
    try:
        from agentry.executor import run_workflow

        result = run_workflow(
            workflow_path=workflow_path,
            inputs=parsed_inputs,
            target=target,
            output_format=output_format,
        )
        if output_format == OutputFormat.JSON:
            import json

            click.echo(json.dumps(result))
        else:
            click.echo(str(result))
    except ImportError:
        # Executor not yet implemented — emit a stub response.
        logger.debug("Executor module not available; emitting stub output.")
        if output_format == OutputFormat.JSON:
            import json

            stub: dict[str, object] = {
                "status": "not_implemented",
                "workflow": workflow_path,
                "inputs": parsed_inputs,
                "target": target,
            }
            click.echo(json.dumps(stub))
        else:
            click.echo(f"Running workflow: {workflow_path}")
            click.echo(f"Target: {target}")
            if parsed_inputs:
                for k, v in parsed_inputs.items():
                    click.echo(f"  Input {k}={v}")
            click.echo("(Executor not yet implemented)")


# ---------------------------------------------------------------------------
# Stub commands: setup, ci, registry
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def setup(ctx: click.Context) -> None:  # noqa: ARG001
    """Set up Agentry for the current environment.

    \b
    Note: This command is not yet implemented.

    \b
    Examples:
      agentry setup
    """
    click.echo("Not yet implemented")
    sys.exit(0)


@main.command()
@click.pass_context
def ci(ctx: click.Context) -> None:  # noqa: ARG001
    """Generate CI pipeline configuration for a workflow.

    \b
    Note: This command is not yet implemented.

    \b
    Examples:
      agentry ci
    """
    click.echo("Not yet implemented")
    sys.exit(0)


@main.command()
@click.pass_context
def registry(ctx: click.Context) -> None:  # noqa: ARG001
    """Browse and manage the workflow registry.

    \b
    Note: This command is not yet implemented.

    \b
    Examples:
      agentry registry
    """
    click.echo("Not yet implemented")
    sys.exit(0)


# ---------------------------------------------------------------------------
# keygen command
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--private-key",
    "private_key_path",
    default=None,
    metavar="PATH",
    help=(
        "Override the private key output path. "
        "Defaults to ~/.agentry/signing-key.pem."
    ),
)
@click.option(
    "--public-key",
    "public_key_path",
    default=None,
    metavar="PATH",
    help=(
        "Override the public key output path. "
        "Defaults to .agentry/public-key.pem (in the current directory)."
    ),
)
@click.pass_context
def keygen(
    ctx: click.Context,
    private_key_path: str | None,
    public_key_path: str | None,
) -> None:
    """Generate an Ed25519 signing keypair for workflow signing.

    The private key is saved outside the project directory so it is never
    accidentally committed to version control.  The public key is saved
    inside the project so it can be shared with collaborators via git.

    \b
    Default paths:
      Private key: ~/.agentry/signing-key.pem  (owner-only, mode 0600)
      Public key:  .agentry/public-key.pem     (commit this to your repo)

    \b
    Examples:
      agentry keygen
      agentry keygen --public-key .agentry/my-key.pem
    """
    from pathlib import Path

    from agentry.security.signing import generate_keypair

    obj = ctx.ensure_object(dict)
    output_format: str = obj.get("output_format", OutputFormat.TEXT)

    priv = Path(private_key_path) if private_key_path else None
    pub = Path(public_key_path) if public_key_path else None

    try:
        resolved_priv, resolved_pub = generate_keypair(
            private_key_path=priv,
            public_key_path=pub,
        )
    except OSError as exc:
        click.echo(f"Error: could not write key files: {exc}", err=True)
        sys.exit(1)

    if output_format == OutputFormat.JSON:
        import json

        click.echo(
            json.dumps(
                {
                    "status": "ok",
                    "private_key": str(resolved_priv),
                    "public_key": str(resolved_pub),
                }
            )
        )
    else:
        click.echo(f"Private key written to: {resolved_priv}")
        click.echo(f"Public key written to:  {resolved_pub}")
        click.echo("")
        click.echo("Next steps:")
        click.echo(f"  git add {resolved_pub}")
        click.echo("  git commit -m 'chore: add agentry public signing key'")
        click.echo("")
        click.echo(
            "Keep your private key safe. It should NEVER be committed to the repository."
        )


# Keep 'cli' as an alias so that callers using 'agentry.cli:cli' also work.
cli = main

if __name__ == "__main__":
    main()
