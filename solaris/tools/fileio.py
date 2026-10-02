# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Replace a file in one step, so no reader ever sees half of it (stdlib only).

A plain ``write_text`` truncates the file and then fills it, and anything that reads it in between - another
session, or Syncthing scanning it to send to other machines - gets a short or empty file. ``write_text_atomic``
writes a temporary file in the same folder and renames it over the target. Temporary names start with
``.solaris-tmp-``, which ``.stglobalignore`` keeps out of Syncthing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP_PREFIX = ".solaris-tmp-"


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` through a temp file and a rename, keeping the target's permission bits.

    A symlinked target is written through (the link stays a link); a new file gets the umask default mode.
    """
    path = Path(os.path.realpath(path))
    data = text.encode(encoding)
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
