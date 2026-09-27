"""Prompt A12 (§75-§78): launch.sh CWD-independence and static
rychlik.desktop validation. No GUI event loop assertions here -- these
only prove the launcher can be invoked from an unrelated directory
without import/path errors, and that the desktop entry still points at
the real, executable launcher."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_SH = PROJECT_ROOT / "launch.sh"
DESKTOP_ENTRY = PROJECT_ROOT / "rychlik.desktop"


def test_launch_sh_runs_from_unrelated_cwd(tmp_path):
    """§75: `cd /tmp && launch.sh` must still locate/import the project
    correctly. main.py blocks in the Qt event loop, so this only proves a
    clean startup with no import/path error within a bounded window --
    it does not wait for (or require) a manual window close."""
    proc = subprocess.Popen(
        [str(LAUNCH_SH)],
        cwd=str(tmp_path),  # a directory unrelated to the repository
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.wait(timeout=3)
        # If it exited already, it must not have been due to an error.
        assert proc.returncode == 0, proc.stderr.read()
    except subprocess.TimeoutExpired:
        # Still running the Qt event loop -- exactly the expected clean
        # startup for a GUI app with no window-close driver in this test.
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_launch_sh_exists_and_is_executable():
    assert LAUNCH_SH.is_file()
    import os

    assert os.access(LAUNCH_SH, os.X_OK)


def test_desktop_entry_points_at_launch_sh():
    content = DESKTOP_ENTRY.read_text()
    lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in content.splitlines() if "=" in line}
    assert lines.get("Exec") == str(LAUNCH_SH)
    assert Path(lines["Exec"]).is_file()
    import os

    assert os.access(lines["Exec"], os.X_OK)


def test_desktop_entry_has_required_fields():
    content = DESKTOP_ENTRY.read_text()
    for required in ("Type=Application", "Name=", "Exec="):
        assert required in content
