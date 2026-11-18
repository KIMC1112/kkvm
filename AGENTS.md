# kkvm 工作约定

- 标准源码仓库：`https://github.com/KIMC1112/kkvm.git`。今后的搭建、重建、修复和修改均以此仓库为准。
- 项目显示名称和运行时命名空间使用 `kkvm`，环境变量使用 `KKVM_` 前缀。
- 新安装使用 `/opt/kkvm`，状态与备份使用 `/var/lib/kkvm`；不要在改名时自动迁移现有机器。
- Windows 10 桌面快捷方式显示名称固定为 `win10_start`；启动入口固定为 `/usr/local/bin/win10_start`，沿用动态显卡和 USB 切换方案。
- Windows 网卡使用 RTL8125 2.5GbE 模拟设备，XML 使用 `<model type='rtl8125'/>`；可使用共享 NAT 保留 Linux 网络，不改为 VirtIO，也不要求物理网卡直通。
- 仅修改名称时不要启动、关闭或重启虚拟机。
- 固定内核和 ACPI 构建配置遵循 `profiles/cachyos-7.0.12-acpi/versions.json`，保留校验、独立构建和回退流程。
- 提交前运行 `python3 -m unittest discover -s tests -v`，并对修改的 Shell 脚本执行 `bash -n`。
