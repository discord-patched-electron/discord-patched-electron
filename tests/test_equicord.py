import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('equicord', Path(__file__).resolve().parents[1] / 'integration/launcher/equicord.py')
equicord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(equicord)


class Equicord(unittest.TestCase):
    def test_package_replacement_refreshes_only_private_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'application'
            home = Path(temp) / 'mod'
            (root/'resources').mkdir(parents=True)
            home.mkdir()
            (home/'desktop.asar').write_bytes(b'mod')
            original = root/'resources/app.asar'
            original.write_bytes(b'first')
            app = equicord.prepare(home, root)
            self.assertEqual((app.parent/'_app.asar').read_bytes(),b'first')
            original.write_bytes(b'updated')
            self.assertEqual(equicord.prepare(home, root),app)
            self.assertEqual((app.parent/'_app.asar').read_bytes(),b'updated')
            self.assertEqual(original.read_bytes(),b'updated')
            self.assertEqual((home/'desktop.asar').read_bytes(),b'mod')
