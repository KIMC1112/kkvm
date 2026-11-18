import configparser
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'resources/win10' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

control = load('kkvm_win10_control', 'control.py')
installer = load('kkvm_win10_installer', 'install.py')
UUID = '00000000-0000-4000-8000-000000000001'
CONFIG = {'uuid': UUID, 'start_service': None}


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.state = 'running'
        self.operations = []
        self.uuid = UUID
        self.mock = patch.object(control, 'command', side_effect=self.command)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def command(self, args):
        self.operations.append(args)
        if args[0] == '/usr/bin/systemctl':
            return ''
        self.assertEqual(args[:3], ['/usr/bin/virsh', '-c', 'qemu:///system'])
        self.assertEqual(args[-1], UUID)
        operation = args[3]
        if operation == 'domuuid':
            return self.uuid
        if operation == 'domstate':
            return self.state
        if operation == 'shutdown':
            self.state = 'in shutdown'
        elif operation == 'destroy':
            self.state = 'shut off'
        elif operation == 'start':
            self.state = 'running'
        return ''

    def changes(self):
        return [a[3] for a in self.operations if a[0] == '/usr/bin/virsh' and a[3] not in ('domstate', 'domuuid')]

    def test_normal_stop_timeout_never_escalates(self):
        expected = control.request_control(CONFIG, 'stop')
        with self.assertRaises(RuntimeError):
            control.finish(CONFIG, expected, timeout=0)
        self.assertEqual(self.changes(), ['shutdown'])

    def test_force_uses_destroy_only(self):
        expected = control.request_control(CONFIG, 'stop', force=True)
        control.finish(CONFIG, expected, timeout=0)
        self.assertEqual(self.changes(), ['destroy'])

    def test_force_can_follow_pending_normal_stop(self):
        control.request_control(CONFIG, 'stop')
        expected = control.request_control(CONFIG, 'stop', force=True)
        control.finish(CONFIG, expected, timeout=0)
        self.assertEqual(self.changes(), ['shutdown', 'destroy'])

    def test_stopped_vm_no_operation(self):
        self.state = 'shut off'
        self.assertIsNone(control.request_control(CONFIG, 'stop'))
        self.assertIsNone(control.request_control(CONFIG, 'stop', force=True))
        self.assertEqual(self.changes(), [])

    def test_uuid_mismatch_refuses_operation(self):
        self.uuid = '00000000-0000-4000-8000-000000000002'
        with self.assertRaises(RuntimeError):
            control.request_control(CONFIG, 'stop', force=True)
        self.assertEqual(self.changes(), [])

    def test_running_start_is_idempotent(self):
        self.assertIsNone(control.request_control(CONFIG, 'start'))
        self.assertEqual(self.changes(), [])

    def test_start_uses_existing_service_when_configured(self):
        self.state = 'shut off'
        expected = control.request_control(dict(CONFIG, start_service='kkvm-gpu-start.service'), 'start')
        self.assertEqual(expected, 'running')
        self.assertIn(['/usr/bin/systemctl', 'start', '--no-block', 'kkvm-gpu-start.service'], self.operations)
        self.assertEqual(self.changes(), [])

    def test_start_uses_libvirt_with_hooks(self):
        self.state = 'shut off'
        control.finish(CONFIG, control.request_control(CONFIG, 'start'), timeout=0)
        self.assertEqual(self.changes(), ['start'])

    def test_paused_requires_explicit_force(self):
        self.state = 'paused'
        for action in ('start', 'stop'):
            with self.assertRaises(RuntimeError):
                control.request_control(CONFIG, action)
        self.assertEqual(self.changes(), [])
        control.request_control(CONFIG, 'stop', force=True)
        self.assertEqual(self.changes(), ['destroy'])

    def test_command_failure_does_not_fallback_to_kill(self):
        with patch.object(control, 'virsh', side_effect=[UUID, 'running', subprocess.CalledProcessError(1, 'virsh')]) as call:
            with self.assertRaises(subprocess.CalledProcessError):
                control.request_control(CONFIG, 'stop', force=True)
            self.assertEqual(call.call_count, 3)

    def test_start_rejects_force_before_privilege_escalation(self):
        with patch.object(control.os, 'execv') as execute:
            with self.assertRaises(SystemExit) as result:
                control.main(['start', '-f'])
            self.assertEqual(result.exception.code, 2)
            execute.assert_not_called()


