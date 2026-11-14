from pathlib import Path
import subprocess
import unittest
import json
import yaml
import jsonschema

ROOT = Path(__file__).resolve().parents[2]


def render(*extra):
    return subprocess.run(['helm', 'template', 'prometheus', str(ROOT / 'deploy/chart'),
        '--namespace', 'monitoring', '-f', str(ROOT / 'deploy/site.example.yaml'),
        '--set', 'global.imageRegistry=registry.example.internal', *extra], capture_output=True, text=True)


class ChartContract(unittest.TestCase):
    def documents(self, *args):
        r = render(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [d for d in yaml.safe_load_all(r.stdout) if d]

    def test_operator_security_and_storage(self):
        docs = self.documents()
        prom = next(d for d in docs if d['kind'] == 'Prometheus')['spec']
        self.assertTrue(prom['listenLocal'])
        self.assertFalse(prom['enableAdminAPI'])
        self.assertFalse(prom['enableRemoteWriteReceiver'])
        self.assertTrue(prom['arbitraryFSAccessThroughSMs']['deny'])
        self.assertTrue(prom['ignoreNamespaceSelectors'])
        self.assertEqual(prom['persistentVolumeClaimRetentionPolicy'], {'whenDeleted':'Retain','whenScaled':'Retain'})
        self.assertEqual(prom['serviceMonitorNamespaceSelector']['matchLabels'], {'kubernetes.io/metadata.name':'monitoring'})
        proxy = next(c for c in prom['containers'] if c['name']=='oauth2-proxy')
        self.assertIn('--code-challenge-method=S256', proxy['args'])
        self.assertIn('--allowed-group=prometheus-readers', proxy['args'])
        for doc in docs:
            if doc['kind']=='ClusterRole':
                for rule in doc['rules']:
                    self.assertFalse(set(rule['verbs'])-{'get','list','watch'})
                    self.assertFalse(set(rule['resources'])-{'namespaces','storageclasses'})
        reader = next(d for d in docs if d['kind']=='Role' and d['metadata']['name']=='prometheus-discovery')
        self.assertFalse(any('secrets' in x['resources'] for x in reader['rules']))
        op = next(d for d in docs if d['kind']=='Deployment')['spec']['template']['spec']['containers'][0]
        self.assertIn('--namespaces=monitoring', op['args'])

    def test_fail_closed_configuration(self):
        for flag in ['site.allowedGroups=[]','site.issuerUrl=http://invalid','site.oidcSecret=',
                     'query.enabled=true','scrapeConfig=legacy','scrapeSecrets[0]=legacy','storage.retain=false']:
            with self.subTest(flag=flag):self.assertNotEqual(render('--set', flag).returncode,0)

    def test_query_endpoint_separate(self):
        docs=self.documents('--set','query.enabled=true','--set','query.authSecret=reader','--set','query.tlsSecret=tls')
        spec=next(d for d in docs if d['kind']=='Prometheus')['spec']
        query=next(c for c in spec['containers'] if c['name']=='query-proxy')
        self.assertTrue(query['securityContext']['readOnlyRootFilesystem'])
        self.assertEqual({v['name'] for v in query['volumeMounts']},{'query-auth','query-tls'})
        policy=next(d for d in docs if d['kind']=='NetworkPolicy' and d['metadata']['name']=='prometheus')
        ingress=next(r for r in policy['spec']['ingress'] if r['ports'][0]['port']==9443)
        self.assertEqual(ingress['from'][0]['namespaceSelector']['matchLabels']['kubernetes.io/metadata.name'],'vasi')
        self.assertTrue(ingress['from'][0]['podSelector']['matchLabels'])

    def test_crd_schema_and_image_coverage(self):
        docs=self.documents('--set','query.enabled=true','--set','query.authSecret=reader','--set','query.tlsSecret=tls')
        crds=list(yaml.safe_load_all((ROOT/'deploy/operator/crds.yaml').read_text()))
        crd=next(d for d in crds if d['metadata']['name']=='prometheuses.monitoring.coreos.com')
        schema=next(v['schema']['openAPIV3Schema'] for v in crd['spec']['versions'] if v['name']=='v1')
        prom=next(d for d in docs if d['kind']=='Prometheus')
        jsonschema.Draft4Validator(schema).validate(prom)
        self.assertFalse(set(prom['spec'])-set(schema['properties']['spec']['properties']))
        images=set()
        def walk(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k in ['containers','initContainers']:images.update(c['image'] for c in x)
                    walk(x)
            elif isinstance(v,list):
                for x in v:walk(x)
        walk(docs)
        self.assertEqual(len(images),5)
        self.assertTrue(all(x.startswith('registry.example.internal/') for x in images))
        op=next(d for d in docs if d['kind']=='Deployment')['spec']['template']['spec']['containers'][0]
        arg=next(x for x in op['args'] if x.startswith('--prometheus-config-reloader='))
        self.assertIn(arg.split('=',1)[1],images)
        lock=json.loads((ROOT/'deploy/operator/source.json').read_text())
        self.assertEqual(len(lock['crds']),len(crds))
