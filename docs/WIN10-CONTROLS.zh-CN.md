# Windows 桌面启停

## 三个入口

| 桌面显示名称 | 命令 | 行为 |
|---|---|---|
| win10_start | /usr/local/bin/win10_start | 启动 Windows；保留已有动态显卡/USB 启动入口 |
| win10_stop | /usr/local/bin/win10_stop | 请求 Windows 正常关机，最多等待 150 秒 |
| win10_stop -f | /usr/local/bin/win10_stop -f | 强制关闭该 Windows 虚拟机，相当于断电 |

正常关机超时只报错，**不会自动强制断电**。等待正常关机期间，可主动使用另一个入口执行强制关闭。强制关闭可能丢失未保存的数据；不会删除磁盘、NVRAM 或虚拟机定义，也不会关闭 Linux 主机。

## 以后安装时创建

完成 main.sh 菜单 1 的依赖安装，再通过菜单 7 创建虚拟机，创建成功后自动安装三个桌面入口。菜单 8 可单独安装/更新入口，默认首次绑定 libvirt 的 kkvm 域；后续重装保留已有 UUID 绑定。桌面入口安装步骤本身不启停虚拟机。

已有其它域名时，使用普通 Linux 桌面用户执行：

~~~sh
python3 resources/win10/install.py --domain 实际域名
~~~

管理员代为安装时显式指定桌面用户：

~~~sh
sudo python3 resources/win10/install.py --user kk --domain 实际域名
~~~

依赖：Python 3.9+、libvirt/virsh、sudo、util-linux（runuser）、xdg-user-dirs、desktop-file-utils，以及可运行终端的 Linux 桌面。菜单 1 已包含这些软件包。安装器用 xdg-user-dir 查询目标用户的实际桌面路径，支持中文“桌面”目录；禁用 XDG 桌面时可使用 --desktop /home/kk/Desktop 显式指定。

三个 .desktop 文件均设置可执行权限并通过 desktop-file-validate。Terminal=true 允许需要时显示 sudo 密码提示；不新增宽泛免密权限。某些桌面首次运行仍需点“允许启动/信任”。旧 win10_start.desktop 会先备份，再用 win10_start-launcher.desktop 替代以避免重复图标。

## 动态显卡和 USB

如果 /usr/local/bin/win10_start 已存在，安装器验证它可执行并保持内容不变。显卡/USB 动态切换、设备选择及恢复仍使用原部署逻辑。

全新机器没有启动入口时，默认通过 virsh start 启动指定 UUID，使用已配置的 libvirt hooks 与 managed hostdev。若动态切换由专用 systemd 服务负责，应先部署该服务，再安装入口时指定它：

~~~sh
python3 resources/win10/install.py --domain kkvm --start-service kkvm-gpu-start.service
~~~

此参数仅用于没有现成 win10_start 的机器，服务必须已存在。入口安装器不会凭空生成 GPU/USB 切换服务，不会将动态切换改成永久 VFIO 绑定。关机通过 libvirt shutdown/destroy，设备归还由原有 hooks/服务完成；“Windows 已关闭”只表示域进入 shut off，不等于显卡/USB 已逐一验证归还。

配置存于 /etc/kkvm/win10.json，绑定 UUID，避免重命名域后误操作其它虚拟机。新控制入口的并发请求使用 /run/lock/kkvm-win10-control.lock；等待关机阶段释放该锁，让显式 -f 可以继续执行。原有启动脚本使用其自己的锁和设备策略。

## 备份与恢复

每次安装先备份将被修改的文件到 /var/lib/kkvm/launcher-backups/时间戳目录。before.json 记录原路径、是否原本存在、备份文件、SHA256、权限和属主；after.json 记录安装结果 SHA256。重复安装继续保留已有 win10_start，并生成新的备份记录。

恢复时先比对 after.json，确认文件未被后续修改；按 before.json 恢复原文件内容、权限和属主。existed=false 的条目是此次新建文件，确认仍与 after.json 匹配后才移除。只恢复本次安装涉及的文件，不删除虚拟机磁盘或现有 GPU/USB 服务。安装若中途失败，原件仍保存在 before.json 对应备份中，可据此恢复。

## 验证范围

自动测试覆盖正常关机超时不升级、强制关机、UUID 不匹配拒绝、运行中不重复启动、已关机无操作、保留动态启动入口、新安装和重复安装、三个桌面命令映射及备份恢复到额外副本。

本次开发在 Windows 环境执行模拟测试，没有连接或关闭真实 Windows 虚拟机；实际 Linux 图标信任状态和硬件归还需在目标部署时验证。
