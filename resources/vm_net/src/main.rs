use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, env, fs, io::Write, net::{IpAddr, Ipv4Addr}, path::{Path, PathBuf}, process::{Command, Stdio}, time::{SystemTime, UNIX_EPOCH}};
use xmltree::{Element, XMLNode};
#[cfg(unix)] use std::os::fd::AsRawFd;
type R<T> = Result<T, Box<dyn std::error::Error>>;
const BASE: &str = "/var/lib/vm_net";
const VIRSH: &str = "/usr/bin/virsh";
const NFT: &str = "/usr/bin/nft";
const IP: &str = "/usr/bin/ip";
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct Lan { address: String, gateway: Ipv4Addr, dns: Vec<Ipv4Addr> }
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct Uplink { gateway: Ipv4Addr, device: String }
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct Config {
    version: u32, vm: String, uuid: String, network: String, bridge: String, mac: String,
    blocked: BTreeSet<IpAddr>, lan: Option<Lan>, uplink: Option<Uplink>,
}
#[derive(Serialize, Deserialize)]
struct Snapshot { config: Option<Config>, network: String, network_live: Option<String>, nft: Option<String>, routes: Vec<Value>, rules: Vec<Value> }
fn fail<T>(s: impl Into<String>) -> R<T> { Err(s.into().into()) }
fn run(prog: &str, args: &[&str], input: Option<&str>) -> R<String> {
    let mut c = Command::new(prog); c.args(args).env("LC_ALL", "C").env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin");
    c.stdin(if input.is_some(){Stdio::piped()}else{Stdio::null()}).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut p = c.spawn()?;
    if let Some(s) = input { p.stdin.take().ok_or("stdin unavailable")?.write_all(s.as_bytes())?; }
    let out = p.wait_with_output()?;
    let record=json!({"time_unix_ns":now_id(),"program":prog,"args":args,"stdin":input,"exit_code":out.status.code(),"stdout":String::from_utf8_lossy(&out.stdout),"stderr":String::from_utf8_lossy(&out.stderr)});
    let mut audit=fs::OpenOptions::new().create(true).append(true).open(Path::new(BASE).join("audit.jsonl"))?;
    writeln!(audit,"{}",record)?;
    if !out.status.success() { return fail(format!("{} {:?}: {}\n{}", prog,args,out.status,String::from_utf8_lossy(&out.stderr))); }
    Ok(String::from_utf8(out.stdout)?)
}
fn vir(args: &[&str]) -> R<String> { let mut a=vec!["-c","qemu:///system"]; a.extend_from_slice(args);run(VIRSH,&a,None) }
fn ip(args: &[&str]) -> R<String> { run(IP,args,None) }
fn xml(s: &str) -> R<Element> { Ok(Element::parse(s.as_bytes())?) }
fn xml_string(e: &Element) -> R<String> { let mut b=vec![];e.write_with_config(&mut b,xmltree::EmitterConfig::new().perform_indent(true))?;Ok(String::from_utf8(b)?) }
fn attr<'a>(e: &'a Element, key: &str) -> R<&'a str> { e.attributes.get(key).map(|s|s.as_str()).ok_or_else(||format!("Missing {} on {}",key,e.name).into()) }
fn child<'a>(e: &'a Element, key: &str) -> R<&'a Element> { e.get_child(key).ok_or_else(||format!("Missing {}",key).into()) }
fn elements(e: &Element) -> impl Iterator<Item=&Element> { e.children.iter().filter_map(|n|n.as_element()) }
fn identifier(s: &str) -> bool { !s.is_empty() && s.len()<=80 && s.bytes().all(|c|c.is_ascii_alphanumeric() || b"_.-".contains(&c)) && !s.starts_with('-') }
fn mac_valid(s: &str) -> bool { let v:Vec<_>=s.split(':').collect();v.len()==6 && v.iter().all(|x|x.len()==2 && x.bytes().all(|b|b.is_ascii_hexdigit())) }
fn cidr(s: &str) -> R<(Ipv4Addr,u8,u32)> {
    let (a,p)=s.split_once('/').ok_or("IP needs prefix, e.g. 192.168.122.170/24")?;
    let a:Ipv4Addr=a.parse()?;let p:u8=p.parse()?;
    if !(1..=30).contains(&p) {return fail("IPv4 prefix must be 1..30");}
    let mask=u32::MAX << (32-p);let n=u32::from(a)&mask;
    if u32::from(a)==n || u32::from(a)==n|!mask || !unicast(a) {return fail("Invalid host address");}
    Ok((a,p,n))
}
fn unicast(a: Ipv4Addr) -> bool { !a.is_unspecified() && !a.is_loopback() && !a.is_multicast() && !a.is_broadcast() && a.octets()[0]<224 && a.octets()[0]!=0 }
fn validate(c: &Config) -> R<()> {
    if c.version!=1 || !identifier(&c.vm) || !identifier(&c.network) || !identifier(&c.bridge) || c.bridge.len()>15 || !mac_valid(&c.mac) {return fail("Invalid configuration identity");}
    if c.uuid.len()!=36 || c.uuid.bytes().enumerate().any(|(i,b)| if [8,13,18,23].contains(&i){b!=b'-'}else{!b.is_ascii_hexdigit()}) {return fail("Invalid UUID");}
    if let Some(l)=&c.lan {
        let (a,p,n)=cidr(&l.address)?;let g=u32::from(l.gateway);let mask=u32::MAX << (32-p);
        if !unicast(l.gateway) || g&mask!=n || g==n || g==n|!mask || l.gateway==a {return fail("Gateway must be a different usable address in the guest subnet");}
        if l.dns.is_empty() || l.dns.iter().any(|a|!unicast(*a)) {return fail("Specify at least one unicast DNS server");}
    }
    if let Some(u)=&c.uplink {if !unicast(u.gateway) || !identifier(&u.device) || u.device.len()>15 {return fail("Invalid uplink");}}
    if c.blocked.iter().any(|a|match a {IpAddr::V4(a)=>!unicast(*a),IpAddr::V6(a)=>a.is_unspecified()||a.is_multicast()||a.is_loopback()}) {return fail("Blocked destinations must be unicast IPs");}
    Ok(())
}
fn key(c: &Config) -> String { c.uuid.replace('-',"") }
fn table(c: &Config) -> String {format!("vm_net_{}",key(c))}
fn route_table(c: &Config) -> u32 {let h=Sha256::digest(c.uuid.as_bytes());22000+(u16::from_be_bytes([h[0],h[1]]) as u32 % 8000)}
fn config_path(c: &Config) -> PathBuf {Path::new(BASE).join(format!("{}.json",key(c)))}
fn load_current(c: &Config) -> R<Option<Config>> {let p=config_path(c);if p.exists(){Ok(Some(serde_json::from_slice(&fs::read(p)?)?))}else{Ok(None)}}
fn atomic(path: &Path, s: &str) -> R<()> {let t=path.with_extension("tmp");fs::write(&t,s)?;fs::File::open(&t)?.sync_all()?;fs::rename(t,path)?;Ok(())}
fn discover(vm: &str) -> R<Config> {
    if !identifier(vm) {return fail("Invalid VM name");}
    let d=xml(&vir(&["dumpxml",vm,"--inactive"])?)?;
    let nics:Vec<_>=elements(child(&d,"devices")?).filter(|e|e.name=="interface").collect();
    if nics.len()!=1 || attr(nics[0],"type")?!="network" {return fail("Requires exactly one libvirt NAT network interface; passthrough/multiple NICs are not supported");}
    let nic=nics[0];let network=attr(child(nic,"source")?,"network")?.to_string();
    let n=xml(&vir(&["net-dumpxml",&network,"--inactive"])?)?;
    if attr(child(&n,"forward")?,"mode")?!="nat" {return fail("Only libvirt NAT networks are supported");}
    let c=Config{version:1,vm:vm.into(),uuid:child(&d,"uuid")?.get_text().ok_or("Missing UUID text")?.into_owned(),network,bridge:attr(child(&n,"bridge")?,"name")?.into(),mac:attr(child(nic,"mac")?,"address")?.to_lowercase(),blocked:BTreeSet::new(),lan:None,uplink:None};
    validate(&c)?;Ok(c)
}
fn same_identity(a: &Config,b: &Config) -> bool {a.uuid==b.uuid && a.vm==b.vm && a.network==b.network && a.bridge==b.bridge && a.mac==b.mac}
fn rules(c: &Config, existing: bool) -> String {
    let t=table(c);let mut s=String::new();if existing{s+=&format!("delete table bridge {t}\n");}
    s+=&format!("table bridge {t} {{\n comment \"vm_net:{}\"\n chain filter {{\n type filter hook prerouting priority -300; policy accept;\n",c.uuid);
    for a in &c.blocked {s+=&format!("  meta ibrname \"{}\" ether saddr {} {} daddr {} counter drop\n",c.bridge,c.mac,if a.is_ipv4(){"ip"}else{"ip6"},a);}
    s+" }\n}\n"
}
fn nft_current(c: &Config) -> R<Option<String>> {
    let list:Value=serde_json::from_str(&run(NFT,&["-j","list","tables"],None)?)?;
    let exists=list["nftables"].as_array().ok_or("Invalid nft JSON")?.iter().any(|v|v["table"]["family"]=="bridge" && v["table"]["name"]==table(c));
    if !exists{return Ok(None);}
    let s=run(NFT,&["list","table","bridge",&table(c)],None)?;
    if !s.contains(&format!("comment \"vm_net:{}\"",c.uuid)){return fail("Refusing to overwrite foreign nft table");}Ok(Some(s))
}
fn network_active(name: &str) -> R<bool> {Ok(vir(&["net-list","--name"])?.lines().any(|s|s==name))}
fn ensure_exclusive(c: &Config) -> R<()> {
    for name in vir(&["list","--all","--name"])?.lines().filter(|s|!s.is_empty() && *s!=c.vm) {
        for inactive in [true,false] {
            let d=if inactive {vir(&["dumpxml",name,"--inactive"])?}else{vir(&["dumpxml",name])?};
            let e=xml(&d)?;
            for nic in elements(child(&e,"devices")?).filter(|e|e.name=="interface") {
                if nic.get_child("source").and_then(|s|s.attributes.get("network"))==Some(&c.network) {return fail(format!("Network {} is also used by {}; refusing shared-network changes",c.network,name));}
            }
        }
    }Ok(())
}
fn el(name: &str, attrs: &[(&str,String)]) -> Element {let mut e=Element::new(name);for(k,v)in attrs{e.attributes.insert((*k).into(),v.clone());}e}
fn network_xml(original: &str,c: &Config) -> R<String> {
    let Some(l)=&c.lan else{return Ok(original.into())};
    let mut n=xml(original)?;
    if elements(&n).filter(|e|e.name=="ip").count()!=1 {return fail("LAN editing requires a single IPv4 network; multi-subnet/IPv6 network not modified");}
    let old=child(&n,"ip")?;
    if old.attributes.get("family").is_some_and(|f|f!="ipv4") {return fail("Expected IPv4 network");}
    if n.attributes.keys().any(|k|k.contains("dnsmasq")) || elements(&n).any(|e|e.name=="options") {return fail("Custom dnsmasq options require manual review");}
    if let Some(d)=old.get_child("dhcp") {
        for h in elements(d).filter(|e|e.name=="host") {if h.attributes.get("mac").map(|s|s.to_lowercase())!=Some(c.mac.clone()){return fail("Other DHCP reservations present; refusing to replace them");}}
        if elements(d).any(|e|(e.name!="host" && e.name!="range") || elements(e).next().is_some()) {return fail("Custom DHCP/lease settings present");}
    }
    if elements(old).any(|e|e.name!="dhcp") {return fail("Custom IP/TFTP settings present");}
    let (a,p,_)=cidr(&l.address)?;
    let mut i=old.clone();i.attributes.insert("address".into(),l.gateway.to_string());i.attributes.insert("prefix".into(),p.to_string());i.attributes.remove("netmask");i.children.clear();
    let mut d=Element::new("dhcp");
    d.children.push(XMLNode::Element(el("range",&[("start",a.to_string()),("end",a.to_string())])));
    d.children.push(XMLNode::Element(el("host",&[("mac",c.mac.clone()),("name",c.vm.clone()),("ip",a.to_string())])));
    i.children.push(XMLNode::Element(d));
    let mut dns=n.get_child("dns").cloned().unwrap_or_else(||Element::new("dns"));
    if dns.attributes.get("enable").is_some_and(|v|v=="no"){return fail("Network DNS is disabled; enable it explicitly before setting DNS forwarders");}
    dns.children.retain(|x|x.as_element().is_none_or(|e|e.name!="forwarder"));
    n.children.retain(|x|x.as_element().is_none_or(|e|e.name!="ip" && e.name!="dns"));
    for a in &l.dns{dns.children.push(XMLNode::Element(el("forwarder",&[("addr",a.to_string())])));}
    n.children.push(XMLNode::Element(dns));n.children.push(XMLNode::Element(i));
    n.attributes.remove("connections");xml_string(&n)
}
fn guest_subnet(network: &str) -> R<(String,String)> {
    let e=xml(network)?;let i=child(&e,"ip")?;let a:Ipv4Addr=attr(i,"address")?.parse()?;
    let p:u8=if let Some(p)=i.attributes.get("prefix"){p.parse()?}else{let m:u32=u32::from(attr(i,"netmask")?.parse::<Ipv4Addr>()?);let p=m.count_ones() as u8;if p==0 || m!=u32::MAX << (32-p){return fail("Noncontiguous netmask");}p};
    let (_,_,n)=cidr(&format!("{a}/{p}"))?;Ok((format!("{}/{p}",Ipv4Addr::from(n)),a.to_string()))
}
fn uplink_subnet(u: &Uplink) -> R<String> {
    let v:Value=serde_json::from_str(&ip(&["-j","-4","addr","show","dev",&u.device])?)?;
    for a in v.as_array().ok_or("Invalid ip JSON")?.iter().flat_map(|v|v["addr_info"].as_array().into_iter().flatten()) {
        let Some(local)=a["local"].as_str()else{continue};let p=a["prefixlen"].as_u64().ok_or("Missing prefix")?;
        if p==0 || p>30{continue;}let (_,_,n)=cidr(&format!("{local}/{p}"))?;let mask=u32::MAX << (32-p);
        let g=u32::from(u.gateway);
        if g&mask==n && g!=n && g!=n|!mask && local!=u.gateway.to_string() {return Ok(format!("{}/{p}",Ipv4Addr::from(n)));}
    }fail("External gateway must be directly connected to the selected host interface; it cannot be an arbitrary unreachable address")
}
fn routing_snapshot(c: &Config) -> R<(Vec<Value>,Vec<Value>)> {
    let t=route_table(c);let all:Vec<Value>=serde_json::from_str(&ip(&["-j","-N","-4","route","show","table","all"])?)?;
    let routes=all.into_iter().filter(|v|v["table"].as_u64()==Some(t as u64)||v["table"]==t.to_string()).collect();
    let all:Vec<Value>=serde_json::from_str(&ip(&["-j","-4","rule","show"])?)?;
    let rules=all.into_iter().filter(|v|v["priority"].as_u64()==Some(t as u64)||v["table"].as_u64()==Some(t as u64)||v["table"]==t.to_string()).collect();Ok((routes,rules))
}
fn ensure_route_ownership(c: &Config,old: Option<&Config>,routes: &[Value],rules: &[Value]) -> R<()> {
    let owned=old.is_some_and(|o|o.uplink.is_some());
    if (!routes.is_empty() || !rules.is_empty()) && !owned {return fail("Policy routing table/priority collision; refusing to overwrite foreign routes");}
    for r in rules {if r["iif"]!=c.bridge || r["priority"].as_u64()!=Some(route_table(c) as u64) || !(r["table"].as_u64()==Some(route_table(c) as u64)||r["table"]==route_table(c).to_string()) || r["src"]!="all" {return fail("Foreign policy rule in reserved slot");}}
    if rules.len()>1 {return fail("Duplicate policy rules");}
    for r in routes {if r["protocol"]!=242 && r["protocol"]!="242" {return fail("Foreign route in reserved table");}}Ok(())
}
fn route_clear(c: &Config) -> R<()> {
    let (routes,rules)=routing_snapshot(c)?;let t=route_table(c).to_string();
    if !rules.is_empty(){ip(&["-4","rule","del","priority",&t,"iif",&c.bridge,"table",&t])?;}
    if !routes.is_empty(){ip(&["-4","route","flush","table",&t])?;}Ok(())
}
fn route_apply(c: &Config,network: &str) -> R<()> {
    route_clear(c)?;
    if let Some(u)=&c.uplink {
        let t=route_table(c).to_string();let up=uplink_subnet(u)?;let (guest,_)=guest_subnet(network)?;
        ip(&["-4","route","add","table",&t,&up,"dev",&u.device,"scope","link","proto","242"])?;
        if guest!=up {ip(&["-4","route","add","table",&t,&guest,"dev",&c.bridge,"scope","link","proto","242"])?;}
        ip(&["-4","route","add","table",&t,"default","via",&u.gateway.to_string(),"dev",&u.device,"proto","242"])?;
        ip(&["-4","rule","add","priority",&t,"iif",&c.bridge,"table",&t])?;
    }Ok(())
}
fn restore_routes(c: &Config,s: &Snapshot) -> R<()> {
    route_clear(c)?;let t=route_table(c).to_string();
    for r in &s.routes {
        let dest=r["dst"].as_str().unwrap_or("default");let dev=r["dev"].as_str().ok_or("Snapshot route missing dev")?;
        let mut a=vec!["-4","route","add","table",&t,dest,"dev",dev,"proto","242"];
        if let Some(g)=r["gateway"].as_str(){a.extend(["via",g]);}else{a.extend(["scope","link"]);}ip(&a)?;
    }
    if !s.rules.is_empty(){ip(&["-4","rule","add","priority",&t,"iif",&c.bridge,"table",&t])?;}Ok(())
}
fn restore_nft(c: &Config,old: Option<&str>) -> R<()> {
    let now=nft_current(c)?;let mut s=String::new();if now.is_some(){s+=&format!("delete table bridge {}\n",table(c));}if let Some(old)=old{s+=old;}
    if !s.is_empty(){run(NFT,&["-f","-"],Some(&s))?;}Ok(())
}
fn now_id() -> String {SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos().to_string()}
fn backup(c: &Config,s: &Snapshot) -> R<PathBuf> {
    let p=Path::new(BASE).join("backups").join(format!("{}-{}",key(c),now_id()));fs::create_dir_all(&p)?;
    fs::write(p.join("snapshot.json"),serde_json::to_vec_pretty(s)?)?;
    fs::write(p.join("identity.json"),serde_json::to_vec_pretty(c)?)?;
    fs::write(p.join("network.before.xml"),&s.network)?;
    fs::write(p.join("original.sha256"),format!("{:x}  network.before.xml\n{:x}  snapshot.json\n",Sha256::digest(s.network.as_bytes()),Sha256::digest(fs::read(p.join("snapshot.json"))?)))?;Ok(p)
}
fn network_replace(c: &Config,content: &str,active: bool,dir: &Path) -> R<()> {
    let p=dir.join("network.target.xml");fs::write(&p,content)?;
    if network_active(&c.network)? {vir(&["net-destroy",&c.network])?;}
    vir(&["net-define",p.to_str().ok_or("Invalid path")?,"--validate"])?;
    if active {vir(&["net-start",&c.network])?;}Ok(())
}
fn stopped(c: &Config) -> R<()> {if vir(&["domstate",&c.vm])?.trim()!="shut off" {return fail("LAN/gateway editing requires the VM to be shut off. vm_net NEVER shuts down or restarts the VM. Shut down Windows yourself, then retry with --guest-dhcp-confirmed.");}Ok(())}
fn prevent_overlap(c: &Config) -> R<()> {
    let Some(l)=&c.lan else{return Ok(())};let(_,p,n)=cidr(&l.address)?;let end=n|!(u32::MAX << (32-p));
    let a:Value=serde_json::from_str(&ip(&["-j","-4","addr","show"])?)?;
    for dev in a.as_array().ok_or("Invalid address JSON")? {if dev["ifname"]==c.bridge{continue;}for v in dev["addr_info"].as_array().into_iter().flatten(){
        let Some(local)=v["local"].as_str()else{continue};let Some(p2)=v["prefixlen"].as_u64()else{continue};if p2==0||p2>32{continue;}
        let n2=u32::from(local.parse::<Ipv4Addr>()?) & (u32::MAX << (32-p2));let end2=n2|!(u32::MAX << (32-p2));
        if n<=end2 && n2<=end{return fail(format!("Guest subnet overlaps host interface {}",dev["ifname"]));}
    }}Ok(())
}
fn apply(c: &Config,execute: bool,dhcp: bool) -> R<()> {
    validate(c)?;let actual=discover(&c.vm)?;if !same_identity(c,&actual){return fail("VM identity/network changed; refresh config first");}
    let live_domain=xml(&vir(&["dumpxml",&c.vm])?)?;
    let live_nics:Vec<_>=elements(child(&live_domain,"devices")?).filter(|e|e.name=="interface").collect();
    if live_nics.len()!=1 || attr(child(live_nics[0],"mac")?,"address")?.to_lowercase()!=c.mac || attr(child(live_nics[0],"source")?,"network")?!=c.network {return fail("Live NIC differs from persistent NIC; resolve it before applying rules");}
    let old=load_current(c)?;let original=vir(&["net-dumpxml",&c.network,"--inactive"])?;
    let live=if network_active(&c.network)?{Some(vir(&["net-dumpxml",&c.network])?)}else{None};
    let lan_change=c.lan.is_some() && old.as_ref().and_then(|x|x.lan.as_ref())!=c.lan.as_ref();
    if old.as_ref().is_some_and(|o|o.lan.is_some()) && c.lan.is_none(){return fail("Use rollback to undo LAN changes; clearing lan is not a restore");}
    let routing_change=old.as_ref().and_then(|o|o.uplink.as_ref())!=c.uplink.as_ref() || (lan_change && c.uplink.is_some());
    if lan_change || c.uplink.is_some(){ensure_exclusive(c)?;}
    if lan_change {prevent_overlap(c)?;}
    let target=if lan_change{network_xml(&original,c)?}else{original.clone()};
    if let Some(u)=&c.uplink{uplink_subnet(u)?;let n=xml(&target)?;let f=child(&n,"forward")?;if f.attributes.get("dev").is_some_and(|d|d!=&u.device)||elements(f).any(|e|e.name=="interface" && e.attributes.get("dev")!=Some(&u.device)){return fail("Existing libvirt forwarding is restricted to a different uplink interface");}}
    let nft=nft_current(c)?;let nft_script=rules(c,nft.is_some());
    run(NFT,&["-c","-f","-"],Some(&nft_script))?;
    let (routes,rules_old)=routing_snapshot(c)?;
    ensure_route_ownership(c,old.as_ref(),&routes,&rules_old)?;
    println!("{}",serde_json::to_string_pretty(&json!({"config":c,"lan_change":lan_change,"routing_change":routing_change,"route_table":route_table(c),"restart_network_required":lan_change,"nft":nft_script,"apply":execute}))?);
    if !execute {return Ok(());}
    if lan_change {if !dhcp{return fail("Pass --guest-dhcp-confirmed only after verifying Windows uses DHCP; static Windows addressing is NOT changed by libvirt DHCP");}stopped(c)?;}
    if c.uplink.is_some() && !lan_change && live.is_none(){return fail("Start the libvirt network before selecting an uplink");}
    let s=Snapshot{config:old,network:original,network_live:live,nft,routes,rules:rules_old};let dir=backup(c,&s)?;
    println!("Backup: {}",dir.display());
    let result=(||->R<()> {
        if lan_change{network_replace(c,&target,s.network_live.is_some(),&dir)?;}
        if routing_change{route_apply(c,&target)?;}
        run(NFT,&["-f","-"],Some(&nft_script))?;
        atomic(&config_path(c),&serde_json::to_string_pretty(c)?)?;
        fs::write(dir.join("applied.json"),serde_json::to_vec_pretty(c)?)?;Ok(())
    })();
    if let Err(e)=result {
        let rollback=(||->R<()>{if lan_change{network_replace(c,s.network_live.as_deref().unwrap_or(&s.network),s.network_live.is_some(),&dir)?;let p=dir.join("network.before.xml");vir(&["net-define",p.to_str().unwrap(),"--validate"])?;}if routing_change{restore_routes(c,&s)?;}restore_nft(c,s.nft.as_deref())?;if let Some(o)=&s.config{atomic(&config_path(c),&serde_json::to_string_pretty(o)?)?;}else if config_path(c).exists(){fs::remove_file(config_path(c))?;}Ok(())})();
        return fail(format!("Apply failed: {e}; automatic rollback: {rollback:?}; backup {}",dir.display()));
    }
    println!("Applied. No VM power operation or host default-route change performed.");Ok(())
}
fn rollback(path: &Path,execute: bool) -> R<()> {
    let c:Config=serde_json::from_slice(&fs::read(path.join("identity.json"))?)?;validate(&c)?;
    let s:Snapshot=serde_json::from_slice(&fs::read(path.join("snapshot.json"))?)?;
    let applied:Config=serde_json::from_slice(&fs::read(path.join("applied.json"))?)?;
    if load_current(&c)?.as_ref()!=Some(&applied){return fail("Current config differs from this transaction; rollback the latest transaction first");}
    if !same_identity(&c,&discover(&c.vm)?){return fail("Identity changed");}
    let lan_change=s.config.as_ref().and_then(|o|o.lan.as_ref())!=applied.lan.as_ref();
    let route_change=s.config.as_ref().and_then(|o|o.uplink.as_ref())!=applied.uplink.as_ref() || (lan_change && applied.uplink.is_some());
    println!("Restore {} (LAN restart: {})",path.display(),lan_change);if !execute{return Ok(());}
    if lan_change{ensure_exclusive(&c)?;stopped(&c)?;}
    let (r,ru)=routing_snapshot(&c)?;ensure_route_ownership(&c,Some(&applied),&r,&ru)?;
    if lan_change{network_replace(&c,s.network_live.as_deref().unwrap_or(&s.network),s.network_live.is_some(),path)?;vir(&["net-define",path.join("network.before.xml").to_str().unwrap(),"--validate"])?;}
    if route_change{restore_routes(&c,&s)?;}
    restore_nft(&c,s.nft.as_deref())?;
    if let Some(o)=&s.config{atomic(&config_path(&c),&serde_json::to_string_pretty(o)?)?;}else{fs::remove_file(config_path(&c))?;}
    fs::write(path.join("rolled-back"),now_id())?;println!("Restored");Ok(())
}
fn restore_all(filters_only: bool) -> R<()> {
    let mut errors=vec![];
    for entry in fs::read_dir(BASE)? {let p=entry?.path();if p.extension().and_then(|s|s.to_str())!=Some("json"){continue;}
        let result=(||->R<()>{let c:Config=serde_json::from_slice(&fs::read(&p)?)?;validate(&c)?;
            // Destination blocking comes first, even if the bridge/VM is not up yet.
            let n=nft_current(&c)?;run(NFT,&["-f","-"],Some(&rules(&c,n.is_some())))?;
            if !filters_only && c.uplink.is_some(){let actual=discover(&c.vm)?;if !same_identity(&c,&actual){return fail("VM identity changed");}ensure_exclusive(&c)?;let(r,ru)=routing_snapshot(&c)?;ensure_route_ownership(&c,Some(&c),&r,&ru)?;let network=vir(&["net-dumpxml",&c.network])?;route_apply(&c,&network)?;}Ok(())})();
        if let Err(e)=result{errors.push(format!("{}: {e}",p.display()));}
    }if errors.is_empty(){Ok(())}else{fail(errors.join("\n"))}
}
fn status(vm: &str) -> R<()> {
    let c=discover(vm)?;println!("{}",serde_json::to_string_pretty(&json!({"detected":c,"saved":load_current(&c)?}))?);
    for args in [vec!["domstate",vm],vec!["domiflist",vm],vec!["net-dumpxml",&c.network]] {println!("{}",vir(&args)?);}
    if vir(&["domstate",vm])?.trim()=="running" {println!("{}",vir(&["domifaddr",vm])?);} else {println!("Guest is stopped; live IP query skipped");}
    println!("{}",ip(&["-4","route","show"])?);println!("{}",nft_current(&c)?.unwrap_or_else(||"No vm_net filter installed".into()));Ok(())
}
fn help() {println!("vm_net 0.1.1 (kkvm) — libvirt NAT / RTL8125 compatible\n\n  vm_net status VM\n  vm_net export VM FILE.json\n  vm_net block VM IP [--apply]\n  vm_net unblock VM IP [--apply]\n  vm_net set VM [--ip IP/PREFIX --gateway IP --dns IP,IP]\n      [--uplink-gateway IP --uplink-dev IFACE | --uplink-default]\n      [--guest-dhcp-confirmed] [--apply]\n  vm_net plan FILE.json\n  vm_net apply FILE.json [--guest-dhcp-confirmed] --apply\n  vm_net rollback BACKUP_DIRECTORY [--apply]\n  vm_net render-rules FILE.json\n  vm_net restore-all\n\nWithout --apply, mutations only display a plan. LAN changes require the VM\nto be shut off and Windows DHCP explicitly confirmed. External gateways\nuse an exclusive VM network policy route; host default route is untouched.\nBlocking an IP also blocks DNS/HTTP on that IP, but not ARP or forwarding\nto OTHER destinations through that router. Direct IPv4/IPv6 only; proxies\nand alternate addresses are outside this rule. Root required except help/render.");}
fn main_inner() -> R<()> {
    let a:Vec<String>=env::args().skip(1).collect();if a.is_empty()||a[0]=="--help"||a[0]=="help"{help();return Ok(());}
    if a[0]=="render-rules" {if a.len()!=2{return fail("render-rules requires FILE.json");}let c:Config=serde_json::from_slice(&fs::read(&a[1])?)?;validate(&c)?;print!("{}",rules(&c,false));return Ok(());}
    #[cfg(not(unix))] {return fail("Host operations require Linux");}
    #[cfg(unix)] {
        if unsafe{libc::geteuid()}!=0 {return fail("Run with sudo");}unsafe{libc::umask(0o077);}
        fs::create_dir_all(BASE)?;let lock=fs::OpenOptions::new().create(true).truncate(false).write(true).open(Path::new(BASE).join("lock"))?;
        if unsafe{libc::flock(lock.as_raw_fd(),libc::LOCK_EX|libc::LOCK_NB)}!=0{return fail("Another vm_net transaction is running");}
        let execute=a.iter().any(|s|s=="--apply");let dhcp=a.iter().any(|s|s=="--guest-dhcp-confirmed");
        let arg=|i:usize|a.get(i).map(|s|s.as_str()).ok_or("Missing argument");
        match a[0].as_str() {
            "status"=>{if a.len()!=2{return fail("status VM");}status(arg(1)?)?;},
            "export"=>{if a.len()!=3{return fail("export VM FILE.json");}let c=discover(arg(1)?)?;let c=load_current(&c)?.unwrap_or(c);fs::write(arg(2)?,serde_json::to_vec_pretty(&c)?)?;},
            "block"|"unblock"=>{if a.len()<3 || a[3..].iter().any(|s|s!="--apply"){return fail("block/unblock VM IP [--apply]");}let c=discover(arg(1)?)?;let mut c=load_current(&c)?.unwrap_or(c);let address:IpAddr=arg(2)?.parse()?;if a[0]=="block"{c.blocked.insert(address);}else{c.blocked.remove(&address);}apply(&c,execute,false)?;},
            "set"=>{let c=discover(arg(1)?)?;let mut c=load_current(&c)?.unwrap_or(c);let mut address=None;let mut gw=None;let mut dns=None;let mut ug=None;let mut ud=None;let mut default=false;let mut i=2;
                while i<a.len(){match a[i].as_str(){"--apply"|"--guest-dhcp-confirmed"=>{},"--uplink-default"=>default=true,"--ip"=>{i+=1;address=Some(arg(i)?.to_string());},"--gateway"=>{i+=1;gw=Some(arg(i)?.parse()?);},"--dns"=>{i+=1;dns=Some(arg(i)?.split(',').map(str::parse).collect::<Result<Vec<Ipv4Addr>,_>>()?);},"--uplink-gateway"=>{i+=1;ug=Some(arg(i)?.parse()?);},"--uplink-dev"=>{i+=1;ud=Some(arg(i)?.to_string());},_=>return fail(format!("Unknown option {}",a[i]))}i+=1;}
                if address.is_some()||gw.is_some()||dns.is_some(){let old=c.lan.clone();c.lan=Some(Lan{address:address.or_else(||old.as_ref().map(|o|o.address.clone())).ok_or("Specify --ip, --gateway and --dns together the first time")?,gateway:gw.or_else(||old.as_ref().map(|o|o.gateway)).ok_or("Missing --gateway")?,dns:dns.or_else(||old.map(|o|o.dns)).ok_or("Missing --dns")?});}
                if default && (ug.is_some()||ud.is_some()){return fail("Conflicting uplink options");}
                if default{c.uplink=None;}else if ug.is_some()||ud.is_some(){c.uplink=Some(Uplink{gateway:ug.ok_or("Missing --uplink-gateway")?,device:ud.ok_or("Missing --uplink-dev")?});}apply(&c,execute,dhcp)?;
            },
            "plan"|"apply"=>{if a.len()<2{return fail("Missing config file");}if a[2..].iter().any(|s|s!="--apply"&&s!="--guest-dhcp-confirmed"){return fail("Unknown option");}let c:Config=serde_json::from_slice(&fs::read(arg(1)?)?)?;apply(&c,a[0]=="apply"&&execute,dhcp)?;},
            "rollback"=>{if a.len()<2||a[2..].iter().any(|s|s!="--apply"){return fail("rollback DIR [--apply]");}rollback(Path::new(arg(1)?),execute)?;},
            "restore-all"|"restore-filters"=>{if a.len()!=1{return fail("restore commands take no arguments");}restore_all(a[0]=="restore-filters")?;},
            _=>return fail("Unknown command; use --help"),
        }
    }Ok(())
}
fn main(){if let Err(e)=main_inner(){eprintln!("vm_net: {e}");std::process::exit(1);}}

