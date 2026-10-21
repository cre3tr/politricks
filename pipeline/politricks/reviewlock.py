"""One writer at a time per bill.

Why this file exists: the `politricks review` walk holds the bill in memory and
saves the whole file on every accept. A walk left open in one terminal would
silently overwrite a `--from-sheet` import made in another (found 2026-10-15,
documented then, closed here 2026-10-19). `generate` has the same shape: it
loads the bill, waits minutes for a batch, then saves the whole file. So every
command that saves a bill takes this lock before it loads the bill and keeps it
until it has saved: the walk, the sheet import, generate, fetch, extract,
samples and verify.

The lock is an OS file lock, not just a file that exists. When the process
ends, however it ends (q, Ctrl-C, a closed window, a crash), the OS lets go of
it, so a stale lock can never block the next review. The `.lock` file itself
may stay behind; on its own it means nothing, and it's gitignored.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path


class BillLocked(Exception):
    """Another walk, sheet import or generate run has this bill open."""


def lock_path(bill_file: Path) -> Path:
    # Doesn't end in .json, so the site never builds a page from it.
    return bill_file.with_name(bill_file.name + ".lock")


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # one byte at offset 0, don't wait
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    # Windows says to unlock before closing (closing alone frees it "at some
    # point"). flock is freed by the close itself.
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def bill_lock(bill_file: Path) -> Iterator[None]:
    """Hold the bill's lock for the `with` block, or raise `BillLocked` at once."""
    fd = os.open(lock_path(bill_file), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            raise BillLocked(
                f"{bill_file.name} is open in another politricks command (a review walk, sheet import, "
                "generate run, or a fetch/extract/samples/verify). Let it finish, or quit it "
                "(q in a walk), then run this again."
            )
        try:
            yield
        finally:
            # A failed unlock must never replace the real result: closing the
            # file below frees the lock anyway.
            with contextlib.suppress(OSError):
                _unlock(fd)
    finally:
        os.close(fd)
