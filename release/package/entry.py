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
           'oauth2-proxy': 'quay.io/oauth2-proxy/oauth2-proxy:v7.15.6',
           'operator': 'quay.io/prometheus-operator/prometheus-operator:v0.95.1',
           'config-reloader': 'quay.io/prometheus-operator/prometheus-config-reloader:v0.95.1'}
REPOSITORIES = {'prometheus': 'prometheus/prometheus',
                'oauth2-proxy': 'oauth2-proxy/oauth2-proxy', 'query-proxy': 'prometheus/query-proxy',
                'operator': 'prometheus-operator/prometheus-operator',
                'config-reloader': 'prometheus-operator/prometheus-config-reloader'}


def query_sources():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / 'deploy/query-proxy').iterdir())
            if p.name in {'Dockerfile', 'server.py'}}


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
    values['images'] = {r['role']: {k: r[k] for k in ('repository', 'tag', 'digest')} for r in records}
    (tree / 'values.yaml').write_text(yaml.safe_dump(values))
    for query_enabled in (False, True):
        flags = ['-f', str(ROOT / 'deploy/site.example.yaml'), '--set', 'global.imageRegistry=registry.example.invalid']
        if query_enabled:
            flags += ['--set', 'query.enabled=true', '--set', 'query.authSecret=reader', '--set', 'query.tlsSecret=query-tls']
        run('helm', 'lint', str(tree), *flags)
        run('helm', 'template', 'prometheus', str(tree), *flags)
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
        query_tag = 'chentu-experiment/prometheus-query:0.1.1'
        base = Path(os.environ['BASE_MATERIALS']) if os.environ.get('BASE_MATERIALS') else None
        previous = {r['role']: r for r in json.loads((base / 'images.lock.json').read_text())} if base else {}
        if not base and subprocess.run(['docker', 'image', 'inspect', query_tag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            raise ValueError('query image tag exists; use a new version, never overwrite')
        source_identity = query_sources()
        if not base:
            subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '-t', query_tag,
                        str(ROOT / 'deploy/query-proxy')], check=True)
        for role, source in {**SOURCES, 'query-proxy': query_tag}.items():
            dest = materials / (role + '-amd64.oci.tar')
            if role in previous:
                row = previous[role]
                if row['source'] != source or row['repository'] != REPOSITORIES[role] or (role == 'query-proxy' and row.get('buildSource') != source_identity):
                    raise ValueError('reused image source mismatch')
                verify_image(base / row['file'], row)
                shutil.copy2(base / row['file'], dest)
                records.append(row)
                continue
            subprocess.run(['skopeo', 'copy', '--override-os', 'linux', '--override-arch', 'amd64',
                            ('docker-daemon:' if role == 'query-proxy' else 'docker://') + source,
                            'oci-archive:' + str(dest)], check=True)
            with tarfile.open(dest) as t:
                digest = json.load(t.extractfile('index.json'))['manifests'][0]['digest']
            row = dict(role=role, source=source, repository=REPOSITORIES[role],
                       tag=source.rsplit(':', 1)[1], digest=digest, file=dest.name,
                       sha256=sha(dest), bytes=dest.stat().st_size)
            verify_image(dest, row)
            if role == 'query-proxy':
                if source_identity != query_sources():
                    raise ValueError('query source changed during build')
                row['buildSource'] = source_identity
            records.append(row)
        dump(materials / 'images.lock.json', records)
        return
    if target == 'verify':
        sys.path.insert(0, str(ROOT / 'release/package'))
        import prepare_install
        archive = Path(os.environ['PACKAGE'])
        with tempfile.TemporaryDirectory(dir=archive.resolve().parent) as tmp:
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
        subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(ROOT / 'release/package'), '-p', 'test_*.py'], check=True)
        return
    records = json.loads((materials / 'images.lock.json').read_text())
    if {r['role'] for r in records} != set(REPOSITORIES) or len(records) != 5:
        raise ValueError('exactly five image roles required')
    for r in records:
        source = SOURCES.get(r['role'], 'chentu-experiment/prometheus-query:0.1.1')
        if (r['source'] != source or r['file'] != r['role'] + '-amd64.oci.tar'
                or r['repository'] != REPOSITORIES[r['role']]
                or r['tag'] != source.rsplit(':', 1)[1]
                or (r['role'] == 'query-proxy' and r.get('buildSource') != query_sources())):
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
    shutil.copytree(ROOT / 'deploy/operator', stage / 'operator')
    shutil.copy2(ROOT / 'LICENSE', stage)
    shutil.copy2(ROOT / 'NOTICE', stage)
    dump(stage / 'source-provenance.json', {
        'repository': 'https://github.com/TheApeironLab/prometheus',
        'commit': run('git', '-C', str(ROOT), 'rev-parse', 'HEAD').strip(),
        'workingTreeDirty': bool(run('git', '-C', str(ROOT), 'status', '--porcelain').strip()),
        'adapterSha256': {str(p.relative_to(ROOT)): sha(p) for directory in ['release/package', 'deploy/chart', 'deploy/query-proxy', 'deploy/operator']
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
