#!/usr/bin/env python3
"""Opt-in build profile: CachyOS 7.0.12 + QEMU 11.0.3 ACPI identity."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import acpi_identity
import kernel
from prepare_source import prepare

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
LOCK = kernel.LOCK

CONFIGURE = [
    '--target-list=x86_64-softmmu', '--without-default-features',
    '--enable-kvm', '--enable-linux-io-uring', '--enable-linux-aio',
    '--enable-pixman', '--enable-opengl', '--enable-sdl',
    '--enable-spice', '--enable-spice-protocol', '--enable-png', '--disable-vnc',
    '--enable-libusb', '--enable-libudev', '--enable-pipewire', '--enable-tpm',
    '--enable-numa', '--enable-seccomp', '--enable-zstd', '--enable-tools', '--enable-modules',
    '--disable-docs', '--disable-werror', '--disable-linux-user', '--disable-bsd-user', '--disable-tcg',
    '--extra-cflags=-g0', '--extra-ldflags=-s',
]

def run(args, cwd=None, log=None, env=None):
    args = list(map(str, args))
    record = {'argv': args, 'cwd': str(cwd) if cwd else None}
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    if log:
        with Path(log).open('a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
    print('+ ' + ' '.join(args), flush=True)
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    if result.returncode:
        raise RuntimeError('Command failed; see retained build records: ' + args[0])
    return result.stdout

def file_manifest(root):
    result = {}
    root = Path(root)
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            if not p.resolve().is_relative_to(root.resolve()):
                raise ValueError('External symlink in artifact: ' + str(p))
            result[str(p.relative_to(root))] = {'link': os.readlink(p)}
        elif p.is_file():
            result[str(p.relative_to(root))] = {'sha256': kernel.digest(p)}
    return result

def build(jobs):
    if os.geteuid() == 0:
        raise RuntimeError('Build as a normal user, not root; only capture uses sudo')
    if jobs < 1:
        raise ValueError('jobs must be positive')
    kernel.require_kernel()
    if 'AuthenticAMD' not in Path('/proc/cpuinfo').read_text(encoding='utf-8'):
        raise RuntimeError('This profile is AMD-only')
    for command in ('git', 'ninja', 'cc', 'pkg-config', 'sudo'):
        if not shutil.which(command):
            raise RuntimeError('Install required build tool first: ' + command)
    # Do not run package upgrades to satisfy dependencies. Configure reports missing libraries.
    patch = (REPO / 'patches/QEMU/AMD-v11.0.3.patch').read_text(encoding='utf-8').encode()
    if hashlib.sha256(patch).hexdigest() != LOCK['amd_patch_sha256']:
        raise ValueError('AMD patch changed; review and update the version lock first')
    root = REPO / '.local'
    root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='cachyos7012-acpi-', dir=root))
    os.chmod(work, 0o700)
    log = work / 'commands.jsonl'
    source = work / 'source'
    prefix = Path('/opt/kkvm/builds') / work.name
    if prefix.exists():
        raise RuntimeError('Install destination already exists')
    print('BUILD RECORDS: ' + str(work), flush=True)
    # Do not save host table bodies or machine serials; only whitelisted 26-byte identities.
    captured = run(['sudo', 'python3', HERE / 'acpi_identity.py', 'capture'], log=log)
    identity = json.loads(captured)
    acpi_identity.validate_identity(identity)
    (work / 'identity.json').write_text(json.dumps(identity, indent=2))
    run(['git', 'init', source], log=log)
    run(['git', 'config', 'core.autocrlf', 'false'], cwd=source, log=log)
    run(['git', 'remote', 'add', 'origin', 'https://github.com/qemu/qemu.git'], cwd=source, log=log)
    run(['git', 'fetch', '--depth=1', 'origin', LOCK['qemu_commit']], cwd=source, log=log)
    run(['git', 'checkout', '--detach', 'FETCH_HEAD'], cwd=source, log=log)
    commit = run(['git', 'rev-parse', 'HEAD'], cwd=source, log=log).strip()
    if commit != LOCK['qemu_commit'] or (source / 'VERSION').read_text(encoding='utf-8').strip() != '11.0.3':
        raise ValueError('QEMU source identity mismatch')
    patch_path = work / 'AMD-v11.0.3.patch'
    patch_path.write_bytes(patch)
    run(['git', 'apply', '--check', patch_path], cwd=source, log=log)
    run(['git', 'apply', patch_path], cwd=source, log=log)
    prepare(source, identity)
    acpi_identity.apply(source, identity, work / 'acpi-evidence')
    (work / 'source.diff').write_text(run(['git', 'diff', '--binary'], cwd=source, log=log))
    # Include untracked driver evidence; git diff alone omits new files added by git apply.
    (work / 'rtl8125.sha256').write_text(kernel.digest(source / 'hw/net/rtl8125.c') + '\n')
    run(['./configure', '--prefix=' + str(prefix), *CONFIGURE], cwd=source, log=log)
    run(['ninja', '-C', 'build', '-j' + str(jobs)], cwd=source, log=log)
    binary = source / 'build/qemu-system-x86_64'
    version = run([binary, '--version'], cwd=source, log=log)
    devices = run([binary, '-device', 'help'], cwd=source, log=log)
    if '11.0.3' not in version or 'rtl8125' not in devices:
        raise ValueError('Built executable failed version/RTL8125 checks')
    stage = work / 'stage'
    env = dict(os.environ, DESTDIR=str(stage))
    run(['ninja', '-C', 'build', 'install'], cwd=source, log=log, env=env)
    artifact = stage / prefix.relative_to('/')
    standard = artifact / 'bin/qemu-system-x86_64'
    renamed = artifact / 'bin/qemu-system-x86_64-acpi-identity'
    standard.rename(renamed)
    metadata = {'profile': 'cachyos-7.0.12-acpi', 'created_utc': datetime.now(timezone.utc).isoformat(),
                'kernel': os.uname().release, 'qemu_commit': commit, 'prefix': str(prefix),
                'artifact': str(artifact), 'configure': CONFIGURE, 'files': file_manifest(artifact)}
    (work / 'build.json').write_text(json.dumps(metadata, indent=2))
    print('Built, not activated. Install explicitly with:')
    print('sudo python3 ' + str(HERE / 'profile.py') + ' install ' + str(work / 'build.json'))
    return work

def install(metadata_path):
    kernel.require_root()
    metadata_path = Path(metadata_path).resolve()
    m = json.loads(metadata_path.read_text(encoding='utf-8'))
    if m['profile'] != 'cachyos-7.0.12-acpi' or m['qemu_commit'] != LOCK['qemu_commit'] or m['kernel'] != LOCK['kernel_release']:
        raise ValueError('Unexpected build profile')
    artifact = Path(m['artifact']).resolve()
    prefix = Path(m['prefix'])
    parent = Path('/opt/kkvm/builds')
    if prefix.parent != parent or not prefix.name.startswith('cachyos7012-acpi-'):
        raise ValueError('Unsafe installation destination')
    if artifact != metadata_path.parent / 'stage' / prefix.relative_to('/'):
        raise ValueError('Unexpected staged artifact path')
    if file_manifest(artifact) != m['files']:
        raise ValueError('Artifact changed since build; installation refused')
    kernel.require_kernel()
    import fcntl
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / '.install.lock').open('w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if prefix.exists():
            raise FileExistsError('Refusing to overwrite an existing build: ' + str(prefix))
        staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=parent))
        shutil.copytree(artifact, staging, symlinks=True, dirs_exist_ok=True)
        if file_manifest(staging) != m['files']:
            raise ValueError('Copy verification failed; staging retained: ' + str(staging))
        shutil.copy2(metadata_path, staging / 'kkvm-build.json')
        staging.rename(prefix)
    print('Installed: ' + str(prefix / 'bin/qemu-system-x86_64-acpi-identity'))
    print('No VM XML, active emulator, desktop shortcut, driver, firmware, or boot setting was changed.')
    print('Keep the previous emulator path for rollback; switch only during a planned VM shutdown.')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest='command', required=True)
    s.add_parser('check')
    s.add_parser('pin-kernel')
    for name in ('verify-packages', 'install-kernel'):
        a = s.add_parser(name)
        a.add_argument('directory')
    b = s.add_parser('build')
    b.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
    i = s.add_parser('install')
    i.add_argument('manifest')
    a = p.parse_args()
    if a.command == 'check':
        print(json.dumps({'installed': kernel.require_kernel(), 'running': os.uname().release}))
    elif a.command == 'pin-kernel':
        kernel.require_root()
        kernel.require_kernel()
        dest = kernel.backup_path() / 'pin'
        print('Pin changed:', kernel.pin_file('/etc/pacman.conf', dest), 'backup:', dest)
    elif a.command == 'verify-packages':
        kernel.verify_packages(a.directory)
    elif a.command == 'install-kernel':
        kernel.install_kernel(a.directory)
    elif a.command == 'build':
        build(a.jobs)
    else:
        install(a.manifest)

if __name__ == '__main__':
    main()
