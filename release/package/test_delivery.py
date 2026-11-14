import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from prepare_install import extract


class SafeExtraction(unittest.TestCase):
    def attempt(self, name, kind=tarfile.REGTYPE, valid_hash=True):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'p.tgz'
            with tarfile.open(archive, 'w:gz') as t:
                m = tarfile.TarInfo(name)
                m.type = kind
                m.linkname = '/etc/passwd'
                m.size = 1 if kind == tarfile.REGTYPE else 0
                t.addfile(m, io.BytesIO(b'x') if m.size else None)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest() if valid_hash else '0' * 64
            extract(archive, digest, Path(tmp) / 'out')
            return True

    def test_regular(self):
        self.assertTrue(self.attempt('charts/a.tgz'))

    def test_unsafe(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                           ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.attempt(name, kind)

    def test_wrong_outer_hash(self):
        with self.assertRaises(ValueError):
            self.attempt('a', valid_hash=False)