class InstallerTests(unittest.TestCase):
    def test_desktop_actions_are_distinct_and_use_fixed_entrypoints(self):
        parsed = {}
        for text in installer.desktop_entries().values():
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(text)
            section = cfg['Desktop Entry']
            parsed[section['Name']] = section['Exec']
            self.assertEqual(section['Terminal'], 'true')
        self.assertEqual(parsed, {
            'win10_start': '/usr/local/bin/win10_start',
            'win10_stop': '/usr/local/bin/win10_stop',
            'win10_stop -f': '/usr/local/bin/win10_stop -f',
        })

    def test_install_preserves_dynamic_start_and_backs_up_replaced_files(self):
        self.exercise_install(preserve=True)

    def test_fresh_install_and_reinstall_are_consistent(self):
        self.exercise_install(preserve=False)

    def exercise_install(self, preserve):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / 'home/test-user'
            desktop = home / 'Desktop'
            desktop.mkdir(parents=True)
            def mapped(p):
                s = str(p)
                if s.startswith(('/usr/local/', '/etc/kkvm/', '/var/lib/kkvm/')):
                    return root / s.lstrip('/')
                return Path(p)
            start = mapped('/usr/local/bin/win10_start')
            stop = mapped('/usr/local/bin/win10_stop')
            start.parent.mkdir(parents=True)
            if preserve:
                start.write_bytes(b'#!/bin/sh\n# existing dynamic GPU/USB launcher\n')
                start.chmod(0o755)
            stop.write_bytes(b'old stop tool')
            legacy = desktop / 'win10_start.desktop'
            legacy.write_bytes(b'old desktop entry')
            account = types.SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name='test-user', pw_dir=str(home))
            fake_pwd = types.SimpleNamespace(getpwnam=lambda name: account)
            args = types.SimpleNamespace(user='test-user', desktop=str(desktop), domain='kkvm', start_service=None)
            subprocess_calls = []
            def read_only_query(argv, **kwargs):
                subprocess_calls.append(argv)
                self.assertEqual(argv, ['/usr/bin/virsh', '-c', 'qemu:///system', 'domuuid', 'kkvm'])
                return UUID + '\n'
            def validate(argv, **kwargs):
                self.assertEqual(argv[0], 'desktop-file-validate')
                self.assertTrue(Path(argv[1]).is_file())
            with patch.dict(sys.modules, {'pwd': fake_pwd}), patch.object(installer, 'Path', side_effect=mapped), \
                    patch.object(installer.os, 'chown', create=True), patch.object(installer.subprocess, 'check_output', side_effect=read_only_query), \
                    patch.object(installer.subprocess, 'run', side_effect=validate):
                installer.install(args)
                first_start = start.read_bytes()
                installer.install(args)
            self.assertEqual(len(subprocess_calls), 2)
            self.assertEqual(start.read_bytes(), first_start)
            if preserve:
                self.assertEqual(first_start, b'#!/bin/sh\n# existing dynamic GPU/USB launcher\n')
            else:
                self.assertEqual(first_start, (installer.HERE / 'win10_start').read_bytes())
            self.assertEqual(len(list(desktop.glob('*.desktop'))), 3)
            self.assertFalse(legacy.exists())
            self.assertEqual(json.loads(mapped('/etc/kkvm/win10.json').read_text())['uuid'], UUID)
            backups = sorted(mapped('/var/lib/kkvm/launcher-backups').iterdir())
            first = json.loads((backups[0] / 'before.json').read_text())
            old_stop = next(r for r in first if r['path'] == str(stop))
            self.assertEqual((backups[0] / old_stop['file']).read_bytes(), b'old stop tool')
            # Restore into a separate file, leaving the final installed command intact.
            restored = root / 'restored-stop'
            restored.write_bytes((backups[0] / old_stop['file']).read_bytes())
            self.assertEqual(installer.digest(restored.read_bytes()), old_stop['sha256'])
            self.assertEqual(stop.read_bytes(), (installer.HERE / 'win10_stop').read_bytes())


if __name__ == '__main__':
    unittest.main()
