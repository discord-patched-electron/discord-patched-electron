import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('window_inventory', Path(__file__).resolve().parents[1] / 'integration/audio/window_inventory.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class WindowInventory(unittest.TestCase):
    def test_sway_nested_floating_and_xwayland_windows(self):
        tree = {'nodes':[{'type':'con','pid':20,'name':'One','app_id':'one'},
            {'floating_nodes':[{'type':'con','pid':21,'name':'Two','app_id':None,'window_properties':{'class':'two'}}]}]}
        rows = inventory.sway_windows(tree)
        self.assertEqual({(r['pid'],r['app_id']) for r in rows},{(20,'one'),(21,'two')})

    def test_hyprland_and_gnome_are_explicit_metadata_only_queries(self):
        with patch.dict(inventory.os.environ, {'HYPRLAND_INSTANCE_SIGNATURE':'test'}, clear=True), \
                patch.object(inventory, 'run', return_value='[{"pid":25,"class":"zen","title":"Window"}]') as command:
            self.assertEqual(inventory.windows()[0]['pid'],25)
            command.assert_called_once_with(['/usr/bin/hyprctl','-j','clients'])
        import json
        reply=json.dumps({'type':'s','data':[json.dumps([{'pid':26,'wm_class':'app','title':'Window'}])]})
        with patch.dict(inventory.os.environ, {'XDG_CURRENT_DESKTOP':'GNOME'}, clear=True), patch.object(inventory,'run',return_value=reply):
            self.assertEqual(inventory.windows()[0]['backend'],'gnome-window-calls')

    def test_missing_optional_backend_and_bad_pid_are_ignored(self):
        with patch.dict(inventory.os.environ, {'XDG_CURRENT_DESKTOP':'KDE'}, clear=True), patch.object(inventory,'run',side_effect=FileNotFoundError):
            self.assertEqual(inventory.windows(),[])
        self.assertEqual(inventory.normalize([{'pid':True},{'pid':'123'},{'pid':0}], 'test'),[])

    def test_desktop_notes_do_not_promise_automatic_capture_matching(self):
        for desktop, dependency in [('KDE', 'kdotool'), ('GNOME', 'Window Calls')]:
            with patch.dict(inventory.os.environ, {'XDG_CURRENT_DESKTOP':desktop}, clear=True), \
                    patch.object(inventory.Path, 'is_file', return_value=False):
                note = inventory.desktop_note()
                self.assertIn(dependency, note)
                self.assertIn('manual', note)
