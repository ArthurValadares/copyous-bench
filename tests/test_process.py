import os
import subprocess
import sys

import pytest

from copyous_bench.orchestration.process import (
    CommandError,
    SupervisedResult,
    kill_process_group,
    run_command,
    run_supervised,
)

PY = sys.executable


def _spawn(code, **kwargs):
    return subprocess.Popen([PY, "-c", code], start_new_session=True, **kwargs)


def test_run_command_merges_stderr_into_stdout():
    proc = run_command([PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert "out" in proc.stdout and "err" in proc.stdout


def test_run_command_logs_to_file_with_cwd_and_env(tmp_path):
    log = tmp_path / "logs" / "x.log"
    proc = run_command(
        [PY, "-c", "import os; print(os.environ['X'], os.getcwd())"], cwd=tmp_path, env={"X": "1"}, log_path=log
    )
    assert proc.stdout is None
    assert log.read_text().split() == ["1", str(tmp_path.resolve())]


def test_run_command_check():
    assert run_command([PY, "-c", "pass"], check=True).returncode == 0
    with pytest.raises(CommandError, match=r"Command failed \(3\)"):
        run_command([PY, "-c", "raise SystemExit(3)"], check=True)
    assert run_command([PY, "-c", "raise SystemExit(3)"]).returncode == 3


def test_kill_process_group_terminates():
    proc = _spawn("import time; time.sleep(60)")
    kill_process_group(proc)
    assert proc.returncode is not None


def test_kill_process_group_escalates_to_sigkill():
    proc = _spawn(
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); "
        "time.sleep(60)",
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.readline().strip() == b"ready"
    kill_process_group(proc, grace=0.3)
    assert proc.returncode == -9
    proc.stdout.close()


def test_kill_process_group_tolerates_a_finished_process():
    proc = _spawn("pass")
    proc.wait()
    kill_process_group(proc)


def _supervise(tmp_path, code, **kwargs):
    options = {
        "cwd": tmp_path,
        "env": os.environ,
        "timeout": 20,
        "log_path": tmp_path / "run.log",
        "poll_interval": 0.05,
    }
    return run_supervised([PY, "-c", code], **{**options, **kwargs})


def test_supervised_normal_exit(tmp_path):
    assert _supervise(tmp_path, "print('x')") == SupervisedResult(0)
    assert (tmp_path / "run.log").read_text() == "x\n"
    assert _supervise(tmp_path, "raise SystemExit(4)").returncode == 4


def test_supervised_timeout(tmp_path):
    result = _supervise(tmp_path, "import time; time.sleep(60)", timeout=0.2)
    assert result == SupervisedResult(None, timed_out=True)


def test_supervised_stall(tmp_path):
    result = _supervise(tmp_path, "import time; time.sleep(60)", progress=lambda: 0, stall_timeout=0.2)
    assert result == SupervisedResult(None, stalled=True)


def test_supervised_progress_keeps_the_process_alive(tmp_path):
    ticks = tmp_path / "ticks"
    ticks.mkdir()
    code = (
        f"import time, pathlib\nfor i in range(8):\n    pathlib.Path({str(ticks)!r}, str(i)).touch(); time.sleep(0.1)\n"
    )
    result = _supervise(tmp_path, code, progress=lambda: len(list(ticks.iterdir())), stall_timeout=0.45)
    assert result == SupervisedResult(0)


def test_supervised_without_stall_timeout_ignores_progress(tmp_path):
    result = _supervise(tmp_path, "import time; time.sleep(60)", timeout=0.2, progress=lambda: 0)
    assert result.timed_out
