"""score.py must not write its JSON record onto the SQLite file.

sqlite opens drift.db on the first free fd. In a hook that is fd 3, and a
parent can inherit fd 3 already pointed at that file. The old fd-3 side
channel opened that descriptor "w" and overwrote the database header.
"""

import os
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_SCORE = os.path.join(_ROOT, "scripts", "score.py")
_PROFILE = os.path.join(_ROOT, "profiles", "caveman.json")


def _score(db: str, text: str) -> None:
    subprocess.check_call(
        [
            sys.executable,
            _SCORE,
            "--text",
            text,
            "--db",
            db,
            "--session",
            "fd3",
            "--profile-json",
            _PROFILE,
        ],
        stdout=subprocess.DEVNULL,
    )


def test_inherited_fd3_does_not_clobber_the_database(tmp_path):
    db = str(tmp_path / "drift.db")
    _score(db, "Short reply.")
    assert open(db, "rb").read(15) == b"SQLite format 3"

    wrapper = r"""
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
os.dup2(fd, 3)
if fd != 3:
    os.close(fd)
os.execv(sys.executable, [
    sys.executable, sys.argv[2],
    "--text", "Another short reply.",
    "--db", sys.argv[1],
    "--session", "fd3",
    "--profile-json", sys.argv[3],
])
"""
    subprocess.check_call(
        [sys.executable, "-c", wrapper, db, _SCORE, _PROFILE],
        stdout=subprocess.DEVNULL,
    )
    head = open(db, "rb").read(15)
    assert head == b"SQLite format 3", head
    con = sqlite3.connect(db)
    n = con.execute("select count(*) from scores").fetchone()[0]
    con.close()
    assert n >= 1
