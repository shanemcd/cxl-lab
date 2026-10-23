"""Render lab-owned content and protect filtering/identity contracts offline.

Run with .venv/bin/python -m unittest discover -s tests -p 'test_perses*.py'.
No cluster access, credentials, or mutations are performed.
"""
import re
import unittest
from pathlib import Path

import jinja2
import yaml

TEMPLATES = Path(__file__).resolve().parents[1] / 'ansible_collections/shanemcd/cxl_lab/roles/observability/templates'


def render(name):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.StrictUndefined)
    return yaml.safe_load(env.get_template(name + '.yaml.j2').render(
        observability_namespace='content', observability_rule_name='memory-tiers',
        observability_perses_instance_label={'app.kubernetes.io/instance': 'perses'},
    ))


def queries(config):
    for key, panel in config['panels'].items():
        for query in panel['spec'].get('queries', []):
            yield key, query['spec']['plugin']['spec']['query']


class PersesDashboardTests(unittest.TestCase):
    def test_all_template_variables_are_declared(self):
        for name in ('infra-cxl-glance', 'cluster-overview', 'vm-explorer'):
            config = render('perses-dashboard-' + name)['spec']['config']
            declared = {v['spec']['name'] for v in config['variables']}
            for key, query in queries(config):
                self.assertLessEqual(set(re.findall(r'\$(\w+)', query)), declared, key)

    def test_cascading_scope_and_bounded_limit(self):
        config = render('perses-dashboard-infra-cxl-glance')['spec']['config']
        variables = {v['spec']['name']: v['spec'] for v in config['variables']}
        self.assertEqual(list(variables), ['node', 'namespace', 'vm', 'limit'])
        self.assertIn('$node', str(variables['namespace']['plugin']))
        self.assertIn('$namespace', str(variables['vm']['plugin']))
        self.assertEqual(variables['limit']['defaultValue'], '10')
        self.assertFalse(variables['limit']['allowAllValue'])
        self.assertEqual([v['value'] for v in variables['limit']['plugin']['spec']['values']], ['5', '10', '20', '50'])
        for key, query in queries(config):
            self.assertIn('node=~"$node"', query, key)
            if key in ('CxlVMPlacement', 'CxlVMCxlResidency'):
                self.assertIn('namespace=~"$namespace"', query)
                self.assertIn('vm=~"$vm"', query)
                self.assertIn('@ end()', query)
                self.assertIn('topk($limit', query)
                self.assertIn('and on (node, namespace, vm)', query)
        visual = config['panels']['CxlVMPlacement']['spec']['plugin']['spec']['visual']
        self.assertNotIn('stack', visual)
        self.assertEqual(visual['areaOpacity'], 0)

    def test_explorer_uses_one_fixed_vm_selection_across_charts(self):
        config = render('perses-dashboard-vm-explorer')['spec']['config']
        selections = set()
        for key, query in queries(config):
            self.assertIn('node=~"$node"', query, key)
            self.assertIn('namespace=~"$namespace"', query, key)
            self.assertIn('name=~"$vm"', query, key)
            self.assertIn('@ end()', query, key)
            selections.add(query.split('and on (node, namespace, name)')[1])
        self.assertEqual(len(selections), 1)
        self.assertEqual(len(config['panels']), 5)

    def test_recording_rules_preserve_vm_identity_and_placement(self):
        rules = render('cxl-memory-tiers')['spec']['groups'][0]['rules']
        vm_rules = [r for r in rules if r['record'] == 'openshift:vm:memory:tier:bytes']
        self.assertEqual(len(vm_rules), 2)
        for rule in vm_rules:
            self.assertIn('sum by (node, namespace, vm)', rule['expr'])
            self.assertIn('workload_kind="VirtualMachineInstance"', rule['expr'])

    def test_overview_labels_match_metric_families(self):
        config = render('perses-dashboard-cluster-overview')['spec']['config']
        q = dict(queries(config))
        self.assertIn('cluster-wide', config['panels']['api-up']['spec']['display']['name'])
        self.assertIn('instance=~"$node"', q['cluster-cpu'])
        self.assertIn('instance=~"$node"', q['node-mem'])
        self.assertIn('node=~"$node"', q['pod-mem'])
        self.assertIn('container!="POD"', q['pod-mem'])
        self.assertIn('@ end()', q['pod-mem'])

    def test_layout_references_resolve(self):
        for name in ('infra-cxl-glance', 'cluster-overview', 'vm-explorer'):
            config = render('perses-dashboard-' + name)['spec']['config']
            for layout in config['layouts']:
                for item in layout['spec']['items']:
                    self.assertIn(item['content']['$ref'].split('/')[-1], config['panels'])


if __name__ == '__main__':
    unittest.main()
