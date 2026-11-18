#!/usr/bin/env python3
"""Pinned CachyOS kernel provisioning. No reboot, driver removal, or default boot change."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / 'versions.json').read_text(encoding='utf-8'))
NAMES = ('linux-cachyos', 'linux-cachyos-headers')

def run(args, check=True):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=check, text=True, capture_output=False)

def output(args):
    return subprocess.check_output(args, text=True).strip()

def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def installed():
    return {name: output(['pacman', '-Q', name]).split()[1] for name in NAMES}

def require_kernel(running=True):
    versions = installed()
    if any(v != LOCK['package_version'] for v in versions.values()):
        raise RuntimeError('Kernel AND headers must be exactly ' + LOCK['package_version'])
    if running and os.uname().release != LOCK['kernel_release']:
        raise RuntimeError('Boot ' + LOCK['kernel_release'] + ' manually before building this profile')
    return versions

def merge_pin(text):
    lines = text.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if line.strip() == '[options]']
    if len(positions) != 1:
        raise ValueError('Expected one pacman [options] section')
    section = False
    present = set()
    for line in lines:
        s = line.strip()
        if s.startswith('['):
            section = s == '[options]'
        if section and s.split('=', 1)[0].strip() == 'IgnorePkg' and '=' in s:
            present.update(s.split('=', 1)[1].split('#', 1)[0].split())
    missing = [name for name in NAMES if name not in present]
    if not missing:
        return text
    lines.insert(positions[0] + 1, '# kkvm: fixed kernel; see profiles/cachyos-7.0.12-acpi\nIgnorePkg = ' + ' '.join(missing) + '\n')
    return ''.join(lines)

def atomic_write(path, data):
    path = Path(path)
    mode = path.stat().st_mode & 0o777
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def pin_file(path, backup):
    path, backup = Path(path), Path(backup)
    before = path.read_bytes()
    after = merge_pin(before.decode()).encode()
    if before == after:
        return False
    if backup.exists():
        raise ValueError('Backup already exists')
    backup.mkdir(parents=True)
    (backup / 'pacman.conf.before').write_bytes(before)
    (backup / 'pin-manifest.json').write_text(json.dumps({'before': hashlib.sha256(before).hexdigest(), 'after': hashlib.sha256(after).hexdigest()}))
    atomic_write(path, after)
    return True

def restore_pin(path, backup):
    path, backup = Path(path), Path(backup)
    manifest = json.loads((backup / 'pin-manifest.json').read_text(encoding='utf-8'))
    original = backup / 'pacman.conf.before'
    if digest(original) != manifest['before'] or digest(path) != manifest['after']:
        raise ValueError('Configuration or backup changed; refusing to overwrite later edits')
    atomic_write(path, original.read_bytes())

def verify_packages(directory):
    directory = Path(directory).resolve()
    for name, expected in {**LOCK['packages'], **LOCK['signatures']}.items():
        p = directory / name
        if not p.is_file() or digest(p) != expected:
            raise ValueError('Missing/mismatched locked package: ' + name)
    for name in LOCK['packages']:
        run(['pacman-key', '--verify', directory / (name + '.sig'), directory / name])
    return [directory / name for name in LOCK['packages']]

def require_root():
    if os.geteuid() != 0:
        raise RuntimeError('Run this operation with sudo')

def backup_path():
    root = Path('/var/lib/kkvm/kernel-7.0.12')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=root))

def install_kernel(directory):
    require_root()
    if 'ID=cachyos' not in Path('/etc/os-release').read_text(encoding='utf-8').replace('"', ''):
        raise RuntimeError('Automatic kernel install is restricted to CachyOS')
    packages = verify_packages(directory)  # no writes before integrity checks
    if Path('/var/lib/pacman/db.lck').exists():
        raise RuntimeError('Pacman is busy')
    versions = installed()
    if all(v == LOCK['package_version'] for v in versions.values()):
        print('Exact kernel and headers already installed; nothing replaced.')
        return
    for name in ('pacman', 'pacman-key', 'mkinitcpio', 'grub-mkconfig', 'grub-script-check'):
        if not shutil.which(name):
            raise RuntimeError('Required tool missing: ' + name)
    # Only the audited GRUB/mkinitcpio layout is automated. Keep a bootable rescue kernel.
    for p in ('/boot/grub/grub.cfg', '/boot/vmlinuz-linux-cachyos-lts', '/boot/initramfs-linux-cachyos-lts.img'):
        if not Path(p).is_file() or Path(p).stat().st_size == 0:
            raise RuntimeError('Missing GRUB/LTS rescue artifact: ' + p)
    output(['pacman', '-Q', 'linux-cachyos-lts'])
    cpu = Path('/proc/cpuinfo').read_text(encoding='utf-8')
    if 'AuthenticAMD' not in cpu or not all(flag in cpu.split() for flag in ('avx2', 'bmi1', 'bmi2', 'fma', 'f16c', 'movbe', 'xsave')):
        raise RuntimeError('This profile requires AMD x86-64-v3 hardware')
    # Do not remove kernel-bound NVIDIA packages or rewrite DKMS settings automatically.
    pkgs = output(['pacman', '-Qq']).splitlines()
    if any(p.startswith('linux-cachyos-nvidia') for p in pkgs):
        raise RuntimeError('Kernel-bound NVIDIA package present: resolve driver compatibility manually first')
    old = []
    for name, version in versions.items():
        candidates = list(Path('/var/cache/pacman/pkg').glob(name + '-' + version + '-*.pkg.tar.*'))
        candidates = [p for p in candidates if not p.name.endswith('.sig')]
        candidates = [p for p in candidates if output(['pacman', '-Qp', str(p)]).split() == [name, version]]
        if not candidates:
            raise RuntimeError('Cache the installed rollback package before downgrading: ' + name + ' ' + version)
        p = candidates[0]
        run(['pacman-key', '--verify', str(p) + '.sig', p])
        old.append(p)
    if input('Type INSTALL-7.0.12 to install without rebooting: ') != 'INSTALL-7.0.12':
        raise RuntimeError('Cancelled')
    backup = backup_path()
    for p in old:
        shutil.copy2(p, backup / p.name)
        shutil.copy2(str(p) + '.sig', backup / (p.name + '.sig'))
    shutil.copy2('/etc/pacman.conf', backup / 'pacman.conf.original')
    shutil.copy2('/boot/grub/grub.cfg', backup / 'grub.cfg.original')
    for pattern in ('vmlinuz-linux-cachyos*', 'initramfs-linux-cachyos*.img'):
        for p in Path('/boot').glob(pattern):
            shutil.copy2(p, backup / p.name)
    (backup / 'rollback-packages.json').write_text(json.dumps({p.name: digest(backup / p.name) for p in old}, indent=2))
    print('Backup: ' + str(backup), flush=True)
    print('On failure: do not reboot. Reinstall the signed rollback packages here; use the retained LTS rescue boot if needed.', flush=True)
    # No --noconfirm, --nodeps, forced conflicts, package removals, or kernel upgrades.
    run(['pacman', '-U', '--', *packages])
    require_kernel(running=False)
    run(['mkinitcpio', '-p', 'linux-cachyos'])
    temp_cfg = backup / 'grub.cfg.new'
    run(['grub-mkconfig', '-o', temp_cfg])
    run(['grub-script-check', temp_cfg])
    atomic_write('/boot/grub/grub.cfg', temp_cfg.read_bytes())
    pin_file('/etc/pacman.conf', backup / 'pin')
    print('Installed and pinned. No reboot/default-boot/driver change performed. Select 7.0.12 manually at the next maintenance reboot.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    sub.add_parser('pin')
    for command in ('verify-packages', 'install'):
        p = sub.add_parser(command)
        p.add_argument('directory')
    p = sub.add_parser('restore-pin')
    p.add_argument('backup')
    a = parser.parse_args()
    if a.command == 'check':
        print(json.dumps({'installed': require_kernel(), 'running': os.uname().release}))
    elif a.command == 'pin':
        require_root()
        require_kernel()
        backup = backup_path() / 'pin'
        print('Pin changed:', pin_file('/etc/pacman.conf', backup), 'backup:', backup)
    elif a.command == 'restore-pin':
        require_root()
        restore_pin('/etc/pacman.conf', a.backup)
    elif a.command == 'verify-packages':
        verify_packages(a.directory)
    else:
        install_kernel(a.directory)

if __name__ == '__main__':
    main()
