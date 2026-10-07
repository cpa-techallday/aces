"""Output writing that doesn't follow planted links or silently overwrite (audit M1, C4).

Every tool that writes into a run or evidence folder goes through :class:`OutDir`:

* the folder is opened once. On POSIX the open directory handle is kept and every file is created relative to
  it, so swapping the folder for a symlink mid-run changes nothing;
* the final path component is never followed: a symlink (or Windows junction) where a file is to be written is
  refused, not written through;
* new files are created exclusively. Overwriting needs ``overwrite=True`` and then goes through a temporary
  file in the same folder and an atomic rename, which replaces the name itself and never writes through it.

Windows has no directory handles in ``os.open``. There the folder's resolved path is fixed at open and checked
again, with the link test, before every write.
"""
from __future__ import annotations

import io
import os
import secrets
import stat
from pathlib import Path

_POSIX_FD = os.name == "posix" and os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_BINARY = getattr(os, "O_BINARY", 0)


class UnsafePathError(OSError):
    """A write would follow a link, leave its folder or overwrite without permission."""


def is_link(p: str | os.PathLike) -> bool:
    """True for a symlink or a Windows junction."""
    p = Path(p)
    if p.is_symlink():
        return True
    isj = getattr(os.path, "isjunction", None)  # Python 3.12+
    return bool(isj and isj(p))


def _check_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise UnsafePathError(f"not a plain file name: {name!r}")
    return name


