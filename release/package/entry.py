"""Build the independent protected Prometheus application distribution."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {'prometheus': 'quay.io/prometheus/prometheus:v3.16.0',
           'oauth2Proxy': 'quay.io/oauth2-proxy/oauth2-proxy:v7.15.6'}


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def chart(out, version, records):
    tree = out / 'chart-src'
    shutil.copytree(ROOT / 'deploy/chart', tree)
    text = (tree / 'Chart.yaml').read_text()
    text = re.sub(r'^version: .*$', 'version: ' + version, text, flags=re.M)
    (tree / 'Chart.yaml').write_text(text)
    import yaml
    values = yaml.safe_load((tree / 'values.yaml').read_text())
    values['images'] = {r['role']: r['source'].rsplit(':', 1)[0] + '@' + r['digest'] for r in records}
    (tree / 'values.yaml').write_text(yaml.safe_dump(values))
    run('helm', 'lint', str(tree), '-f', str(ROOT / 'deploy/site.example.yaml'))
    run('helm', 'template', 'prometheus', str(tree), '-f', str(ROOT / 'deploy/site.example.yaml'))
    out.joinpath('charts').mkdir()
    run('helm', 'package', str(tree), '-d', str(out / 'charts'))
    shutil.rmtree(tree)
    return out / 'charts' / ('prometheus-protected-' + version + '.tgz')


class HashWriter:
    def __init__(self, file):
        self.file, self.hash = file, hashlib.sha256()
    def write(self, data):
        self.hash.update(data)
        return self.file.write(data)
    def flush(self):
        self.file.flush()


def verify_image(path, record):
    if sha(path) != record['sha256'] or path.stat().st_size != record['bytes']:
        raise ValueError('image archive mismatch')
    with tarfile.open(path) as t:
        index = json.load(t.extractfile('index.json'))
        desc = index['manifests'][0]
        if desc['digest'] != record['digest']:
            raise ValueError('OCI manifest identity mismatch')
        manifest = json.load(t.extractfile('blobs/sha256/' + desc['digest'].split(':')[1]))
        for blob in [desc, manifest['config'], *manifest['layers']]:
            data = t.extractfile('blobs/sha256/' + blob['digest'].split(':')[1]).read()
            if len(data) != blob['size'] or hashlib.sha256(data).hexdigest() != blob['digest'].split(':')[1]:
                raise ValueError('OCI blob mismatch')
        cfg = json.load(t.extractfile('blobs/sha256/' + manifest['config']['digest'].split(':')[1]))
        if cfg['os'] != 'linux' or cfg['architecture'] != 'amd64':
            raise ValueError('wrong image platform')


def main():
    target = sys.argv[1]
    version = os.environ.get('VERSION') or (ROOT / 'release/package/VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be a stable SemVer')
    if (os.environ.get('ARCH') or 'amd64') != 'amd64':
        raise ValueError('only amd64 supported')
    output = Path(os.environ.get('OUTPUT') or ROOT / 'dist' / version).absolute()
    materials = Path(os.environ.get('MATERIALS') or ROOT / 'dist/materials').absolute()
    if target == 'help':
        print('make images | check | chart | package | verify | deployment-test\n'
              'VERSION=0.1.0 ARCH=amd64 OUTPUT=... MATERIALS=...\n'
              'verify: PACKAGE=... ARCHIVE_SHA256=<trusted hash>\n'
              'make test/build/lint retain upstream meanings; deployment-test checks this adapter.')
        return
    if target == 'package-slim':
        raise ValueError('initial release has no baseline; slim unsupported')
    if target == 'images':
        materials.mkdir(parents=True, exist_ok=False)
        records = []
        for role, source in SOURCES.items():
            dest = materials / (role + '-amd64.oci.tar')
            subprocess.run(['skopeo', 'copy', '--override-os', 'linux', '--override-arch', 'amd64',
                            'docker://' + source, 'oci-archive:' + str(dest)], check=True)
            with tarfile.open(dest) as t:
                digest = json.load(t.extractfile('index.json'))['manifests'][0]['digest']
            row = dict(role=role, source=source, repository=source.rsplit(':', 1)[0],
                       tag=source.rsplit(':', 1)[1], digest=digest, file=dest.name,
                       sha256=sha(dest), bytes=dest.stat().st_size)
            verify_image(dest, row)
            records.append(row)
        dump(materials / 'images.lock.json', records)
        return
    if target == 'verify':
        sys.path.insert(0, str(ROOT / 'release/package'))
        import prepare_install
        archive = Path(os.environ['PACKAGE'])
        with tempfile.TemporaryDirectory(dir=ROOT / 'dist') as tmp:
            tree = Path(tmp) / 'restored'
            prepare_install.extract(archive, os.environ['ARCHIVE_SHA256'], tree)
            index = json.loads((tree / 'vasi-package.json').read_text())
            for item in index['items'] + index['files']:
                path = tree / item['file']
                if sha(path) != item['sha256'] or path.stat().st_size != item['size']:
                    raise ValueError('package item mismatch: ' + item['file'])
            for row in json.loads((tree / 'images.lock.json').read_text()):
                verify_image(tree / 'images' / row['file'], row)
        print('archive, declared files and OCI graph verified')
        return
    if target == 'deployment-test':
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'release/package'), '-p', 'test_*.py'], check=True)
        return
    records = json.loads((materials / 'images.lock.json').read_text())
    if {r['role'] for r in records} != set(SOURCES) or len(records) != 2:
        raise ValueError('exactly two image roles required')
    for r in records:
        if r['source'] != SOURCES[r['role']] or r['file'] != r['role'] + '-amd64.oci.tar':
            raise ValueError('unrecognized material')
        verify_image(materials / r['file'], r)
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 8 * 1024**3:
        raise ValueError('retain 8 GiB free before packaging')
    output.mkdir(parents=True, exist_ok=False)
    pkg = chart(output, version, records)
    if target in ('check', 'chart'):
        print(pkg)
        return
    if target != 'package':
        raise ValueError('unknown target')
    stage = output / 'stage'
    stage.mkdir()
    shutil.move(str(output / 'charts'), stage / 'charts')
    (stage / 'images').mkdir()
    for r in records:
        shutil.copy2(materials / r['file'], stage / 'images' / r['file'])
    shutil.copy2(materials / 'images.lock.json', stage)
    shutil.copy2(ROOT / 'release/package/prepare_install.py', stage)
    shutil.copy2(ROOT / 'deploy/site.example.yaml', stage)
    shutil.copy2(ROOT / 'release/package/README.md', stage / 'README.md')
    shutil.copy2(ROOT / 'LICENSE', stage)
    shutil.copy2(ROOT / 'NOTICE', stage)
    dump(stage / 'source-provenance.json', {
        'repository': 'https://github.com/TheApeironLab/prometheus',
        'commit': run('git', '-C', str(ROOT), 'rev-parse', 'HEAD').strip(),
        'workingTreeDirty': bool(run('git', '-C', str(ROOT), 'status', '--porcelain').strip()),
        'adapterSha256': {str(p.relative_to(ROOT)): sha(p) for directory in ['release/package', 'deploy/chart']
                           for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in str(p)},
        'images': records, 'installation': 'not-run', 'channel': (os.environ.get('CHANNEL') or 'development')})
    chartpath = stage / 'charts' / pkg.name
    items = [dict(format='chart', file='charts/' + pkg.name, name='prometheus-protected',
                  version=version, valuesYaml='', size=chartpath.stat().st_size, sha256=sha(chartpath))]
    items += [dict(format='image', file='images/' + r['file'], repository=r['repository'],
                   tag=r['tag'], digest=r['digest'], size=r['bytes'], sha256=r['sha256']) for r in records]
    paths = {x['file'] for x in items}
    files = [dict(file=str(p.relative_to(stage)), size=p.stat().st_size, sha256=sha(p))
             for p in stage.rglob('*') if p.is_file() and str(p.relative_to(stage)) not in paths]
    dump(stage / 'vasi-package.json', dict(schema='vasi.package.v1', application='prometheus',
                                         version=version, items=items, files=files))
    (stage / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + str(p.relative_to(stage)) + '\n'
                                               for p in sorted(stage.rglob('*')) if p.is_file()))
    archive = output / ('prometheus-' + version + '-amd64-full.tar.gz')
    with archive.open('xb') as f:
        writer = HashWriter(f)
        with tarfile.open(fileobj=writer, mode='w|gz') as tar:
            for p in sorted(stage.rglob('*')):
                if p.is_file():
                    tar.add(p, arcname=str(p.relative_to(stage)), recursive=False)
        writer.flush()
        digest = writer.hash.hexdigest()
    (output / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n')
    print(archive)


if __name__ == '__main__':
    main()
