from __future__ import annotations
import asyncio
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from proto_harness.sandbox.types import (
    ApplyResult,
    HandbackResult,
    SandboxInfo,
    SandboxStatus,
)
logger = logging.getLogger(__name__)

_CAPTURE_IDENTITY = (
    "-c", "user.name=proto",
    "-c", "user.email=proto@localhost",
    "-c", "commit.gpgsign=false",
)


def _run_git(
    args: list[str],
    cwd: Path,
    timeout_s: float=30.0,
    with_identity: bool=False
)-> tuple[int,str,str]:
    cmd=["git"]
    if with_identity:
        cmd.extend(_CAPTURE_IDENTITY)
    cmd.extend(args)
    try:
        proc= subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        return proc.returncode,proc.stdout.strip(),proc.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error("Git command timed out after %ss: %s", timeout_s, " ".join(cmd))
        return 124, "", f"Git command timed out after {timeout_s}s"
    except Exception as exc:
        logger.error("Failed to execute git command %s: %s", " ".join(cmd), exc)
        return 1, "", str(exc)
    

def is_git_repo(path: Path) -> bool:
    """Return True if path is within a valid git working tree."""
    code, out, _ = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return code == 0 and out.lower() == "true"

def get_repo_root(path: Path) -> Path:
    """Resolve the root directory of the git repository containing path."""
    code, out, err = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
    if code != 0 or not out:
        raise RuntimeError(f"Path '{path}' is not inside a git repository: {err}")
    return Path(out).resolve()