class OutDir:
    """A folder that files are written into safely.

    ``new=True`` requires that the folder does not exist yet (a fresh run folder).
    """

    def __init__(self, path: str | os.PathLike, *, new: bool = False, mkdir: bool = True):
        p = Path(path)
        if is_link(p):
            raise UnsafePathError(f"{p} is a link; refusing to write through it")
        if p.exists():
            if new:
                raise FileExistsError(f"{p} already exists; choose a new output folder")
            if not p.is_dir():
                raise NotADirectoryError(str(p))
        elif mkdir:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.mkdir(exist_ok=False)
        else:
            raise FileNotFoundError(str(p))
        self.path = p.resolve()
        self._fd: int | None = None
        if _POSIX_FD:
            self._fd = os.open(p, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)

    # ----------------------------------------------------------------- lifecycle
    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> OutDir:  # noqa: PYI034
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:  # best effort; callers should close
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110 - interpreter shutdown
            pass

    # ----------------------------------------------------------------- helpers
    def _revalidate(self) -> None:
        # Windows / no dir-fd support: re-check the folder before each write
        if self._fd is None and (is_link(self.path) or Path(self.path).resolve() != self.path
                                 or not self.path.is_dir()):
            raise UnsafePathError(f"{self.path} changed or became a link during the run")

    def _open_fd(self, name: str, flags: int, mode: int = 0o644) -> int:
        name = _check_name(name)
        self._revalidate()
        flags |= _NOFOLLOW | _BINARY
        if self._fd is not None:
            return os.open(name, flags, mode, dir_fd=self._fd)
        target = self.path / name
        if is_link(target):
            raise UnsafePathError(f"{target} is a link; refusing to write through it")
        return os.open(target, flags, mode)

    def _is_link_entry(self, name: str) -> bool:
        if self._fd is not None:
            try:
                return stat.S_ISLNK(os.stat(name, dir_fd=self._fd, follow_symlinks=False).st_mode)
            except FileNotFoundError:
                return False
        return is_link(self.path / name)

    def sha256(self, name: str) -> str:
        """sha256 of a file in this folder, read through the folder's handle (never through a link)."""
        import hashlib

        fd = self._open_fd(name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        h = hashlib.sha256()
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise UnsafePathError(f"{self.path / name} is not a regular file")
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def exists(self, name: str) -> bool:
        name = _check_name(name)
        if self._fd is not None:
            try:
                os.stat(name, dir_fd=self._fd, follow_symlinks=False)
                return True
            except FileNotFoundError:
                return False
        return os.path.lexists(self.path / name)

    # ----------------------------------------------------------------- writing
    def write_bytes(self, name: str, data: bytes, *, overwrite: bool = False) -> Path:
        if not overwrite:
            try:
                fd = self._open_fd(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                if self._is_link_entry(name):
                    raise UnsafePathError(f"{self.path / name} is a link; refusing to write through it") from None
                raise FileExistsError(f"{self.path / name} exists; refusing to overwrite") from None
            except OSError as e:  # ELOOP from O_NOFOLLOW on a planted link
                raise UnsafePathError(f"cannot create {self.path / name}: {e}") from e
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            return self.path / name
        tmp = f".{_check_name(name)}.{secrets.token_hex(6)}.tmp"
        fd = self._open_fd(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self._revalidate()
            if self._fd is not None:
                os.replace(tmp, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            else:
                os.replace(self.path / tmp, self.path / name)
        except BaseException:
            try:
                if self._fd is not None:
                    os.unlink(tmp, dir_fd=self._fd)
                else:
                    os.unlink(self.path / tmp)
            except OSError:
                pass
            raise
        return self.path / name

    def write_text(self, name: str, text: str, *, overwrite: bool = False, encoding: str = "utf-8") -> Path:
        return self.write_bytes(name, text.encode(encoding), overwrite=overwrite)

    def open_append(self, name: str, *, encoding: str = "utf-8") -> io.TextIOWrapper:
        """A text file opened for appending (logs). A link at ``name`` is refused."""
        try:
            fd = self._open_fd(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        except OSError as e:
            if isinstance(e, UnsafePathError):
                raise
            raise UnsafePathError(f"cannot open {self.path / name} for append: {e}") from e
        return os.fdopen(fd, "a", encoding=encoding)

    def subdir(self, name: str, *, new: bool = False) -> OutDir:
        """A child folder, created under this folder's handle (never through a link)."""
        name = _check_name(name)
        self._revalidate()
        if self._fd is not None:
            try:
                os.mkdir(name, 0o755, dir_fd=self._fd)
            except FileExistsError:
                if new:
                    raise FileExistsError(f"{self.path / name} already exists") from None
            child = OutDir.__new__(OutDir)
            child._fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=self._fd)
            child.path = self.path / name
            return child
        return OutDir(self.path / name, new=new)


def write_text(path: str | os.PathLike, text: str, *, overwrite: bool = False) -> Path:
    """Write one file safely: its folder must exist, and neither may be a link."""
    p = Path(path)
    with OutDir(p.parent, mkdir=False) as d:
        return d.write_text(p.name, text, overwrite=overwrite)


def write_bytes(path: str | os.PathLike, data: bytes, *, overwrite: bool = False) -> Path:
    p = Path(path)
    with OutDir(p.parent, mkdir=False) as d:
        return d.write_bytes(p.name, data, overwrite=overwrite)


def read_text_limited(path: str | os.PathLike, max_bytes: int, *, encoding: str = "utf-8") -> str:
    """Read a text file, refusing links and anything larger than ``max_bytes`` (audit M5)."""
    p = Path(path)
    if is_link(p):
        raise UnsafePathError(f"{p} is a link")
    if not stat.S_ISREG(os.stat(p, follow_symlinks=False).st_mode):
        raise UnsafePathError(f"{p} is not a regular file")  # a FIFO or device would block or never end
    fd = os.open(p, os.O_RDONLY | _NOFOLLOW | _BINARY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):  # swapped between the check and the open
            raise UnsafePathError(f"{p} is not a regular file")
        if st.st_size > max_bytes:
            raise ValueError(f"{p} is {st.st_size:,} bytes; the limit is {max_bytes:,}")
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{p} is larger than the limit of {max_bytes:,} bytes")
    return data.decode(encoding)


def sha256_of(path: str | os.PathLike) -> str:
    """sha256 of a regular file, refusing links, FIFOs and devices."""
    import hashlib

    p = Path(path)
    if is_link(p):
        raise UnsafePathError(f"{p} is a link")
    fd = os.open(p, os.O_RDONLY | _NOFOLLOW | _BINARY | getattr(os, "O_NONBLOCK", 0))
    h = hashlib.sha256()
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise UnsafePathError(f"{p} is not a regular file")
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
