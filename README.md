<div align="center">

# kkvm

This project automates complex Linux virtualization tasks.

Canonical repository: [KIMC1112/kkvm](https://github.com/KIMC1112/kkvm)

后续搭建、重建和修改统一以此仓库为准。

</div>

---






## Instructions

<details>
<summary>Expand for details...</summary>

#### 1. Clone Git repository
```sh
git clone --single-branch --depth=1 https://github.com/KIMC1112/kkvm.git
```

#### 2. Change directory
```sh
cd kkvm/
```

#### 3. Execute
```sh
./main.sh
```
- Experimental distro support:
```sh
EXPERIMENTAL=1 ./main.sh
```

---

### 4. Update repository
- ***Make sure you're in the `kkvm/` root directory when running the command below!***
```sh
git pull --ff-only origin main
```

</details>





---

## Supported Distros

| Distro         | Status       |
|----------------|--------------|
| Arch based     | Supported    |
| Debian based   | Experimental |
| Fedora based   | Experimental |
| openSUSE based | Experimental |

## Prerequisites

- `git` package
- Supported Linux distribution
- UEFI/BIOS Settings:
  - CPU virtualization extensions (VT-x / AMD-V)
  - IOMMU support (VT-d / AMD-Vi)
- A dGPU for passthrough (recommended)

## Troubleshooting

#### QEMU log
```
vfio 0000:01:00.0: failed to setup container for group 13: Failed to set group container: Invalid argument
```
#### dmesg log
```
vfio-pci 0000:01:00.0: Firmware has requested this device have a 1:1 IOMMU mapping, rejecting configuring the device without a 1:1 mapping. Contact your platform vendor.
```

- Disable `Pre-boot DMA Protection` (Needed for VFIO)
  - (*Change `IOMMU` from `[Auto]` to `[Enabled]` to find hidden setting*)

## 固定内核与 ACPI identity 部署配置

需要保留 CachyOS **7.0.12-1** 与 QEMU **11.0.3 ACPI identity** 构建时，使用独立入口 `setup-cachyos7012-acpi.sh`。
它固定版本和包 SHA256、保留 RTL8125，在独立目录构建/安装，不自动重启或替换运行中的虚拟机。

详见 [部署、验证与回退说明](profiles/cachyos-7.0.12-acpi/README.zh-CN.md)。原 `main.sh` 的默认流程保持不变。

## kkvm 项目约定

- 新安装目录：`/opt/kkvm`；内核备份：`/var/lib/kkvm/kernel-7.0.12`。
- 新建虚拟机默认名称：`kkvm`；NAT 网络：`kkvm-Router`；网卡保持 `rtl8125`。
- 自定义 QEMU 路径：`KKVM_QEMU_EMULATOR`。
- Windows 桌面快捷方式名称继续使用 `win10_start`，入口继续使用 `/usr/local/bin/win10_start`，沿用动态显卡及 USB 切换。
- 已部署机器的切换与回退见 [迁移说明](docs/MIGRATION.zh-CN.md)。源码改名不会自动迁移正在运行的虚拟机。
- 许可证与贡献者说明见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。

## Windows 桌面启停

部署菜单创建虚拟机成功后，自动安装三个桌面入口：`win10_start`（启动）、`win10_stop`（正常关机）、`win10_stop -f`（强制断电）。已有虚拟机可使用菜单 8 单独安装；安装过程不启停虚拟机。默认正常关机超时不会自动转为强制关闭。

安装条件、动态显卡/USB 接入及恢复方法见 [桌面启停说明](docs/WIN10-CONTROLS.zh-CN.md)。
