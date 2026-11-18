#!/usr/bin/env python3
"""Run only inside test-isolated.sh's private mount AND network namespaces.
Real Linux bridge/nftables/ip routing; libvirt control is mocked, no production VM.
"""
import os, sys, json, subprocess, pathlib, re, time, socket, hashlib, shutil
from xml.etree import ElementTree as ET
ROOT=pathlib.Path(os.environ['VM_NET_TEST_ROOT'])
BIN=str(pathlib.Path(__file__).resolve().parent/'target/release/vm_net')
if not pathlib.Path(BIN).exists(): BIN=str(pathlib.Path(__file__).resolve().parent/'bin/vm_net')
VM='kkvm'
MAC='02:b0:ec:83:2b:a4'
UUID='0e3faeae-4e8a-42ac-a4b2-f6356eb41ea5'
events=[]
server=None
passed=False
def run(*args,ok=True):
    p=subprocess.run(args,text=True,capture_output=True)
    events.append(dict(argv=args,code=p.returncode,stdout=p.stdout,stderr=p.stderr))
    if ok and p.returncode: raise AssertionError(events[-1])
    return p
def vm(*args,ok=True): return run(BIN,*args,ok=ok)
def ping(ns,addr,success=True):
    p=run('ip','netns','exec',ns,'ping','-n','-c','1','-W','1',addr,ok=False)
    assert (p.returncode==0)==success,events[-1]
def backup(p): return pathlib.Path(re.search(r'^Backup: (.+)$',p.stdout,re.M)[1])
def tcp(ns,addr,success=True):
    p=run('ip','netns','exec',ns,'python3','-c',"import socket; socket.create_connection(('"+addr+"',18080),timeout=1).close()",ok=False)
    assert (p.returncode==0)==success,events[-1]
