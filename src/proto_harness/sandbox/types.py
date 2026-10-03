from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class SandboxStatus(str, Enum):
    """The lifecycle status of a workspace sandbox."""

    ACTIVE = "active"
    COMMITTED = "committed"
    APPLIED = "applied"
    DISCARDED = "discarded"
    FAILED = "failed"


@dataclass(slots=True)
class SandboxInfo:
    """Metadata representing an active or completed isolated workspace sandbox.

    Attributes:
        sandbox_id: Unique string identifying the sandbox session.
        base_repo: The path to the developer's original host repository.
        sandbox_dir: Path to the isolated physical worktree directory.
        branch_name: Name of the git branch created for this sandbox.
        base_commit: The git commit hash of HEAD when the sandbox was created.
        status: The current lifecycle status of the sandbox.
        created_at: ISO-8601 timestamp of creation.
    """

    sandbox_id: str
    base_repo: Path
    sandbox_dir: Path
    branch_name: str
    base_commit: str
    status: SandboxStatus = SandboxStatus.ACTIVE
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True, slots=True)
class HandbackResult:
    """Outcome of securing and analyzing changes made in a sandbox.

    Modeled after the 'never-lose-results' guarantee:
    ensures all agent modifications are tracked, diffed, and safely committed.

    Attributes:
        branch: The session branch name containing the modifications.
        committed: Whether uncommitted changes were auto-committed.
        diff: The unified diff string comparing sandbox against base commit.
        modified_files: List of file paths changed, added, or deleted.
        commit_hash: The commit hash of the sandbox branch HEAD (if committed).
        message: Human-readable summary of the hand-back state.
    """

    branch: str
    committed: bool
    diff: str
    modified_files: list[str] = field(default_factory=list)
    commit_hash: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The result of applying sandbox changes back to the base workspace.

    Attributes:
        success: True if the merge or patch succeeded cleanly without conflicts.
        method: The method used to apply changes (e.g. 'merge', 'patch').
        message: Informational message about the application result.
        conflicted_files: List of files with merge or patch conflicts (if any).
    """
    success: bool
    method: str
    message: str
    conflicted_files: list[str] = field(default_factory=list)
