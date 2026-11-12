from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def render(*extra):
    return subprocess.run(['helm', 'template', 'test', str(ROOT / 'deploy/chart'),
        '-f', str(ROOT / 'deploy/site.example.yaml'),
        '--set', 'images.prometheus=quay.io/prometheus/prometheus@sha256:' + 'a'*64,
        '--set', 'images.oauth2Proxy=quay.io/oauth2-proxy/oauth2-proxy@sha256:' + 'b'*64,
        *extra], capture_output=True, text=True)


class ChartContract(unittest.TestCase):
    def test_access_and_data_boundary(self):
        r = render()
        self.assertEqual(r.returncode, 0, r.stderr)
        docs = list(yaml.safe_load_all(r.stdout))
        self.assertFalse(any(d['kind'] in ('ClusterRole', 'ClusterRoleBinding') for d in docs))
        deployment = next(d for d in docs if d['kind'] == 'Deployment')
        pod = deployment['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        prometheus, proxy = pod['containers']
        self.assertIn('--web.listen-address=127.0.0.1:9090', prometheus['args'])
        self.assertNotIn('--web.enable-admin-api', prometheus['args'])
        self.assertIn('--code-challenge-method=S256', proxy['args'])
        self.assertIn('--allowed-group=prometheus-readers', proxy['args'])
        service = next(d for d in docs if d['kind'] == 'Service')
        self.assertEqual([p['port'] for p in service['spec']['ports']], [4180])
        pvc = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
        self.assertEqual(pvc['metadata']['annotations']['helm.sh/resource-policy'], 'keep')

    def test_fail_closed_configuration(self):
        for flag in ['site.allowedGroups=[]', 'site.issuerUrl=http://insecure.invalid', 'site.oidcSecret=']:
            with self.subTest(flag=flag):
                self.assertNotEqual(render('--set', flag).returncode, 0)
