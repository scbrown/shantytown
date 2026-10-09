"""Stop returns while a scoped archive worker is still active."""
import os
from pathlib import Path
import shutil
import subprocess
import time

HOOK = Path(__file__).resolve().parents[1] / 'scripts/st-history-stop-hook.sh'


def wait_for(path):
    deadline = time.monotonic() + 4
    while not path.exists():
        assert time.monotonic() < deadline, str(path)
        time.sleep(.02)


def test_detached_worker_does_not_hold_harness_pipes_and_is_single_flight(tmp_path):
    hook = tmp_path / HOOK.name
    shutil.copy2(HOOK, hook)
    capture = tmp_path / 'st-history-capture.sh'
    capture.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$ST_HISTORY_DIR/started"
while [ ! -f "$ST_HISTORY_DIR/release" ]; do sleep .02; done
echo diagnostic
''')
    capture.chmod(0o700)
    scrub = tmp_path / 'st-history-scrub.sh'
    scrub.write_text('''#!/bin/bash
printf '%s\\n' "$*" > "$ST_HISTORY_DIR/scrubbed"
exit 2
''')
    scrub.chmod(0o700)
    raw = tmp_path / 'raw'
    env = dict(os.environ, SHANTY_AGENT='tester', ST_HISTORY_DIR=str(raw))
    start = time.monotonic()
    try:
        result = subprocess.run([str(hook)], env=env, capture_output=True, timeout=1)
        assert time.monotonic() - start < 1
        assert result.returncode == 0 and result.stdout == b''
        wait_for(raw / 'started')
        assert not (raw / 'hook.log').exists()  # positive slow-worker control
        # Worker lock remains live after the parent exits; overlapping stops
        # don't enqueue another expensive rescan.
        subprocess.run([str(hook), '--worker'], env=env, capture_output=True,
                       check=True, timeout=1)
        assert (raw / 'started').read_text().splitlines() == ['--agent tester']
    finally:
        raw.mkdir(exist_ok=True)
        (raw / 'release').touch()
    wait_for(raw / 'hook.log')
    assert (raw / 'scrubbed').read_text().strip() == '--agent tester'
    assert 'rc=1' in (raw / 'hook.log').read_text()  # scrub failure remains visible
    assert 'diagnostic' in (raw / 'worker.log').read_text()
    assert (raw / 'worker.log').stat().st_mode & 0o777 == 0o600
