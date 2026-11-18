#!/usr/bin/env python3
"""Install kkvm Windows controls and three desktop entries without running the VM."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent


def desktop_entries():
    entries = {}
    for filename, name, action, arguments, icon, comment in (
        ('win10_start-launcher.desktop', 'win10_start', 'start', '', 'computer', '启动 Windows'),
        ('win10_stop.desktop', 'win10_stop', 'stop', '', 'system-shutdown', '正常关闭 Windows'),
        ('win10_stop-force.desktop', 'win10_stop -f', 'stop', ' -f', 'process-stop', '强制关闭 Windows，未保存的数据可能丢失'),
    ):
        entries[filename] = (
            '[Desktop Entry]\nVersion=1.0\nType=Application\n'
            f'Name={name}\nComment={comment}\n'
            f'Exec=/usr/local/bin/win10_{action}{arguments}\n'
            f'TryExec=/usr/local/bin/win10_{action}\nIcon={icon}\n'
            'Terminal=true\nCategories=System;\n'
        )
    return entries


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_file(destination, data, mode, uid, gid):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError('拒绝覆盖符号链接：' + str(destination))
    fd, name = tempfile.mkstemp(prefix='.kkvm-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, mode)
        os.chown(name, uid, gid)
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def snapshot(paths, backup):
    records = []
    for index, item in enumerate(paths):
        item = Path(item)
        if item.is_symlink():
            raise ValueError('拒绝修改符号链接：' + str(item))
        record = {'path': str(item), 'existed': item.exists()}
        if item.exists():
            data = item.read_bytes()
            stat = item.stat()
            saved = str(index) + '.before'
            (backup / saved).write_bytes(data)
            record.update(file=saved, sha256=digest(data), mode=stat.st_mode & 0o777,
                          uid=stat.st_uid, gid=stat.st_gid)
        records.append(record)
    (backup / 'before.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    return records


def install(args):
    import pwd
    account = pwd.getpwnam(args.user or os.environ.get('SUDO_USER') or '')
    if account.pw_uid == 0:
        raise ValueError('请使用 --user 指定需要桌面快捷方式的普通用户。')
    home = Path(account.pw_dir).resolve()
    if args.desktop:
        desktop = Path(args.desktop).resolve()
    else:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith('XDG_')}
        clean_env.update(HOME=str(home), USER=account.pw_name, LOGNAME=account.pw_name)
        desktop = Path(subprocess.check_output(
            ['runuser', '-u', account.pw_name, '--', 'xdg-user-dir', 'DESKTOP'],
            env=clean_env, text=True).strip()).resolve()
    if desktop == home or not desktop.is_relative_to(home):
        raise ValueError('桌面必须是目标用户 home 内的独立目录；请用 --desktop 指定。')
    config_path = Path('/etc/kkvm/win10.json')
    previous = json.loads(config_path.read_text()) if config_path.exists() else {}
    domain = args.domain or previous.get('uuid', 'kkvm')
    domain_uuid = subprocess.check_output(
        ['/usr/bin/virsh', '-c', 'qemu:///system', 'domuuid', domain], text=True).strip()
    domain_uuid = str(uuid.UUID(domain_uuid))
    start = Path('/usr/local/bin/win10_start')
    preserve_start = start.exists() or start.is_symlink()
    if preserve_start and (not start.is_file() or not os.access(start, os.X_OK)):
        raise ValueError('已有 win10_start 不是可执行文件，未覆盖。')
    service = args.start_service or previous.get('start_service')
    if args.start_service and preserve_start:
        raise ValueError('已保留现有 win10_start；不能同时指定 --start-service。')
    if service:
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*[.]service', service):
            raise ValueError('无效的启动服务名称。')
        loaded = subprocess.check_output(
            ['/usr/bin/systemctl', 'show', '-p', 'LoadState', '--value', service], text=True).strip()
        if loaded != 'loaded':
            raise ValueError('动态启动服务未安装：' + service)
    config = {'uuid': domain_uuid, 'start_service': service}
    entries = desktop_entries()
    # Validate every desktop entry before replacing any installed file.
    with tempfile.TemporaryDirectory(prefix='kkvm-desktop-') as temp:
        for name, content in entries.items():
            p = Path(temp) / name
            p.write_text(content, encoding='utf-8')
            subprocess.run(['desktop-file-validate', str(p)], check=True)
    desktop.mkdir(parents=True, exist_ok=True)
    os.chown(desktop, account.pw_uid, account.pw_gid)
    writes = {
        Path('/usr/local/lib/kkvm/win10_control.py'): ((HERE / 'control.py').read_bytes(), 0o644, 0, 0),
        Path('/usr/local/bin/win10_stop'): ((HERE / 'win10_stop').read_bytes(), 0o755, 0, 0),
        config_path: ((json.dumps(config, indent=2) + '\n').encode(), 0o644, 0, 0),
    }
    if not preserve_start:
        writes[start] = ((HERE / 'win10_start').read_bytes(), 0o755, 0, 0)
    for name, content in entries.items():
        writes[desktop / name] = (content.encode(), 0o755, account.pw_uid, account.pw_gid)
    legacy = desktop / 'win10_start.desktop'
    backup = Path('/var/lib/kkvm/launcher-backups') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup.mkdir(parents=True, mode=0o700)
    before = snapshot([*writes, legacy], backup)
    retained_start = digest(start.read_bytes()) if preserve_start else None
    for p, (data, mode, uid, gid) in writes.items():
        write_file(p, data, mode, uid, gid)
        if p.read_bytes() != data:
            raise RuntimeError('安装校验失败：' + str(p))
    if legacy.exists():
        legacy.unlink()
    if preserve_start and digest(start.read_bytes()) != retained_start:
        raise RuntimeError('已有启动入口发生变化。')
    after = [{'path': str(p), 'sha256': digest(p.read_bytes())} for p in writes]
    (backup / 'after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
    print('已安装桌面入口：win10_start、win10_stop、win10_stop -f')
    print('桌面：' + str(desktop))
    print('备份与 SHA256：' + str(backup))
    print('已有动态启动入口已保留。' if preserve_start else '新启动入口使用指定服务或 libvirt 及其现有 hooks。')
    print('安装未启动、停止或重启虚拟机。')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--user', help='桌面所属的 Linux 用户，默认 sudo 调用者')
    p.add_argument('--domain', help='libvirt 域名或 UUID，首次默认 kkvm；重装保留原绑定')
    p.add_argument('--desktop', help='显式指定该用户 home 内的桌面目录')
    p.add_argument('--start-service', help='无现有 win10_start 时，使用已部署的动态 GPU/USB 启动服务')
    args = p.parse_args()
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '--', '/usr/bin/python3', str(Path(__file__).resolve()), *sys.argv[1:]])
    install(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('安装失败：' + str(error), file=sys.stderr)
        sys.exit(1)
