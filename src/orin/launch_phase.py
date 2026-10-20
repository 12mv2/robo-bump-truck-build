# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Child-authored proof that a managed live_trail launch exited before FC access.

Only ``exited_before_fc`` is terminal proof. ``validating`` is deliberately not:
an abrupt death or a failed boundary write must leave the launcher refusing.
The launcher must also verify identity, launch nonce and actual process exit.
Unmanaged imports and direct main() calls do not enable this instrumentation.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid


FILE_ENV = "ROBOTICS_LAUNCH_PHASE_FILE"
NONCE_ENV = "ROBOTICS_LAUNCH_NONCE"
_active = None


def current_identity(proc_root=Path("/proc")):
    """Read this process's Linux boot/PID/start tuple; unavailable means refuse."""
    pid = os.getpid()
    stat = (proc_root / str(pid) / "stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    ticks = fields[19]
    boot = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
    if not boot or not ticks.isdigit() or fields[0] in {"Z", "X"}:
        raise ValueError("cannot identify managed launch")
    return {"pid": pid, "boot_id": boot, "start_ticks": ticks}


def atomic_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _PhaseProof:
    def __init__(self, path, nonce):
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"managed launch receipt already exists: {path}")
        self.path = path
        self.identity = current_identity()
        self.nonce = nonce
        self.fc_access_possible = False
        self.write("validating")

    def write(self, phase):
        atomic_json(self.path, {"version": 1, "launch_nonce": self.nonce,
                               "identity": self.identity, "phase": phase})

    def before_fc_access(self):
        # Latch BEFORE I/O: if persistence fails and the caller unwinds, the old
        # validating file must never be replaced by a false terminal proof.
        self.fc_access_possible = True
        self.write("fc_access_possible")

    def finish(self):
        if not self.fc_access_possible:
            self.write("exited_before_fc")


def before_fc_access():
    """Call before importing/opening any FC connection; write failure must abort."""
    if _active is not None:
        _active.before_fc_access()


@contextmanager
def managed_main():
    """Enable proof only around the managed entry point, including argparse exit."""
    global _active
    configured = FILE_ENV in os.environ, NONCE_ENV in os.environ
    if not any(configured):
        yield
        return
    path_value = os.environ.get(FILE_ENV)
    nonce = os.environ.get(NONCE_ENV)
    if not all(configured) or not path_value or not nonce:
        raise RuntimeError("managed launch needs both phase file and nonce")
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeError("managed launch phase file must be absolute")
    if _active is not None:
        raise RuntimeError("managed launch proof is already active")
    proof = _PhaseProof(path, nonce)
    _active = proof
    try:
        yield
    finally:
        try:
            proof.finish()
        finally:
            _active = None
