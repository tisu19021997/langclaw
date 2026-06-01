"""Factory for deepagents filesystem/shell backends.

deepagents abstracts the agent's file tools behind a swappable *backend*
(``BackendProtocol``).  Langclaw used to hard-code ``FilesystemBackend``; this
module lets operators pick any of the declarative backends from config and lets
developers inject fully-constructed instances (store, composite, sandbox) for
the advanced cases.

Follows the project's ``base.py`` + factory convention (cf.
``make_message_bus`` / ``make_checkpointer_backend``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepagents.backends import BackendProtocol

    from langclaw.config.schema import BackendConfig


def make_backend(config: BackendConfig, workspace_dir: Path) -> BackendProtocol:
    """Construct a deepagents backend from *config*.

    Args:
        config:        The resolved ``BackendConfig`` selecting the backend
                       kind and its parameters.
        workspace_dir: Default filesystem root for the filesystem-rooted
                       backends (``local_shell`` / ``filesystem``) when
                       ``config.root_dir`` is empty.

    Returns:
        A deepagents backend instance ready to pass to ``create_deep_agent``.

    Raises:
        ValueError: If ``config.backend`` names an unknown backend.
    """
    kind = config.backend
    root = Path(config.root_dir).expanduser() if config.root_dir else workspace_dir

    if kind == "local_shell":
        from deepagents.backends import LocalShellBackend

        return LocalShellBackend(
            root_dir=str(root),
            virtual_mode=config.virtual_mode,
            timeout=config.execute_timeout,
            max_output_bytes=config.max_output_bytes,
            env=config.env or None,
            inherit_env=config.inherit_env,
        )

    if kind == "filesystem":
        from deepagents.backends import FilesystemBackend

        return FilesystemBackend(root_dir=str(root), virtual_mode=config.virtual_mode)

    if kind == "state":
        from deepagents.backends import StateBackend

        return StateBackend()

    if kind == "store":
        from deepagents.backends import StoreBackend

        return StoreBackend()

    raise ValueError(
        f"Unknown agent backend {kind!r}. "
        "Expected one of: local_shell, filesystem, state, store. "
        "For store/composite/sandbox backends that need live objects, pass a "
        "constructed instance via Langclaw(backend=...) instead."
    )


def backend_root_dir(backend: BackendProtocol) -> Path | None:
    """Return the host filesystem root of a filesystem-rooted backend.

    deepagents' filesystem-rooted backends (``FilesystemBackend`` and its
    subclass ``LocalShellBackend``) expose their resolved root as a ``cwd``
    ``Path``.  Non-filesystem backends (``StateBackend``, ``StoreBackend``)
    have no host directory and return ``None``.

    Callers use this to gate behaviour that genuinely requires a real directory
    — the ``move_file`` / ``delete_file`` workspace tools and the on-disk
    ``AGENTS.md`` / skills setup — so those steps are skipped for backends that
    don't live on the local filesystem.
    """
    cwd = getattr(backend, "cwd", None)
    if cwd is None:
        return None
    return Path(cwd)
