"""Binder registry: discover and select environment binders.

Binders are discovered via ``importlib.metadata`` entry points in the
``agentry.binders`` group. The local binder is always registered as the
built-in default and is used when no ``--environment`` flag is provided.

Entry point format (in ``pyproject.toml``)::

    [project.entry-points."agentry.binders"]
    local = "agentry.binders.local:LocalBinder"
    my-custom-binder = "my_package.binder:MyBinder"
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import TYPE_CHECKING, Any

from agentry.binders.github_actions import GitHubActionsBinder
from agentry.binders.local import LocalBinder

if TYPE_CHECKING:
    from agentry.binders.protocol import EnvironmentBinder

logger = logging.getLogger(__name__)

# Name of the built-in default binder.
DEFAULT_BINDER_NAME = "local"

# Name of the built-in GitHub Actions binder.
GITHUB_ACTIONS_BINDER_NAME = "github-actions"


def discover_binders() -> dict[str, type[Any]]:
    """Discover all registered binder classes via entry points.

    Loads entry points from the ``agentry.binders`` group. The built-in
    ``local`` and ``github-actions`` binders are always present regardless
    of installed packages.

    Returns:
        Mapping of binder name to binder class. The ``local`` key always
        maps to :class:`~agentry.binders.local.LocalBinder` and the
        ``github-actions`` key always maps to
        :class:`~agentry.binders.github_actions.GitHubActionsBinder`.
    """
    binders: dict[str, type[Any]] = {
        DEFAULT_BINDER_NAME: LocalBinder,
        GITHUB_ACTIONS_BINDER_NAME: GitHubActionsBinder,
    }

    try:
        eps = importlib.metadata.entry_points(group="agentry.binders")
    except Exception:  # noqa: BLE001
        logger.debug("Failed to load agentry.binders entry points", exc_info=True)
        return binders

    for ep in eps:
        if ep.name == DEFAULT_BINDER_NAME:
            # Allow packages to override the local binder explicitly.
            logger.debug("Entry point overrides built-in local binder: %s", ep.value)
        try:
            binder_cls = ep.load()
            binders[ep.name] = binder_cls
            logger.debug("Discovered binder %r from %s", ep.name, ep.value)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to load binder %r from entry point %r",
                ep.name,
                ep.value,
                exc_info=True,
            )

    return binders


def get_binder(name: str | None = None) -> EnvironmentBinder:
    """Return an instantiated binder for the given name.

    If *name* is ``None`` or ``"local"``, the built-in
    :class:`~agentry.binders.local.LocalBinder` is returned without
    consulting entry points (fast path for the common case).

    Args:
        name: The binder name (e.g. ``"local"``). When ``None``, the default
            binder is returned.

    Returns:
        An instantiated binder that satisfies the
        :class:`~agentry.binders.protocol.EnvironmentBinder` protocol.

    Raises:
        KeyError: If *name* is specified but no binder with that name is found.
    """
    if name is None or name == DEFAULT_BINDER_NAME:
        return LocalBinder()

    if name == GITHUB_ACTIONS_BINDER_NAME:
        return GitHubActionsBinder()

    available = discover_binders()
    if name not in available:
        raise KeyError(
            f"No binder named {name!r} found. "
            f"Available binders: {sorted(available)!r}. "
            f"Install the appropriate package or check the --environment flag."
        )

    binder_cls = available[name]
    binder: EnvironmentBinder = binder_cls()
    return binder
