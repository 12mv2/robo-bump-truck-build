# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Small atomic recording checkpoints, called only by the disk-writer thread."""
import json
import os
from pathlib import Path


def sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_npz(path, **arrays):
    import numpy as np
    path = Path(path)
    temp = path.with_name(path.name + '.partial')
    with temp.open('wb') as f:
        np.savez_compressed(f, **arrays)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    sync_directory(path.parent)


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.partial')
    with temp.open('w') as f:
        json.dump(value, f)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    sync_directory(path.parent)