def _ensure_exclude_sandboxes(repo_root:Path)->None:
    exclude_file= repo_root/".git"/"info"/"exclude"
    exclude_pattern = ".proto_harness/sandboxes/"
    if exclude_file.exists():
        try:
            content = exclude_file.read_text(encoding="utf-8")
            if exclude_pattern not in content:
                exclude_file.write_text(content.rstrip() + f"\n{exclude_pattern}\n", encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to update git exclude file: %s", exc)
    else:
        try:
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            exclude_file.write_text(f"{exclude_pattern}\n", encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to create git exclude file: %s", exc)


def create_sandbox(base_cwd: Path, sandbox_id: str | None = None) -> SandboxInfo:
    repo_root = get_repo_root(base_cwd)
    _ensure_exclude_sandboxes(repo_root)

    if not sandbox_id:
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        sandbox_id = f"sbx_{timestamp}_{short_id}"

    branch_name = f"proto/{sandbox_id}"
    sandbox_dir = (repo_root / ".proto_harness" / "sandboxes" / sandbox_id).resolve()
    code, base_commit, err = _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if code != 0:
        raise RuntimeError(f"Failed to get HEAD commit in '{repo_root}': {err}")

    sandbox_dir.parent.mkdir(parents=True, exist_ok=True)
    code, _, err = _run_git(
        ["worktree", "add", "-b", branch_name, str(sandbox_dir), "HEAD"],
        cwd=repo_root,
    )
    if code != 0:
        raise RuntimeError(f"Failed to create sandbox worktree '{sandbox_dir}': {err}")
    
    logger.info("Created isolated sandbox: %s at %s (branch: %s)", sandbox_id, sandbox_dir, branch_name)
    return SandboxInfo(
        sandbox_id=sandbox_id,
        base_repo=repo_root,
        sandbox_dir=sandbox_dir,
        branch_name=branch_name,
        base_commit=base_commit,
        status=SandboxStatus.ACTIVE,
    )

def get_sandbox_diff(sandbox: SandboxInfo) -> str:
    """Compute the unified diff between the sandbox and its base commit."""
    if not sandbox.sandbox_dir.exists():
        return ""
    # Include unstaged and committed changes against base commit
    code, diff_out, _ = _run_git(["diff", sandbox.base_commit], cwd=sandbox.sandbox_dir)
    return diff_out if code == 0 else ""


def handback_workspace(sandbox: SandboxInfo) -> HandbackResult:
    if not sandbox.sandbox_dir.exists():
        return HandbackResult(
            branch=sandbox.branch_name,
            committed=False,
            diff="",
            message="Sandbox directory does not exist.",
        )
    # Check for any dirty or untracked files
    code, status_out, _ = _run_git(["status", "--porcelain"], cwd=sandbox.sandbox_dir)
    has_changes = bool(status_out.strip())
    committed = False
    if has_changes:
        # Stage everything including new files
        _run_git(["add", "-A"], cwd=sandbox.sandbox_dir)
        # Commit using safe identity
        commit_code, _, commit_err = _run_git(
            ["commit", "-m", "proto: auto-captured sandbox changes"],
            cwd=sandbox.sandbox_dir,
            with_identity=True,
        )
        if commit_code == 0:
            committed = True
        else:
            logger.warning("Failed to auto-commit sandbox changes: %s", commit_err)
    # Get updated HEAD commit
    _, head_commit, _ = _run_git(["rev-parse", "HEAD"], cwd=sandbox.sandbox_dir)
    # Get modified files list
    _, files_out, _ = _run_git(
        ["diff", "--name-only", sandbox.base_commit, "HEAD"],
        cwd=sandbox.sandbox_dir,
    )
    modified_files = [line.strip() for line in files_out.splitlines() if line.strip()]
    # Get full unified diff
    _, full_diff, _ = _run_git(
        ["diff", sandbox.base_commit, "HEAD"],
        cwd=sandbox.sandbox_dir,
    )
    sandbox.status = SandboxStatus.COMMITTED
    msg = (
        f"Captured {len(modified_files)} modified file(s) onto branch '{sandbox.branch_name}'."
        if modified_files
        else "No modifications made in sandbox."
    )
    return HandbackResult(
        branch=sandbox.branch_name,
        committed=committed,
        diff=full_diff,
        modified_files=modified_files,
        commit_hash=head_commit or None,
        message=msg,
    )


def apply_sandbox(sandbox: SandboxInfo, target_cwd: Path | None = None) -> ApplyResult:
    """Apply the changes made in the sandbox back to the developer's main branch."""
    target_repo = target_cwd or sandbox.base_repo
    # Check if sandbox has uncommitted changes and secure them first
    handback_workspace(sandbox)
    # Attempt to merge sandbox branch cleanly into active branch
    code, out, err = _run_git(
        ["merge", "--no-ff", "-m", f"Merge sandbox '{sandbox.sandbox_id}'", sandbox.branch_name],
        cwd=target_repo,
        with_identity=True,
    )
    if code == 0:
        sandbox.status = SandboxStatus.APPLIED
        return ApplyResult(
            success=True,
            method="merge",
            message=f"Successfully merged sandbox '{sandbox.branch_name}' into active branch.",
        )
    # Check for merge conflicts
    _, status_out, _ = _run_git(["status", "--porcelain"], cwd=target_repo)
    conflicts = [
        line[3:].strip()
        for line in status_out.splitlines()
        if line.startswith("UU") or line.startswith("AA")
    ]
    # Abort the conflicting merge to leave the user's workspace untouched
    _run_git(["merge", "--abort"], cwd=target_repo)
    return ApplyResult(
        success=False,
        method="merge",
        message=f"Merge failed due to conflicts: {err or out}",
        conflicted_files=conflicts,
    )


def cleanup_sandbox(sandbox: SandboxInfo, delete_branch: bool = False) -> None:
    """Safely remove the worktree and clean up references."""
    if sandbox.sandbox_dir.exists():
        # Remove worktree registration and files
        code, _, err = _run_git(
            ["worktree", "remove", "--force", str(sandbox.sandbox_dir)],
            cwd=sandbox.base_repo,
        )
        if code != 0:
            logger.warning("git worktree remove failed: %s; falling back to shutil.rmtree", err)
            try:
                shutil.rmtree(sandbox.sandbox_dir, ignore_errors=True)
            except Exception as exc:
                logger.error("Failed to delete sandbox directory: %s", exc)
        _run_git(["worktree", "prune"], cwd=sandbox.base_repo)
    if delete_branch:
        _run_git(["branch", "-D", sandbox.branch_name], cwd=sandbox.base_repo)
        sandbox.status = SandboxStatus.DISCARDED


class SandboxContext:
    """Async context manager that wraps a task in an isolated Git worktree."""
    def __init__(
        self,
        cwd: Path,
        sandbox_id: str | None = None,
        auto_cleanup: bool = True,
        delete_branch: bool = False,
    ) -> None:
        self.cwd = cwd
        self.sandbox_id = sandbox_id
        self.auto_cleanup = auto_cleanup
        self.delete_branch = delete_branch
        self.sandbox: SandboxInfo | None = None
    async def __aenter__(self) -> SandboxInfo:
        # Run synchronous worktree creation in executor
        loop = asyncio.get_running_loop()
        self.sandbox = await loop.run_in_executor(
            None, create_sandbox, self.cwd, self.sandbox_id
        )
        return self.sandbox
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self.sandbox:
            return
        loop = asyncio.get_running_loop()
        # Always secure uncommitted changes before cleanup
        await loop.run_in_executor(None, handback_workspace, self.sandbox)
        if self.auto_cleanup:
            await loop.run_in_executor(
                None, cleanup_sandbox, self.sandbox, self.delete_branch
            )