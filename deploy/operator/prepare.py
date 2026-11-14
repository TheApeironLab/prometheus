#!/usr/bin/env python3
"""Explicit platform CRD preparation, separate from application installation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--apply', action='store_true', help='Apply after server dry-run; never force conflicts')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    lock = json.loads((root / 'source.json').read_text())
    manifest = root / 'crds.yaml'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != lock['crdsSha256']:
        raise SystemExit('CRD checksum mismatch')
    base = ['kubectl', '--kubeconfig', args.kubeconfig]
    command = base + ['apply', '--server-side', '--field-manager=prometheus-platform', '-f', str(manifest)]
    subprocess.run(command + ['--dry-run=server'], check=True)
    if args.apply:
        subprocess.run(command, check=True)
        subprocess.run(base + ['wait', '--for=condition=Established', '--timeout=120s',
                              *('crd/' + name for name in lock['crds'])], check=True)
    print(json.dumps({'operatorVersion': lock['version'], 'crds': len(lock['crds']),
                      'applied': args.apply, 'forceConflicts': False}))


if __name__ == '__main__':
    main()
