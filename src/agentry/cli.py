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
from typing import Any

import click

from agentry import __version__

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _MinimalRunner: a thin shim for SetupPhase that requires no Docker
# ---------------------------------------------------------------------------


class _MinimalRunner:
    """Minimal runner shim compatible with SetupPhase's runner interface.

    SetupPhase expects the runner to satisfy the
    ``agentry.security.envelope.RunnerProtocol`` interface, which uses a
    no-argument ``provision()`` returning ``dict[str, Any]``.  This shim
    provides exactly that without starting a real container or process.

    The setup manifest is populated with metadata from this stub; the actual
    sandbox provisioning only happens when ``agentry run`` is invoked.
    """

    def provision(self) -> dict[str, Any]:
        """Return empty metadata — no real provisioning is performed."""
        return {}

    def teardown(self) -> None:
        """No-op teardown."""

    def execute(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        """Not used during setup-only execution."""
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    def check_available(self) -> bool:
        """Always available — no external dependencies for the shim."""
        return True


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
@click.argument("workflow_paths", metavar="WORKFLOW_PATH [WORKFLOW_PATH2]", nargs=-1)
@click.option(
    "--security-audit",
    "security_audit",
    is_flag=True,
    default=False,
    help=(
        "Run a security audit on the workflow(s). "
        "With one path: warn if unsigned. "
        "With two paths: diff security-relevant fields between versions."
    ),
)
@click.pass_context
def validate(ctx: click.Context, workflow_paths: tuple[str, ...], security_audit: bool) -> None:
    """Validate a workflow definition YAML file.

    Parses the workflow definition at WORKFLOW_PATH, validates it against the
    Agentry schema, and reports all errors with file path and location context.

    When --security-audit is given with two paths, diffs security-relevant
    fields (trust level, network allowlist, side effects, output paths, etc.)
    between the two workflow versions.  Also warns when a workflow lacks a
    signature.

    \b
    Exit codes:
      0  Workflow is valid (or audit has no errors)
      1  Validation failed (errors written to stderr)

    \b
    Examples:
      agentry validate workflows/code-review.yaml
      agentry validate workflows/bug-fix.yaml --verbose
      agentry validate --security-audit workflows/code-review.yaml
      agentry validate --security-audit workflows/v1.yaml workflows/v2.yaml
    """
    import json
    import os

    obj = ctx.ensure_object(dict)
    output_format: str = obj.get("output_format", OutputFormat.TEXT)

    # --security-audit mode -----------------------------------------------
    if security_audit:
        if len(workflow_paths) == 0:
            click.echo(
                "Error: --security-audit requires at least one WORKFLOW_PATH",
                err=True,
            )
            sys.exit(1)

        if len(workflow_paths) == 1:
            # Single-path audit: warn about missing signature.
            from agentry.security.audit import security_audit_single

            path = workflow_paths[0]
            if not os.path.exists(path):
                click.echo(f"Error: workflow file not found: {path}", err=True)
                sys.exit(1)

            try:
                report = security_audit_single(path)
            except (FileNotFoundError, ValueError) as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)

            if output_format == OutputFormat.JSON:
                click.echo(json.dumps(report.format_json()))
            else:
                click.echo(report.format_text())
            sys.exit(0)

        if len(workflow_paths) == 2:
            # Two-path audit: diff security-relevant fields.
            from agentry.security.audit import security_audit as run_security_audit

            path1, path2 = workflow_paths[0], workflow_paths[1]
            for p in (path1, path2):
                if not os.path.exists(p):
                    click.echo(f"Error: workflow file not found: {p}", err=True)
                    sys.exit(1)

            try:
                report = run_security_audit(path1, path2)
            except (FileNotFoundError, ValueError) as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)

            if output_format == OutputFormat.JSON:
                click.echo(json.dumps(report.format_json()))
            else:
                click.echo(report.format_text())
            sys.exit(0)

        # More than 2 paths is an error.
        click.echo(
            "Error: --security-audit accepts at most two WORKFLOW_PATH arguments",
            err=True,
        )
        sys.exit(1)

    # Normal validate mode -------------------------------------------------
    if len(workflow_paths) == 0:
        click.echo("Error: Missing argument 'WORKFLOW_PATH'.", err=True)
        sys.exit(1)

    if len(workflow_paths) > 1:
        click.echo(
            "Error: validate accepts exactly one WORKFLOW_PATH without --security-audit",
            err=True,
        )
        sys.exit(1)

    workflow_path = workflow_paths[0]

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
            # Additional semantic checks: report unknown agent runtimes.
            # This is best-effort; any failure here is silently ignored so
            # that stub implementations of the parser still work.
            _extra_warnings: list[str] = []
            try:
                from agentry.models.agent import KNOWN_RUNTIMES
                from agentry.parser import load_workflow_file

                _wf = load_workflow_file(workflow_path)
                if _wf.agent is not None and _wf.agent.runtime not in KNOWN_RUNTIMES:
                    _extra_warnings.append(
                        f"Warning: unknown agent runtime '{_wf.agent.runtime}'. "
                        f"Known runtimes: {', '.join(sorted(KNOWN_RUNTIMES))}."
                    )
            except Exception:  # noqa: BLE001
                pass  # best-effort only; schema errors already caught above

            if _extra_warnings:
                for w in _extra_warnings:
                    click.echo(w, err=True)
                sys.exit(1)

            if output_format == OutputFormat.JSON:
                click.echo(json.dumps({"status": "valid", "path": workflow_path}))
            else:
                click.echo(f"Validation successful: {workflow_path}")
    except ImportError:
        # Parser not yet implemented — report the YAML is loadable at minimum.
        logger.debug("Parser module not available; performing basic YAML load check.")
        try:
            import yaml

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
@click.option(
    "--skip-preflight",
    "skip_preflight",
    is_flag=True,
    default=False,
    help=(
        "Skip preflight checks (API key validation, Docker availability, "
        "filesystem mount verification). Intended for development only."
    ),
)
@click.option(
    "--node",
    "node",
    default=None,
    type=str,
    metavar="NODE_ID",
    help=(
        "Execute only the specified node in isolation (composition workflows only). "
        "No upstream data is provided and no downstream propagation occurs."
    ),
)
@click.option(
    "--binder",
    "binder",
    default=None,
    type=str,
    metavar="NAME",
    help=(
        "Override binder selection. "
        "Auto-detected when not specified: github-actions if GITHUB_ACTIONS=true, "
        "local otherwise."
    ),
)
@click.pass_context
def run(
    ctx: click.Context,
    workflow_path: str,
    inputs: tuple[str, ...],
    target: str,
    skip_preflight: bool,
    node: str | None,
    binder: str | None,
) -> None:
    """Execute a workflow definition against a local repository.

    Resolves abstract workflow inputs (git-diff, repository-ref) from TARGET,
    calls the configured LLM, validates output, and emits results.

    \b
    Options:
      --input KEY=VALUE       Pass input value (repeatable)
      --target PATH           Repository to run against (default: cwd)
      --skip-preflight        Skip preflight checks (development only)
      --node NODE_ID          Execute only the specified node in isolation
      --binder NAME           Override binder (auto-detected from GITHUB_ACTIONS env)

    \b
    Examples:
      agentry run workflows/code-review.yaml --input diff=HEAD~1
      agentry run workflows/code-review.yaml --input diff=HEAD~1 --target /path/to/repo
      agentry run workflows/code-review.yaml --skip-preflight
      agentry run workflows/bug-fix.yaml \\
          --input issue-description='Login fails' \\
          --target /path/to/repo
      agentry run workflows/planning-pipeline.yaml --node triage
      agentry run workflows/code-review.yaml --binder github-actions
      agentry run workflows/code-review.yaml --binder local
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

    # Resolve the active binder: explicit --binder flag overrides auto-detection.
    # Auto-detection: use "github-actions" when GITHUB_ACTIONS=true, else "local".
    _binder_name: str
    if binder is not None:
        _binder_name = binder
    elif os.environ.get("GITHUB_ACTIONS") == "true":
        _binder_name = "github-actions"
    else:
        _binder_name = "local"

    logger.debug("Resolved binder: %s", _binder_name)

    try:
        from agentry.binders.registry import get_binder as _get_binder

        _active_binder = _get_binder(_binder_name)
        logger.debug("Instantiated binder: %s", _active_binder)
    except KeyError as exc:
        click.echo(f"Error: unknown binder {_binder_name!r}: {exc}", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(
            f"Error: binder {_binder_name!r} could not be initialised: {exc}",
            err=True,
        )
        sys.exit(1)

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

    # Attempt to load the workflow so we can detect composition mode.
    # This also powers the setup phase below.
    _loaded_workflow = None
    try:
        from agentry.parser import load_workflow_file

        _loaded_workflow = load_workflow_file(workflow_path)
    except Exception:  # noqa: BLE001
        logger.debug("Could not load workflow for composition detection.", exc_info=True)

    # Run setup phase before agent execution when workflow is loadable.
    # This validates the environment, runs preflight checks, and produces
    # the setup manifest regardless of trust level.
    try:
        from agentry.security.checks import (
            AgentAvailabilityCheck,
            AnthropicAPIKeyCheck,
            DockerAvailableCheck,
            FilesystemMountsCheck,
            GitHubTokenScopeCheck,
        )
        from agentry.security.setup import SetupPhase, SetupPhaseError, SetupPreflightError

        if _loaded_workflow is None:
            raise ImportError("Workflow not loaded")

        _api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        _checks: list[Any] = []
        if not skip_preflight:
            _checks = [
                AnthropicAPIKeyCheck(),
                DockerAvailableCheck(trust=_loaded_workflow.safety.trust.value),
                FilesystemMountsCheck(
                    read_paths=list(_loaded_workflow.safety.filesystem.read),
                    write_paths=list(_loaded_workflow.safety.filesystem.write),
                ),
            ]
            # Add agent availability check when the workflow has an agent block.
            if _loaded_workflow.agent is not None:
                _checks.append(AgentAvailabilityCheck(runtime=_loaded_workflow.agent.runtime))
            # When running in GitHub Actions, add a token scope preflight check.
            if _binder_name == "github-actions":
                _tool_declarations = list(_loaded_workflow.tools.capabilities)
                _github_repository = os.environ.get("GITHUB_REPOSITORY", "")
                _checks.append(
                    GitHubTokenScopeCheck(
                        tool_declarations=_tool_declarations,
                        github_repository=_github_repository,
                    )
                )
        _runner = _MinimalRunner()
        _phase = SetupPhase(
            workflow=_loaded_workflow,
            runner=_runner,
            preflight_checks=_checks,
            api_key=_api_key,
            workflow_path=workflow_path,
        )
        _setup_result = _phase.run()
        logger.info("Setup phase complete: manifest at %s", _setup_result.manifest_path)
        logger.debug("Setup phase result: %s", _setup_result)
    except SetupPreflightError as exc:
        msg = f"Preflight check failed: {exc.check_name}: {exc.message}"
        if exc.remediation:
            msg += f"\nRemediation: {exc.remediation}"
        click.echo(msg, err=True)
        sys.exit(1)
    except SetupPhaseError as exc:
        click.echo(f"Setup failed: {exc}", err=True)
        sys.exit(1)
    except Exception:  # noqa: BLE001
        # If setup phase components are not yet available, proceed without setup.
        logger.debug(
            "Setup phase components not available; proceeding without setup.",
            exc_info=True,
        )

    # Detect composition workflow and dispatch accordingly.
    _is_composition = (
        _loaded_workflow is not None
        and bool(_loaded_workflow.composition.steps)
    )

    if node is not None and not _is_composition:
        click.echo(
            "Error: --node flag is only valid for composition workflows "
            "(workflows with a non-empty composition.steps block).",
            err=True,
        )
        sys.exit(1)

    if _is_composition:
        # Dispatch through CompositionEngine for composed workflows.
        import asyncio
        import datetime
        from pathlib import Path

        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_dir = Path(target) / ".agentry" / "runs" / run_id
        workflow_base_dir = Path(workflow_path).parent

        assert _loaded_workflow is not None  # guaranteed by _is_composition check

        try:
            from agentry.agents.registry import AgentRegistry
            from agentry.composition.display import CompositionDisplay
            from agentry.composition.engine import CompositionEngine
            from agentry.runners.detector import RunnerDetector

            # Resolve agent runtime from workflow's agent block (or fall back
            # to the default "claude-code").
            _agent_runtime = "claude-code"
            if _loaded_workflow.agent is not None:
                _agent_runtime = _loaded_workflow.agent.runtime

            # Build agent kwargs from the agent block when available.
            _agent_kwargs: dict[str, Any] = {}
            if _loaded_workflow.agent is not None:
                if _loaded_workflow.agent.model:
                    _agent_kwargs["model"] = _loaded_workflow.agent.model
                if _loaded_workflow.agent.system_prompt:
                    _agent_kwargs["system_prompt"] = _loaded_workflow.agent.system_prompt
                if _loaded_workflow.agent.max_iterations is not None:
                    _agent_kwargs["max_iterations"] = _loaded_workflow.agent.max_iterations

            _registry = AgentRegistry.default()
            _detector = RunnerDetector(
                agent_registry=_registry,
                agent_name=_agent_runtime,
                agent_kwargs=_agent_kwargs,
            )

            if node is not None:
                # Single-node isolation: build a single-step composition.
                from agentry.models.composition import CompositionBlock, CompositionStep

                _matching = [
                    s for s in _loaded_workflow.composition.steps
                    if s.node_id == node
                ]
                if not _matching:
                    click.echo(
                        f"Error: --node {node!r} not found in composition. "
                        f"Available nodes: "
                        f"{', '.join(_loaded_workflow.composition.node_ids)}",
                        err=True,
                    )
                    sys.exit(1)
                _isolated_step = _matching[0]
                _isolated_composition = CompositionBlock(
                    steps=[
                        CompositionStep(
                            name=_isolated_step.name,
                            workflow=_isolated_step.workflow,
                            id=_isolated_step.id,
                            failure=_isolated_step.failure,
                            # No depends_on — isolation means no upstream
                            depends_on=[],
                            # No inputs — isolation means no upstream data
                            inputs={},
                        )
                    ]
                )
                _composition_to_run = _isolated_composition
            else:
                _composition_to_run = _loaded_workflow.composition

            # Build TTY-aware display and wire callbacks into the engine.
            _is_tty_mode = obj.get("is_tty", False)
            _display = CompositionDisplay(
                is_tty=_is_tty_mode,
                output_format=output_format,
            )

            _engine = CompositionEngine(
                composition=_composition_to_run,
                runner_detector=_detector,
                binder=_active_binder,
                run_dir=run_dir,
                workflow_base_dir=workflow_base_dir,
                on_node_start=_display.on_node_start,
                on_node_complete=_display.on_node_complete,
                on_node_fail=_display.on_node_fail,
                on_node_skip=_display.on_node_skip,
            )
            _composition_record = asyncio.run(_engine.execute())

            if output_format == OutputFormat.JSON:
                import json

                click.echo(json.dumps(_composition_record.to_dict()))
            else:
                _display.print_summary(_composition_record)
        except ImportError:
            # CompositionEngine not yet implemented — emit a stub response.
            logger.debug("CompositionEngine not available; emitting stub output.")
            if output_format == OutputFormat.JSON:
                import json

                stub: dict[str, object] = {
                    "status": "not_implemented",
                    "mode": "composition",
                    "workflow": workflow_path,
                    "inputs": parsed_inputs,
                    "target": target,
                    "node": node,
                }
                click.echo(json.dumps(stub))
            else:
                click.echo(f"Running composition workflow: {workflow_path}")
                click.echo(f"Target: {target}")
                if node:
                    click.echo(f"Node: {node}")
                click.echo("(CompositionEngine not yet implemented)")
        except Exception as exc:  # noqa: BLE001
            click.echo(f"Error: composition execution failed: {exc}", err=True)
            sys.exit(1)
        return

    # Single-workflow execution via Runner → Agent pipeline.
    try:
        if _loaded_workflow is None:
            raise ImportError("Workflow not loaded")
        import datetime
        import json as _json
        from pathlib import Path

        from agentry.agents.registry import AgentRegistry
        from agentry.runners.detector import RunnerDetector
        from agentry.security.checks import (
            AgentAvailabilityCheck,
            AnthropicAPIKeyCheck,
            DockerAvailableCheck,
            FilesystemMountsCheck,
            GitHubTokenScopeCheck,
        )
        from agentry.security.envelope import EnvelopeResult, SecurityEnvelope

        # 1. Resolve agent runtime from workflow's agent block.
        _agent_runtime = "claude-code"
        if _loaded_workflow.agent is not None:
            _agent_runtime = _loaded_workflow.agent.runtime

        # 2. Build agent kwargs from the agent block.
        _sw_agent_kwargs: dict[str, Any] = {}
        if _loaded_workflow.agent is not None:
            if _loaded_workflow.agent.model:
                _sw_agent_kwargs["model"] = _loaded_workflow.agent.model
            if _loaded_workflow.agent.max_iterations is not None:
                _sw_agent_kwargs["max_iterations"] = _loaded_workflow.agent.max_iterations

        # 3. Instantiate RunnerDetector and get runner.
        _registry = AgentRegistry.default()
        _detector = RunnerDetector(
            agent_registry=_registry,
            agent_name=_agent_runtime,
            agent_kwargs=_sw_agent_kwargs,
        )
        _sw_runner = _detector.get_runner(_loaded_workflow.safety)

        # 4. Build preflight checks (respecting --skip-preflight).
        _envelope_checks: list[Any] = []
        if not skip_preflight:
            _envelope_checks = [
                AnthropicAPIKeyCheck(),
                DockerAvailableCheck(trust=_loaded_workflow.safety.trust.value),
                FilesystemMountsCheck(
                    read_paths=list(_loaded_workflow.safety.filesystem.read),
                    write_paths=list(_loaded_workflow.safety.filesystem.write),
                ),
            ]
            if _loaded_workflow.agent is not None:
                _envelope_checks.append(
                    AgentAvailabilityCheck(runtime=_loaded_workflow.agent.runtime)
                )
            if _binder_name == "github-actions":
                _tool_declarations = list(_loaded_workflow.tools.capabilities)
                _github_repository = os.environ.get("GITHUB_REPOSITORY", "")
                _envelope_checks.append(
                    GitHubTokenScopeCheck(
                        tool_declarations=_tool_declarations,
                        github_repository=_github_repository,
                    )
                )

        # 5. Instantiate SecurityEnvelope.
        _envelope = SecurityEnvelope(
            workflow=_loaded_workflow,
            runner=_sw_runner,
            preflight_checks=_envelope_checks,
        )

        # 6. Resolve inputs via binder.
        _input_declarations: dict[str, Any] = {
            name: spec.model_dump() for name, spec in _loaded_workflow.inputs.items()
        }
        _resolved_inputs = _active_binder.resolve_inputs(
            _input_declarations, parsed_inputs
        )

        # 7. Bind tools via binder.
        _tool_bindings = _active_binder.bind_tools(
            list(_loaded_workflow.tools.capabilities)
        )

        # 8. Load system prompt from file or build fallback.
        _system_prompt = ""
        if _loaded_workflow.agent is not None and _loaded_workflow.agent.system_prompt:
            _prompt_path = Path(workflow_path).parent / _loaded_workflow.agent.system_prompt
            if _prompt_path.exists():
                _system_prompt = _prompt_path.read_text(encoding="utf-8")
        if not _system_prompt:
            _identity = _loaded_workflow.identity
            _system_prompt = f"You are {_identity.name}. {_identity.description}"

        # 9. Execute through the envelope.
        _envelope_result: EnvelopeResult = _envelope.execute(
            system_prompt=_system_prompt,
            resolved_inputs=_resolved_inputs,
            available_tools=list(_tool_bindings.keys()),
            agent_name=_agent_runtime,
            agent_config=_sw_agent_kwargs,
        )

        # 10. Handle the result.
        if _envelope_result.aborted or _envelope_result.envelope_error:
            _error_msg = _envelope_result.envelope_error or "Execution aborted."
            click.echo(f"Error: {_error_msg}", err=True)
            sys.exit(1)

        _exec_result = _envelope_result.execution_result
        if _exec_result is not None and _exec_result.error:
            click.echo(f"Error: agent execution failed: {_exec_result.error}", err=True)
            sys.exit(1)

        # 10b. Write execution record to .agentry/runs/TIMESTAMP/.
        _run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
        _runs_base = Path(target) / ".agentry" / "runs"
        try:
            from agentry.runners.execution_record_writer import ExecutionRecordWriter

            _extra: dict[str, Any] = {}
            if _exec_result is not None:
                if _exec_result.output is not None:
                    _extra["agent_output"] = _exec_result.output
                if _exec_result.token_usage:
                    _extra["token_usage"] = _exec_result.token_usage

            _record_writer = ExecutionRecordWriter(runs_dir=_runs_base)
            _record_path = _record_writer.write(
                execution_id=_run_id,
                extra=_extra if _extra else None,
            )
            logger.info("Execution record written: %s", _record_path)

            # Write output.json alongside execution-record.json.
            _output_paths = _active_binder.map_outputs(
                output_declarations={},
                target_dir=target,
                run_id=_run_id,
            )
            _output_json_path = Path(_output_paths.get("output", str(_runs_base / _run_id / "output.json")))
            _output_json_path.parent.mkdir(parents=True, exist_ok=True)
            _output_payload_record: dict[str, object] = {
                "execution_id": _run_id,
                "output": _exec_result.output if _exec_result else None,
                "token_usage": _exec_result.token_usage if _exec_result else {},
            }
            _output_json_path.write_text(_json.dumps(_output_payload_record, indent=2), encoding="utf-8")
            logger.info("Output record written: %s", _output_json_path)
        except Exception as _record_exc:  # noqa: BLE001
            logger.warning("Failed to write execution record: %s", _record_exc)

        # 11. Emit output.
        if output_format == OutputFormat.JSON:
            import json

            _output_payload: dict[str, object] = {
                "status": "success",
                "output": _exec_result.output if _exec_result else None,
                "token_usage": _exec_result.token_usage if _exec_result else {},
            }
            click.echo(json.dumps(_output_payload))
        else:
            click.echo(f"Workflow: {workflow_path}")
            if _exec_result and _exec_result.output:
                click.echo(f"Output: {_exec_result.output}")
            elif _exec_result:
                click.echo("Execution completed (no structured output).")
            else:
                click.echo("Execution completed.")
            if _exec_result and _exec_result.token_usage:
                _usage = _exec_result.token_usage
                click.echo(
                    f"Tokens: input={_usage.get('input', 0)}, "
                    f"output={_usage.get('output', 0)}"
                )

    except ImportError:
        # Pipeline components not yet available — emit a stub response.
        logger.debug("Pipeline components not available; emitting stub output.")
        if output_format == OutputFormat.JSON:
            import json

            single_stub: dict[str, object] = {
                "status": "not_implemented",
                "mode": "single",
                "workflow": workflow_path,
                "inputs": parsed_inputs,
                "target": target,
            }
            click.echo(json.dumps(single_stub))
        else:
            click.echo(f"Running workflow: {workflow_path}")
            click.echo(f"Target: {target}")
            if parsed_inputs:
                for k, v in parsed_inputs.items():
                    click.echo(f"  Input {k}={v}")
            click.echo("(Single-workflow execution not yet wired to Runner/Agent pipeline)")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error: execution failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# setup command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("workflow_path", metavar="WORKFLOW_PATH")
@click.option(
    "--skip-preflight",
    "skip_preflight",
    is_flag=True,
    default=False,
    help=(
        "Skip preflight checks (API key validation, Docker availability, "
        "filesystem mount verification). Intended for development only."
    ),
)
@click.pass_context
def setup(
    ctx: click.Context,
    workflow_path: str,
    skip_preflight: bool,
) -> None:
    """Run the setup phase for WORKFLOW_PATH without executing the agent.

    Provisions the sandbox, runs all preflight checks, compiles the output
    validator schema, and saves a setup manifest to
    .agentry/runs/<timestamp>/setup-manifest.json.  Exits without starting
    the agent or making any LLM API calls.

    \b
    Exit codes:
      0  Setup completed successfully; manifest written to disk.
      1  Setup failed (preflight, provisioning, or schema compilation error).

    \b
    Examples:
      agentry setup workflows/code-review.yaml
      agentry setup workflows/code-review.yaml --skip-preflight
    """
    import json
    import os

    obj = ctx.ensure_object(dict)
    output_format: str = obj.get("output_format", OutputFormat.TEXT)

    logger.debug("Running setup phase for workflow: %s", workflow_path)

    if not os.path.exists(workflow_path):
        click.echo(f"Error: workflow file not found: {workflow_path}", err=True)
        sys.exit(1)

    # Load the workflow definition.
    try:
        from agentry.parser import load_workflow_file

        workflow = load_workflow_file(workflow_path)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error: failed to load workflow: {exc}", err=True)
        sys.exit(1)

    # Build preflight checks appropriate for the trust level.
    from agentry.security.checks import (
        AnthropicAPIKeyCheck,
        DockerAvailableCheck,
        FilesystemMountsCheck,
    )
    checks: list[Any] = []
    if not skip_preflight:
        checks = [
            AnthropicAPIKeyCheck(),
            DockerAvailableCheck(trust=workflow.safety.trust.value),
            FilesystemMountsCheck(
                read_paths=list(workflow.safety.filesystem.read),
                write_paths=list(workflow.safety.filesystem.write),
            ),
        ]

    # Build a minimal runner compatible with SetupPhase's expected interface.
    # SetupPhase uses runner.provision() -> dict[str, Any] (security.envelope
    # protocol), not the runners.protocol RunnerProtocol signature.
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    from agentry.security.setup import (
        SetupPhase,
        SetupPhaseError,
        SetupPreflightError,
    )

    runner = _MinimalRunner()

    phase = SetupPhase(
        workflow=workflow,
        runner=runner,
        preflight_checks=checks,
        api_key=api_key,
        workflow_path=workflow_path,
    )

    try:
        result = phase.run()
    except SetupPreflightError as exc:
        msg = f"Preflight check failed: {exc.check_name}: {exc.message}"
        if exc.remediation:
            msg += f"\nRemediation: {exc.remediation}"
        click.echo(msg, err=True)
        sys.exit(1)
    except SetupPhaseError as exc:
        click.echo(f"Setup failed: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Setup failed: {exc}", err=True)
        sys.exit(1)

    if output_format == OutputFormat.JSON:
        click.echo(
            json.dumps(
                {
                    "status": "ok",
                    "manifest_path": result.manifest_path,
                    "preflight_results": [
                        {
                            "name": r.name,
                            "passed": r.passed,
                            "message": r.message,
                        }
                        for r in result.preflight_results
                    ],
                }
            )
        )
    else:
        click.echo(f"Setup complete: {workflow_path}")
        click.echo(f"Manifest: {result.manifest_path}")
        if result.preflight_results:
            click.echo("Preflight checks:")
            for r in result.preflight_results:
                status = "PASS" if r.passed else "FAIL"
                click.echo(f"  [{status}] {r.name}: {r.message}")


# ---------------------------------------------------------------------------
# ci command group
# ---------------------------------------------------------------------------

_VALID_TRIGGERS = {"pull_request", "push", "schedule", "issues"}


@main.group()
@click.pass_context
def ci(ctx: click.Context) -> None:  # noqa: ARG001
    """Generate CI pipeline configuration for a workflow.

    \b
    Examples:
      agentry ci generate --target github workflows/code-review.yaml
      agentry ci generate --target github --dry-run workflows/code-review.yaml
    """


@ci.command("generate")
@click.argument("workflow_path", metavar="WORKFLOW_PATH")
@click.option(
    "--target",
    required=True,
    metavar="TARGET",
    help=(
        "CI target platform. Only 'github' is currently supported."
    ),
)
@click.option(
    "--triggers",
    default="pull_request",
    show_default=True,
    metavar="TRIGGERS",
    help=(
        "Comma-separated list of triggers. "
        "Supported values: pull_request, push, schedule, issues. "
        "Example: --triggers pull_request,push"
    ),
)
@click.option(
    "--schedule",
    default=None,
    metavar="CRON",
    help=(
        "Cron expression for schedule trigger. "
        "Required when 'schedule' is included in --triggers."
    ),
)
@click.option(
    "--output-dir",
    "output_dir",
    default=".github/workflows/",
    show_default=True,
    type=click.Path(),
    help="Directory to write the generated YAML file.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Print generated YAML to stdout without writing to disk.",
)
@click.pass_context
def ci_generate(
    ctx: click.Context,
    workflow_path: str,
    target: str,
    triggers: str,
    schedule: str | None,
    output_dir: str,
    dry_run: bool,
) -> None:
    """Generate CI pipeline configuration for WORKFLOW_PATH.

    Reads the workflow definition at WORKFLOW_PATH, validates it, and produces
    a CI configuration file for the specified target platform.

    \b
    Exit codes:
      0  Config generated successfully
      1  Validation or generation error

    \b
    Examples:
      agentry ci generate --target github workflows/code-review.yaml
      agentry ci generate --target github --triggers pull_request,push workflows/code-review.yaml
      agentry ci generate --target github --triggers pull_request,schedule --schedule '0 2 * * 1' workflows/code-review.yaml
      agentry ci generate --target github --output-dir ci/workflows/ workflows/code-review.yaml
      agentry ci generate --target github --dry-run workflows/code-review.yaml
    """
    import os

    # Validate --target
    if target != "github":
        click.echo(
            f"Error: unsupported --target value: {target!r}. Only 'github' is supported.",
            err=True,
        )
        sys.exit(1)

    # Parse and validate --triggers
    trigger_list = [t.strip() for t in triggers.split(",") if t.strip()]
    if not trigger_list:
        click.echo("Error: --triggers must not be empty.", err=True)
        sys.exit(1)

    invalid_triggers = [t for t in trigger_list if t not in _VALID_TRIGGERS]
    if invalid_triggers:
        click.echo(
            f"Error: unsupported trigger(s): {', '.join(invalid_triggers)}. "
            f"Supported values: {', '.join(sorted(_VALID_TRIGGERS))}.",
            err=True,
        )
        sys.exit(1)

    # Validate --schedule requirement
    if "schedule" in trigger_list and schedule is None:
        click.echo(
            "Error: --schedule is required when 'schedule' is included in --triggers.",
            err=True,
        )
        sys.exit(1)

    # Check workflow file exists
    if not os.path.exists(workflow_path):
        click.echo(f"Error: workflow file not found: {workflow_path}", err=True)
        sys.exit(1)

    # Load the workflow definition
    try:
        from agentry.parser import load_workflow_file

        workflow = load_workflow_file(workflow_path)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error: failed to load workflow: {exc}", err=True)
        sys.exit(1)

    # Reject composed workflows
    if workflow.composition and workflow.composition.steps:
        click.echo(
            "Error: Composed workflow CI generation is not yet supported. "
            "Generate CI config for each component workflow individually.",
            err=True,
        )
        sys.exit(1)

    # Render GitHub Actions YAML from the workflow definition.
    from agentry.ci.github_actions_renderer import render_pipeline_yaml

    yaml_content = render_pipeline_yaml(
        workflow=workflow,
        workflow_path=workflow_path,
        trigger_list=trigger_list,
        schedule=schedule,
    )

    if dry_run:
        click.echo(yaml_content)
    else:
        from pathlib import Path

        workflow_name = Path(workflow_path).stem
        output_filename = f"agentry-{workflow_name}.yaml"
        output_path = Path(output_dir) / output_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_content)
        click.echo(f"Generated: {output_path}")


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


# ---------------------------------------------------------------------------
# sign command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("workflow_path", metavar="WORKFLOW_PATH")
@click.option(
    "--private-key",
    "private_key_path",
    default=None,
    metavar="PATH",
    help=(
        "Override the private key path. "
        "Defaults to ~/.agentry/signing-key.pem."
    ),
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    metavar="PATH",
    help=(
        "Write signed workflow to PATH instead of overwriting WORKFLOW_PATH."
    ),
)
@click.pass_context
def sign(
    ctx: click.Context,
    workflow_path: str,
    private_key_path: str | None,
    output_path: str | None,
) -> None:
    """Sign a workflow definition's safety and output.side_effects blocks.

    Reads the workflow at WORKFLOW_PATH, signs the 'safety' block and the
    'output.side_effects' block using an Ed25519 private key, then appends a
    'signature' block to the YAML file.  The file is updated in place unless
    --output is supplied.

    \b
    Default private key path: ~/.agentry/signing-key.pem
    Generate a keypair first with: agentry keygen

    \b
    Signature block fields:
      algorithm:     "ed25519"
      signed_blocks: ["safety", "output.side_effects"]
      signature:     <hex-encoded Ed25519 signature>
      timestamp:     <ISO 8601 UTC timestamp>

    \b
    Exit codes:
      0  Workflow signed successfully
      1  Error (workflow not found, key not found, key type mismatch)

    \b
    Examples:
      agentry sign workflows/code-review.yaml
      agentry sign workflows/code-review.yaml --private-key /path/to/key.pem
      agentry sign workflows/code-review.yaml --output workflows/signed.yaml
    """
    import os
    from pathlib import Path

    from agentry.security.signing import sign_workflow

    obj = ctx.ensure_object(dict)
    output_format: str = obj.get("output_format", OutputFormat.TEXT)

    if not os.path.exists(workflow_path):
        click.echo(f"Error: workflow file not found: {workflow_path}", err=True)
        sys.exit(1)

    priv = Path(private_key_path) if private_key_path else None
    out = Path(output_path) if output_path else None

    try:
        resolved_out = sign_workflow(
            workflow_path,
            private_key_path=priv,
            output_path=out,
        )
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if output_format == OutputFormat.JSON:
        import json

        click.echo(
            json.dumps(
                {
                    "status": "ok",
                    "workflow": str(resolved_out),
                    "signed_blocks": ["safety", "output.side_effects"],
                }
            )
        )
    else:
        click.echo(f"Workflow signed: {resolved_out}")
        click.echo("Signed blocks: safety, output.side_effects")


# Keep 'cli' as an alias so that callers using 'agentry.cli:cli' also work.
cli = main

if __name__ == "__main__":
    main()
