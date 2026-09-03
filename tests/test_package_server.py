"""A package that boots on the VPS, and never carries what must not ship.

The set of files is easy to under- or over-include by hand: forget blaze_tdf
and the server won't import; sweep too wide and a club save leaves the machine.
These pin both ends."""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import package_server as P  # noqa: E402


def test_every_listed_file_exists_in_the_repo() -> None:
    for relative in P.members():
        assert (P.REPO / relative).exists(), f"listed but missing: {relative}"


def test_the_server_runtime_modules_are_all_present() -> None:
    # The three the running server imports. If the server grows a fourth, it
    # has to be added here or the package fails to boot -- which is the whole
    # point of pinning it.
    listed = set(P.members())
    for module in (
        "server/fifa14_blaze_server.py",
        "server/fut_inventory.py",
        "tools/blaze_tdf.py",
    ):
        assert module in listed
    # And the four data files it reads.
    for data in (
        "server/fifa14_cards.json",
        "server/fifa14_consumables.json",
        "server/fifa14_totw.json",
        "server/icebreakerpacklist.json",
    ):
        assert data in listed


def test_the_package_carries_no_game_files_or_club_data(tmp_path) -> None:
    # The line NOTICE.md draws: no EA bytes, and no player's saved club, ever
    # leave this machine in the package.
    output = tmp_path / "pkg.tgz"
    P.build(output)
    with tarfile.open(output) as tar:
        names = tar.getnames()
    forbidden = ("club-save", "/clubs/", "sessions.json", "default.xex",
                 ".big", ".stfs", "cardsdll", "/work/")
    for name in names:
        low = name.lower()
        assert not any(bad in low for bad in forbidden), f"must not ship: {name}"
    # runtime ships empty, with just the keep-file.
    runtime = [n for n in names if "/runtime/" in n]
    assert runtime == [f"{P.TOP}/runtime/.keep"]


def test_the_package_boots_and_serves(tmp_path) -> None:
    # The real proof: unpack, run with the interpreter running these tests
    # (no venv, no pip), and get a season list back. Skips cleanly if the
    # interpreter is older than the server needs.
    import json
    import subprocess
    import time
    import urllib.request

    if sys.version_info < (3, 10):
        import pytest
        pytest.skip("server needs Python 3.10+")

    output = tmp_path / "pkg.tgz"
    P.build(output)
    root = tmp_path / "unpacked"
    root.mkdir()
    with tarfile.open(output) as tar:
        tar.extractall(root)
    pkg = root / P.TOP
    (pkg / "fifa14revival.ini").write_text(
        "[server]\nhost = 127.0.0.1\ncore_port = 15041\nidentity_port = 15080\n"
    )

    proc = subprocess.Popen(
        ["sh", "deploy/run.sh"],
        cwd=pkg,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHON": sys.executable},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        body = None
        for _ in range(40):
            time.sleep(0.25)
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:15080/ut/game/fifa14/season/list", timeout=1
                ) as response:
                    body = json.loads(response.read())
                    break
            except Exception:
                continue
        assert body is not None, "server never answered"
        assert len(body["seasons"]) == 10
        assert (pkg / "runtime" / "clubs").is_dir()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
