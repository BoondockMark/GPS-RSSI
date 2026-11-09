"""Shared upload budgets and free-space checks, independent of request framing."""
import shutil

MIB = 1024 * 1024
GIB = 1024 * MIB
UPLOAD_LIMITS = dict(database=128*MIB, audio=128*MIB, map=GIB, terrain=GIB, pack=GIB)
RESERVE = 64 * MIB


def required_space(kind, length):
    # Allow staging/validation copies and SQLite replacement journals. Packs may
    # expand to 1 GiB regardless of their compressed upload size.
    return length + 3*GIB + RESERVE if kind == 'pack' else 4*length + RESERVE


def check_space(path, required):
    if shutil.disk_usage(path).free < required:
        raise ValueError(f'Not enough free space: need approximately {required/MIB:.0f} MiB including working space')
