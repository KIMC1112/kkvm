#!/usr/bin/env python3
"""kkvm Windows VM controls. Never stop the Linux host or force on timeout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

CONFIG = Path('/etc/kkvm/win10.json')


def command(argv):
    result = subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=dict(os.environ, LC_ALL='C'), timeout=30)
    return result.stdout.strip()


def virsh(config, operation):
    return command(['/usr/bin/virsh', '-c', 'qemu:///system', operation, config['uuid']])


def wait_state(config, expected, timeout):
    deadline = time.monotonic() + timeout
    while True:
        state = virsh(config, 'domstate')
        if state == expected:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('等待超时，当前状态：' + state + '；未追加强制关闭或重置命令。')
        time.sleep(1)


def request_control(config, action, force=False):
    # UUID binding survives a display-name change and cannot select another VM by accident.
    if virsh(config, 'domuuid').lower() != config['uuid'].lower():
        raise RuntimeError('虚拟机 UUID 不匹配，未操作。')
    state = virsh(config, 'domstate')
    if action == 'start':
        if state == 'running':
            print('Windows 已运行，无需重复启动。')
            return
        if state != 'shut off':
            raise RuntimeError('当前状态不允许启动：' + state)
        service = config.get('start_service')
        if service:
            command(['/usr/bin/systemctl', 'start', '--no-block', service])
        else:
            virsh(config, 'start')
        return 'running'
    if state == 'shut off':
        print('Windows 已关闭，无需操作。')
        return
    if force:
        if state not in ('running', 'paused', 'in shutdown', 'pmsuspended', 'crashed', 'blocked'):
            raise RuntimeError('当前状态不支持强制关闭：' + state)
        print('正在强制关闭 Windows；未保存的数据可能丢失。', flush=True)
        virsh(config, 'destroy')
    else:
        if state not in ('running', 'in shutdown', 'blocked'):
            raise RuntimeError('当前状态不支持正常关机：' + state + '；如需断电请显式使用 -f。')
        if state != 'in shutdown':
            virsh(config, 'shutdown')
    return 'shut off'


def finish(config, expected, timeout=150):
    if expected is not None:
        wait_state(config, expected, timeout)
        if expected == 'running':
            print('Windows 虚拟机已运行；桌面加载及设备切换由既有部署负责。')
        else:
            print('Windows 已关闭；显卡和 USB 归还由既有 hooks/服务处理。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop'))
    parser.add_argument('-f', '--force', action='store_true', help='强制关闭 Windows（相当于断电）')
    args = parser.parse_args(argv)
    if args.force and args.action != 'stop':
        parser.error('-f 仅适用于 win10_stop')
    if os.geteuid() != 0:
        extra = ['-f'] if args.force else []
        os.execv('/usr/bin/sudo', ['sudo', '--', '/usr/local/bin/win10_' + args.action, *extra])
    stat = CONFIG.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o022:
        raise RuntimeError('控制配置必须归 root 所有，且不能由组或其他用户写入。')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    uuid.UUID(config['uuid'])
    import fcntl
    # Only serialize the command phase with other kkvm controls. Existing GPU hooks
    # retain their own locks; no PCI driver or USB state is modified by this tool.
    with open('/run/lock/kkvm-win10-control.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一启停操作正在进行，请稍后重试。') from None
        expected = request_control(config, args.action, args.force)
    # Release before waiting so an explicit -f can interrupt a pending graceful stop.
    finish(config, expected)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('win10 控制失败：' + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        sys.exit(1)
