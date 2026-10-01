"""Runs the end-to-end harness (interface + proxy manager + proxy subprocess + simulated BACnet device) in a subprocess.

Needs only the installed packages and free local UDP ports; no hardware. Skipped if bacpypes3 is unavailable.
"""
import os
import socket
import subprocess
import sys

from pathlib import Path

import pytest

pytest.importorskip('bacpypes3')

HARNESS = Path(__file__).with_name('e2e_harness.py')
EXPECTED_CHECKS = 14


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_end_to_end(tmp_path):
    log = tmp_path / 'e2e.log'
    # The proxy binds 47808 + offset; pick an offset unlikely to collide with a real BACnet stack on this host.
    proc = subprocess.run([sys.executable, str(HARNESS), str(_free_udp_port()), '13'], capture_output=True, text=True,
                          timeout=180, env={**os.environ, 'BACNET_E2E_LOG': str(log)})
    report = proc.stdout + proc.stderr
    if proc.returncode != 0 and log.exists():
        report += '\n--- log ---\n' + log.read_text()[-6000:]
    assert proc.returncode == 0, report
    assert f'{EXPECTED_CHECKS}/{EXPECTED_CHECKS} passed' in proc.stdout, report
