"""Guard dependency boundaries so utilities remain usable without the app."""

import ast
from pathlib import Path
import subprocess
import sys
import unittest


class ArchitectureTests(unittest.TestCase):
    def test_discovery_can_be_imported_without_application_or_optional_backends(self):
        subprocess.run([sys.executable, "-c", """
import sys
import gps_rssi.discovery
unwanted = {'gps_rssi.app', 'gps_rssi.live', 'gps_rssi.storage', 'gps_rssi.scan_cli',
            'bleak', 'benlink', 'tomlkit'}
assert not unwanted.intersection(sys.modules), unwanted.intersection(sys.modules)
"""], check=True, capture_output=True, text=True)

    def test_reports_and_measurement_rules_do_not_load_sqlite_or_the_server(self):
        subprocess.run([sys.executable, "-c", """
import sys
import gps_rssi.measurements
import gps_rssi.analysis
import gps_rssi.mapping
unwanted = {'gps_rssi.storage', 'gps_rssi.app', 'gps_rssi.live', 'sqlite3'}
assert not unwanted.intersection(sys.modules), unwanted.intersection(sys.modules)
"""], check=True, capture_output=True, text=True)

    def test_first_party_module_dependencies_are_acyclic(self):
        package = Path(__file__).resolve().parents[1] / 'src/gps_rssi'
        graph = {}
        for path in package.glob('*.py'):
            graph[path.stem] = {node.module.split('.')[0] for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
                                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module}
        def visit(name, stack):
            self.assertNotIn(name, stack, 'Dependency cycle: ' + ' -> '.join([*stack, name]))
            for dependency in graph.get(name, set()):
                visit(dependency, [*stack, name])
        for module in graph:
            visit(module, [])


if __name__ == '__main__':
    unittest.main()
