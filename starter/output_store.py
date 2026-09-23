"""Publish an immutable output generation with one atomic namespace change."""

import ctypes
import errno
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def exchange_paths(left, right):
    """Atomically migrate a legacy directory to a version pointer, without a gap.

    macOS: renamex_np(RENAME_SWAP). Linux: renameat2(RENAME_EXCHANGE).
    Unsupported filesystems/platforms fail without touching the old generation.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        call = libc.renamex_np
        call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        status = call(os.fsencode(left), os.fsencode(right), 0x00000002)  # RENAME_SWAP
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        call = libc.renameat2
        call.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        call.restype = ctypes.c_int
        status = call(
            -100, os.fsencode(left), -100, os.fsencode(right), 2
        )  # AT_FDCWD, RENAME_EXCHANGE
    else:
        raise OSError(
            errno.ENOTSUP, "Атомарная замена старой папки не поддерживается; укажите новый --out"
        )
    if status != 0:
        code = ctypes.get_errno()
        raise OSError(code, "Не удалось атомарно переключить результат: " + os.strerror(code))


@contextmanager
def staged_output(out_dir, validate_existing=None):
    """Yield a private directory, publish only when its writer returns normally.

    Previous generations are retained. No destructive fallback is allowed if
    the filesystem cannot atomically exchange a legacy directory and a symlink.
    """
    # Resolve the parent only: resolving out_dir would follow the live pointer.
    requested = Path(out_dir)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    # Resolve the raw parent before collapsing "..": link/../out must follow
    # the filesystem meaning of link rather than its lexical parent.
    if requested.name in ("", ".."):
        requested = requested.resolve()
    parent = requested.parent.resolve()
    target = parent / requested.name
    if target.is_symlink() and not target.exists():
        raise OSError(errno.ENOENT, f"Ссылка результата не существует: {target}")
    if target.exists():
        if not target.is_dir():
            raise OSError(errno.ENOTDIR, f"Путь результата не является папкой: {target}")
        if validate_existing is None:
            raise ValueError("Existing output must be a verified result directory")
        validate_existing(target)
    parent.mkdir(parents=True, exist_ok=True)
    version = Path(tempfile.mkdtemp(prefix=f".{target.name}-version-", dir=parent))
    pointer = parent / f".{target.name}-previous-{uuid4().hex}"
    try:
        yield version
        # Writers have closed their handles; flush each file before publication.
        for path in version.iterdir():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        pointer.symlink_to(version.name, target_is_directory=True)
    except BaseException:
        pointer.unlink(missing_ok=True)
        shutil.rmtree(version)
        raise
    try:
        if target.is_dir() and not target.is_symlink():
            exchange_paths(pointer, target)
            # pointer now names the complete previous directory; retain it.
        else:
            os.replace(pointer, target)
    except OSError:
        # A failed atomic syscall leaves both paths unchanged.
        pointer.unlink(missing_ok=True)
        shutil.rmtree(version)
        raise
