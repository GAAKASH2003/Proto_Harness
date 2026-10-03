"""Test suite verifying interactive REPL sandbox integration."""

from pathlib import Path
import tempfile
import subprocess
import shutil

from proto_harness.tui.app import SlashCompleter
from proto_harness.sandbox import (
    create_sandbox,
    handback_workspace,
    apply_sandbox,
    cleanup_sandbox,
    get_sandbox_diff,
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
    temp_dir = Path(tempfile.mkdtemp(prefix="proto_repl_sbx_")).resolve()
    _git(["init"], temp_dir)
    _git(["config", "user.name", "TestUser"], temp_dir)
    _git(["config", "user.email", "test@example.com"], temp_dir)
    _git(["config", "commit.gpgsign", "false"], temp_dir)

    readme = temp_dir / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    _git(["add", "README.md"], temp_dir)
    _git(["commit", "-m", "Initial commit"], temp_dir)
    return temp_dir


def test_slash_completer_has_sandbox():
    print("Testing SlashCompleter autocompletion for /sandbox...")
    cwd = Path.cwd()
    completer = SlashCompleter(get_cwd=lambda: cwd)
    assert "/sandbox" in completer._base_commands
    print("  [SUCCESS] /sandbox is registered in SlashCompleter base commands.")


def test_repl_sandbox_lifecycle():
    print("Testing REPL sandbox lifecycle operations (diff, apply, discard)...")
    repo = setup_temp_git_repo()

    try:
        # Simulate launching REPL in sandbox mode
        sbx = create_sandbox(repo, sandbox_id="sbx_repl_test")
        assert sbx.sandbox_dir.exists()

        # Simulate user writing code in REPL
        repl_file = sbx.sandbox_dir / "repl_code.py"
        repl_file.write_text("print('interactive sandbox')\n", encoding="utf-8")

        # Test /sandbox status / handback
        hb = handback_workspace(sbx)
        assert hb.committed is True
        assert "repl_code.py" in hb.modified_files

        # Test /sandbox diff
        diff = get_sandbox_diff(sbx)
        assert "interactive sandbox" in diff

        # Test /sandbox apply
        apply_res = apply_sandbox(sbx)
        assert apply_res.success is True
        assert (repo / "repl_code.py").exists()

        # Cleanup
        cleanup_sandbox(sbx, delete_branch=True)
        assert not sbx.sandbox_dir.exists()
        print("  [SUCCESS] REPL sandbox lifecycle (status, diff, apply, cleanup) passed.")

    finally:
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    test_slash_completer_has_sandbox()
    test_repl_sandbox_lifecycle()
    print("\nALL REPL SANDBOX TESTS PASSED!")