#[cfg(test)] mod tests {
    use super::*;
    fn c()->Config{Config{version:1,vm:"kkvm".into(),uuid:"0e3faeae-4e8a-42ac-a4b2-f6356eb41ea5".into(),network:"default".into(),bridge:"virbr0".into(),mac:"02:b0:ec:83:2b:a4".into(),blocked:BTreeSet::new(),lan:None,uplink:None}}
    #[test] fn cidr_validation(){assert!(cidr("192.168.1.0/24").is_err());assert!(cidr("192.168.1.255/24").is_err());assert!(cidr("192.168.1.1/33").is_err());assert!(cidr("127.0.0.1/8").is_err());assert_eq!(cidr("192.168.1.20/24").unwrap().2,u32::from(Ipv4Addr::new(192,168,1,0)));}
    #[test] fn rule_is_destination_only(){let mut c=c();c.blocked.insert("192.168.1.1".parse().unwrap());let s=rules(&c,false);assert!(s.contains("ip daddr 192.168.1.1 counter drop"));assert!(s.contains("ether saddr 02:b0:ec:83:2b:a4"));assert!(!s.contains("ether daddr"));assert!(!s.contains("arp"));assert!(s.contains("policy accept"));}
    #[test] fn ipv6_rule(){let mut c=c();c.blocked.insert("fd00::1".parse().unwrap());assert!(rules(&c,false).contains("ip6 daddr fd00::1"));}
    #[test] fn input_injection_rejected(){let mut x=c();x.bridge="x\"; flush ruleset".into();assert!(validate(&x).is_err());let mut x=c();x.mac="xx:11:22:33:44:55".into();assert!(validate(&x).is_err());}
    #[test] fn lan_validation(){let mut c=c();c.lan=Some(Lan{address:"192.168.55.5/24".into(),gateway:"192.168.55.1".parse().unwrap(),dns:vec!["1.1.1.1".parse().unwrap()]});assert!(validate(&c).is_ok());c.lan.as_mut().unwrap().gateway="192.168.1.1".parse().unwrap();assert!(validate(&c).is_err());}
    #[test] fn preserve_network_and_reserve_ip(){let mut c=c();c.lan=Some(Lan{address:"192.168.55.5/24".into(),gateway:"192.168.55.1".parse().unwrap(),dns:vec!["1.1.1.1".parse().unwrap()]});let s=network_xml("<network><name>default</name><uuid>example</uuid><forward mode='nat'/><bridge name='virbr0'/><ip address='192.168.122.1' netmask='255.255.255.0'><dhcp><range start='192.168.122.2' end='192.168.122.254'/></dhcp></ip></network>",&c).unwrap();let n=xml(&s).unwrap();assert_eq!(child(&n,"uuid").unwrap().get_text().unwrap(),"example");assert_eq!(attr(child(&n,"ip").unwrap(),"address").unwrap(),"192.168.55.1");let d=child(child(&n,"ip").unwrap(),"dhcp").unwrap();assert_eq!(attr(child(d,"host").unwrap(),"ip").unwrap(),"192.168.55.5");assert_eq!(guest_subnet(&s).unwrap().0,"192.168.55.0/24");}
    #[test] fn no_lan_change_means_exact_xml(){let s="<network> unchanged </network>";assert_eq!(network_xml(s,&c()).unwrap(),s);}
    #[test] fn table_deterministic(){assert_eq!(route_table(&c()),route_table(&c()));assert!((22000..30000).contains(&route_table(&c())));}
}
