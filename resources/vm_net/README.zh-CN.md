# vm_net

kkvm 的 Linux/libvirt NAT 网络管理工具，支持 RTL8125。命令不自动启动或关闭虚拟机，也不更换网卡。

```sh
cargo test --locked
cargo build --release --locked
sudo bash test-isolated.sh
sudo bash install.sh
sudo vm_net status kkvm
```

将 `kkvm` 替换为实际 libvirt 虚拟机名称。`sudo vm_net` 或 `vm_net --help` 显示命令帮助，不是交互菜单。

```sh
sudo vm_net block kkvm 192.168.1.1         # 预览
sudo vm_net block kkvm 192.168.1.1 --apply # 应用
sudo vm_net unblock kkvm 192.168.1.1 --apply
sudo vm_net export kkvm /tmp/vm-net.json
sudo vm_net plan /tmp/vm-net.json
```

目的地址过滤匹配虚拟桥、虚拟机 MAC 和 IP；修改 LAN 地址需虚拟机关机、独占 NAT 网络且显式确认 Windows 使用 DHCP。出口策略不修改宿主机默认路由。具体参数见 --help；没有 --apply 时只显示计划。

安装位置：/usr/local/bin/vm_net。持久配置及审计：/var/lib/vm_net。两个 systemd 服务在启动时恢复已保存的过滤和路由；首次安装没有保存配置时不会创建过滤规则。服务不会自动推测要屏蔽的 IP。

install.sh 在 /var/lib/vm_net/install-时间戳 中备份已有命令和服务文件及 SHA256。已有安装恢复原备份；全新安装如需卸载，先确认没有保存的过滤/路由配置，再停用两个恢复服务、移除本次新增命令及服务文件并 daemon-reload；不要清空系统 nftables 或删除虚拟网络。

隔离测试在独立 mount/net namespace 中使用真实 nftables/iproute，virsh 使用 fixture；不连接生产 libvirt。源码包含 0.1.1 的关机状态查询修复，保留原命令与状态目录兼容性。
