#!/usr/bin/python3
import sys,os,pathlib,subprocess,shutil
from xml.etree import ElementTree as ET
r=pathlib.Path(os.environ['VM_NET_TEST_ROOT']);a=sys.argv[1:]
if a[:2]==['-c','qemu:///system']:a=a[2:]
cmd=a[0]
if cmd=='dumpxml': print((r/'domain.xml').read_text())
elif cmd=='list': print('kkvm')
elif cmd=='domstate': print((r/'vm-state').read_text())
elif cmd=='domiflist': print('vguest network default rtl8125 02:b0:ec:83:2b:a4')
elif cmd=='domifaddr': print('192.168.122.170/24')
elif cmd=='net-list': print('default' if (r/'active').exists() else '')
elif cmd=='net-dumpxml': print((r/'network.xml').read_text(),end='')
elif cmd=='net-define': shutil.copyfile(a[1],r/'network.xml')
elif cmd=='net-destroy':
    (r/'active').unlink(missing_ok=True)
    subprocess.run(['ip','addr','flush','dev','virbr0'],check=True)
elif cmd=='net-start':
    if (r/'fail-start-once').exists(): (r/'fail-start-once').unlink();sys.exit(42)
    i=ET.parse(r/'network.xml').getroot().find('ip')
    p=i.get('prefix','24');subprocess.run(['ip','addr','add',i.get('address')+'/'+p,'dev','virbr0'],check=True)
    (r/'active').touch()
else:sys.exit('Unsupported mock virsh command '+repr(a))
