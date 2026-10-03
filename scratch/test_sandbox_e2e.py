import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from proto_harness.sandbox import (
    SandboxContext,
    SandboxStatus,
    apply_sandbox,
    cleanup_sandbox,
    create_sandbox,
    get_repo_root,
    get_sandbox_diff,
    handback_workspace,
    is_git_repo,
)


def _git(cmd: list[str], cwd: Path) -> str:
    res = subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return res.stdout.strip()


def setup_temp_git_repo() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="proto_sbx_test_")).resolve()
    _git(["init"], temp_dir)
    _git(["config", "user.name", "TestUser"], temp_dir)
    _git(["config", "user.email", "test@example.com"], temp_dir)
    _git(["config", "commit.gpgsign", "false"], temp_dir)

    # Initial commit
    readme = temp_dir / "README.md"
    readme.write_text("# Test Repo\nInitial content\n", encoding="utf-8")
    _git(["add", "README.md"], temp_dir)
    _git(["commit", "-m", "Initial commit"], temp_dir)
    return temp_dir


def test_sandbox_lifecycle():
    repo = setup_temp_git_repo()
    print(f"Created temp git repo at {repo}")

    try:
        assert is_git_repo(repo), "Should be recognized as a git repo"
        assert get_repo_root(repo) == repo, "Repo root should match"

        # 1. Create sandbox
        sbx = create_sandbox(repo, sandbox_id="sbx_test_001")
        assert sbx.sandbox_dir.exists(), "Sandbox directory must exist"
        assert sbx.status == SandboxStatus.ACTIVE
        print("[PASS] 1. create_sandbox successfully initialized worktree")

        # 2. Modify files inside sandbox
        sbx_readme = sbx.sandbox_dir / "README.md"
        sbx_readme.write_text("# Test Repo\nModified in sandbox!\n", encoding="utf-8")

        new_file = sbx.sandbox_dir / "new_feature.py"
        new_file.write_text("def hello(): return 'world'\n", encoding="utf-8")

        # 3. Assert host repo is 100% UNTOUCHED
        host_readme = repo / "README.md"
        assert host_readme.read_text(encoding="utf-8") == "# Test Repo\nInitial content\n", (
            "Host repo README must NOT be modified!"
        )
        assert not (repo / "new_feature.py").exists(), (
            "Host repo must not have new_feature.py!"
        )
        print("[PASS] 2. Host repository remained 100% untouched while sandbox was modified")

        # 4. Check diff
        diff = get_sandbox_diff(sbx)
        assert "Modified in sandbox!" in diff
        print("[PASS] 3. get_sandbox_diff accurately reflected sandbox changes")

        # 5. Handback workspace (auto-commit dirty changes)
        handback = handback_workspace(sbx)
        assert handback.committed is True, "Dirty changes should be auto-committed"
        assert "new_feature.py" in handback.modified_files
        assert "README.md" in handback.modified_files
        assert handback.commit_hash is not None
        print(f"[PASS] 4. handback_workspace secured changes (commit {handback.commit_hash[:7]})")

        # 6. Apply sandbox changes back to host repo
        apply_res = apply_sandbox(sbx, repo)
        assert apply_res.success is True, f"Apply should succeed: {apply_res.message}"
        assert (repo / "new_feature.py").exists(), "new_feature.py should now exist in host repo"
        assert host_readme.read_text(encoding="utf-8") == "# Test Repo\nModified in sandbox!\n", (
            "Host repo README should now reflect the merged changes"
        )
        print("[PASS] 5. apply_sandbox merged changes cleanly into host repo")

        # 7. Cleanup sandbox
        cleanup_sandbox(sbx, delete_branch=True)
        assert not sbx.sandbox_dir.exists(), "Sandbox directory should be removed"
        print("[PASS] 6. cleanup_sandbox cleanly removed the worktree and branch")

    finally:
        shutil.rmtree(repo, ignore_errors=True)


async def test_sandbox_context_async():
    repo = setup_temp_git_repo()
    print(f"\nTesting SandboxContext async context manager on {repo}...")
    try:
        async with SandboxContext(repo, sandbox_id="sbx_ctx_test", auto_cleanup=True, delete_branch=True) as sbx:
            assert sbx.sandbox_dir.exists()
            (sbx.sandbox_dir / "async_file.txt").write_text("created in context", encoding="utf-8")
            sbx_path = sbx.sandbox_dir

        # After exiting context, worktree should be cleaned up
        assert not sbx_path.exists(), "Worktree should be cleaned up on context exit"
        print("[PASS] 7. SandboxContext async lifecycle works as expected")
    finally:
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    test_sandbox_lifecycle()
    asyncio.run(test_sandbox_context_async())
    print("\nALL SANDBOX TESTS PASSED!")