def set_state(s): (ROOT/'vm-state').write_text(s)
def addr(ns,a):run('ip','-n',ns,'addr','flush','dev','eth0');run('ip','-n',ns,'addr','add',a,'dev','eth0')
try:
    for name in ['g','r','other']:
        run('ip','netns','add',name);run('ip','-n',name,'link','set','lo','up')
    run('ip','link','add','virbr0','type','bridge');run('ip','link','set','virbr0','up')
    run('ip','addr','add','192.168.122.1/24','dev','virbr0')
    for ns,host,mac,ipaddr in [('g','vguest',MAC,'192.168.122.170/24'),('other','vother','02:00:00:00:00:02','192.168.122.171/24')]:
        run('ip','link','add',host,'type','veth','peer','name','eth0','netns',ns)
        run('ip','link','set',host,'master','virbr0');run('ip','link','set',host,'up')
        run('ip','-n',ns,'link','set','eth0','address',mac);run('ip','-n',ns,'link','set','eth0','up')
        run('ip','-n',ns,'addr','add',ipaddr,'dev','eth0');run('ip','-n',ns,'route','add','default','via','192.168.122.1')
    run('ip','link','add','up0','type','veth','peer','name','eth0','netns','r')
    run('ip','link','set','up0','up');run('ip','addr','add','10.123.0.2/24','dev','up0')
    run('ip','-n','r','link','set','eth0','up');run('ip','-n','r','addr','add','10.123.0.1/24','dev','eth0')
    for a in ['192.168.1.1/32','203.0.113.9/32']:run('ip','-n','r','addr','add',a,'dev','lo')
    server=subprocess.Popen(['ip','netns','exec','r','python3','-m','http.server','18080','--bind','0.0.0.0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    run('ip','route','add','default','via','10.123.0.1')
    for attempt in range(30):
        try:
            socket.create_connection(('192.168.1.1',18080),timeout=0.2).close();break
        except OSError:time.sleep(0.1)
    else:raise AssertionError('Isolated TCP server did not start')
    run('sysctl','-w','net.ipv4.ip_forward=1')
    nat='table ip test_nat {\n chain post {\n type nat hook postrouting priority srcnat; policy accept;\n oifname "up0" masquerade\n }\n}\n'
    p=subprocess.run(['nft','-f','-'],input=nat,text=True,capture_output=True);assert p.returncode==0,p.stderr
    original_route=run('ip','-4','route','show','table','main').stdout
    ping('g','192.168.1.1');ping('g','203.0.113.9');tcp('g','192.168.1.1')
    vm('status',VM)
    vm('block',VM,'192.168.1.1')
    assert not list(pathlib.Path('/var/lib/vm_net').glob('*.json'))
    p=vm('block',VM,'192.168.1.1','--apply'); b=backup(p)
    ping('g','192.168.1.1',False);ping('g','203.0.113.9');ping('other','192.168.1.1')
    tcp('g','192.168.1.1',False);tcp('g','203.0.113.9');tcp('other','192.168.1.1')
    run('ping','-n','-c','1','-W','1','192.168.1.1')
    vm('rollback',str(b),'--apply');ping('g','192.168.1.1')
    for target in ['10.123.0.1','192.168.122.1']:
        vm('block',VM,target,'--apply');ping('g',target,False);ping('g','203.0.113.9')
        vm('unblock',VM,target,'--apply');ping('g',target)
    p=vm('set',VM,'--uplink-gateway','10.123.0.1','--uplink-dev','up0','--apply');b=backup(p)
    assert run('ip','-4','route','show','table','main').stdout==original_route
    ping('g','203.0.113.9')
    table=json.loads(p.stdout.split('\nBackup:')[0])['route_table']
    result=json.loads(run('ip','-j','route','get','203.0.113.9','from','192.168.122.170','iif','virbr0').stdout)[0]
    assert int(result['table'])==table,result
    vm('rollback',str(b),'--apply')
    assert len(json.loads(run('ip','-j','rule','show').stdout))==3
    assert vm('set',VM,'--uplink-gateway','172.25.1.1','--uplink-dev','up0','--apply',ok=False).returncode!=0
    lan=['set',VM,'--ip','192.168.55.20/24','--gateway','192.168.55.1','--dns','1.1.1.1','--apply']
    assert vm(*lan,'--guest-dhcp-confirmed',ok=False).returncode!=0
    set_state('shut off')
    assert vm(*lan,ok=False).returncode!=0
    original_xml=(ROOT/'network.xml').read_text()
    p=vm(*lan,'--guest-dhcp-confirmed');b=backup(p)
    e=ET.parse(ROOT/'network.xml').getroot()
    assert e.find('ip').get('address')=='192.168.55.1'
    assert e.find('ip/dhcp/host').get('ip')=='192.168.55.20'
    run('virt-xml-validate',str(ROOT/'network.xml'),'network')
    addr('g','192.168.55.20/24');run('ip','-n','g','route','add','default','via','192.168.55.1');ping('g','203.0.113.9')
    vm('rollback',str(b),'--apply')
    assert (ROOT/'network.xml').read_text()==original_xml
    addr('g','192.168.122.170/24');run('ip','-n','g','route','add','default','via','192.168.122.1');ping('g','203.0.113.9')
    # Fault injection: a failed libvirt network start rolls back the original XML.
    (ROOT/'fail-start-once').touch()
    assert vm(*lan,'--guest-dhcp-confirmed',ok=False).returncode!=0
    assert (ROOT/'network.xml').read_text()==original_xml
    ping('g','203.0.113.9')
    assert vm('set',VM,'--ip','10.123.0.20/24','--gateway','10.123.0.1','--dns','1.1.1.1','--guest-dhcp-confirmed','--apply',ok=False).returncode!=0
    set_state('running')
    vm('block',VM,'192.168.1.1','--apply');ping('g','192.168.1.1',False)
    run('nft','delete','table','bridge','vm_net_'+UUID.replace('-',''))
    ping('g','192.168.1.1');vm('restore-all');ping('g','192.168.1.1',False)
    run('ip','-6','addr','add','fd00::1/64','dev','virbr0','nodad')
    run('ip','-n','g','-6','addr','add','fd00::2/64','dev','eth0','nodad')
    ping('g','fd00::1')
    vm('block',VM,'fd00::1','--apply');ping('g','fd00::1',False)
    vm('unblock',VM,'fd00::1','--apply');ping('g','fd00::1')
    assert run('ip','-4','route','show','table','main').stdout==original_route
    assert vm('plan',ok=False).returncode!=0
    passed=True
    print('PASS: destination-only ICMP/TCP blocking; other VM/host isolation; same gateway forwarding; rollback; policy route; LAN XML; stopped/DHCP guards; invalid uplink/overlap; injected failure recovery; persistent restore; IPv6 blocking')
finally:
    if server is not None:server.terminate();server.wait(timeout=5)
    pathlib.Path(__file__).with_name('isolated-results.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    project=pathlib.Path(__file__).resolve().parent
    shutil.copytree('/var/lib/vm_net',project/'isolation-state',dirs_exist_ok=True)
    (project/'isolated-summary.json').write_text(json.dumps(dict(passed=passed,time_unix=time.time(),binary_sha256=hashlib.sha256(pathlib.Path(BIN).read_bytes()).hexdigest(),source_sha256=hashlib.sha256((project/'src/main.rs').read_bytes()).hexdigest(),recorded_commands=len(events)),indent=2))
