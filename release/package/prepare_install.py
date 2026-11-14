"""Authenticate the archive before safely extracting; no cluster credentials needed."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import tarfile


def extract(archive, expected, output):
    h = hashlib.sha256()
    with Path(archive).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
        if h.hexdigest() != expected:
            raise ValueError('trusted archive SHA256 mismatch')
        f.seek(0)
        with tarfile.open(fileobj=f, mode='r:gz') as t:
            names, total = set(), 0
            members = t.getmembers()
            for m in members:
                p = PurePosixPath(m.name)
                total += m.size
                if (not m.isfile() or p.is_absolute() or '..' in p.parts or '\\' in m.name
                        or str(p) != m.name or m.name in names or total > 4 * 1024**3
                        or any(ord(c) < 32 for c in m.name)):
                    raise ValueError('unsafe archive member')
                names.add(m.name)
            output = Path(output).absolute()
            if any(p.is_symlink() for p in [output, *output.parents]):
                raise ValueError('symlink extraction destination')
            output.mkdir(parents=True, exist_ok=False)
            t.extractall(output, members=members, filter='data')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('archive', type=Path)
    p.add_argument('--sha256', required=True, help='SHA256 from trusted distribution channel')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    extract(a.archive, a.sha256, a.output)
