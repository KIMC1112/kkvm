# kkvm 搭建、重建与迁移

标准源码仓库：https://github.com/KIMC1112/kkvm.git

## 新搭建与重建

```sh
git clone https://github.com/KIMC1112/kkvm.git
cd kkvm
./main.sh
```

固定 CachyOS 7.0.12-1 与 QEMU 11.0.3 的构建流程继续使用 `setup-cachyos7012-acpi.sh`，详细步骤见 `profiles/cachyos-7.0.12-acpi/README.zh-CN.md`。编译安装路径统一为 `/opt/kkvm`；独立构建位于 `/opt/kkvm/builds`，构建清单名称为 `kkvm-build.json`。内核备份使用 `/var/lib/kkvm/kernel-7.0.12`。新生成 ACPI 源码的标记使用 `kkvm_host_acpi_identity`。

## 已有安装

本次改名针对源码与之后的新构建。现有虚拟机、磁盘、NVRAM、网络、显卡和 USB hooks 不会被自动迁移。不要仅为了改名运行新建虚拟机菜单，否则可能创建另一台虚拟机或另一套 NAT 网络。

1. 保留现有安装目录和完整构建记录；用 `virsh dumpxml <现有域名>` 备份 XML，记录当前 emulator、固件、NVRAM、磁盘和网络路径。
2. 从本仓库重新构建 kkvm。使用新的构建清单安装；旧清单中固化的安装前缀不会被新安装器接受，不要手改清单绕过检查。
3. 仅在计划停机时迁移：先验证新 QEMU 支持 `rtl8125`，再按实际部署调整 XML 中的 emulator、固件等路径。保留可恢复的旧路径和 XML；磁盘与 NVRAM 不随品牌改名而重新创建。
4. NAT 网络新名称为 `kkvm-Router`。如果旧 NAT 网络仍在使用同一子网，保留旧网络或在停机窗口统一迁移，避免同时创建冲突网络。本项目不自动删除旧网络。
5. 若显式指定 QEMU，改用 `KKVM_QEMU_EMULATOR`；桌面显示名称保持 `win10_start`，入口保持 `/usr/local/bin/win10_start`，核对动态显卡和 USB 切换脚本引用的实际域名。
6. 需要回退时恢复备份 XML 和启动脚本引用，使用原构建记录及原安装路径；内核回退使用原备份目录，不能假定备份已被搬到新目录。

仅变更名称时不启动、停止或重启虚拟机。本次源码发布没有执行 Linux 主机部署或虚拟机迁移。
