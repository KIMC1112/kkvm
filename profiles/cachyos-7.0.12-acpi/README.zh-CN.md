# 固定部署配置：CachyOS 7.0.12 + ACPI identity

这个独立入口保留已核验部署的两项特征，**不替换原 main.sh 的默认流程**：

- linux-cachyos / linux-cachyos-headers 都固定为 **7.0.12-1**，启动内核为 **7.0.12-1-cachyos**（x86_64_v3）。不是现有 TKG 模块的 7.0-latest。
- QEMU **11.0.3** 固定源码提交 aeec49e8170de7846f476124602cf7acd400c3df，使用仓库 AMD 补丁，再叠加 **ACPI 表头身份 + IVRS revision 2**。保留 RTL8125、PNG、SPICE，关闭 TCG/VNC，与参考构建的功能选择一致。

这不是旧机器的镜像克隆，也不保证不同工具链生成完全相同的二进制 SHA256。旧内核长期锁定会错过安全修复，应限制暴露面并定期评估；本配置不会私自升级它。

## 边界

- 适用 AMD CachyOS 桌面。自动内核安装仅支持已具有 GRUB + mkinitcpio + LTS 救援内核的布局。其他布局明确拒绝，不猜测引导配置。
- 不自动重启、停启虚拟机、修改 VM XML、清空磁盘、安装 Windows、替换 GPU/USB 切换脚本或改桌面快捷方式。已有 win10_start 入口保持原样；新建快捷方式仍应命名 win10_start。
- 不将网卡替换为 VirtIO；原部署模块的 RTL8125 保持不变。
- 不上传主机 ACPI 数据、SMBIOS、序列号、账号、密码、虚拟磁盘或内核二进制。运行记录仅写入 Git 忽略的 .local/。
- **ACPI identity 不代表内核 DMA 保护或真实硬件隔离已开启。** 不导入物理地址、PCI 拓扑、AML 正文、表长度、IVinfo/DMA opt-in 声明。

## 1. 检查并保留现有 7.0.12

在仓库根目录，以普通账号运行：

~~~bash
bash setup-cachyos7012-acpi.sh check
sudo bash setup-cachyos7012-acpi.sh pin-kernel
~~~

check 只读；pin-kernel 必须同时确认当前运行内核、已装内核包及 headers 精确匹配后才修改 pacman.conf 的 IgnorePkg，保留原有忽略项，并保存原文件 SHA256 和副本。已锁定则不重复改写。

不要同时运行原有 Kernel (TKG/latest) 安装流程，否则会与本配置目的相违背。

## 2. 新主机安装相同内核（显式操作）

先准备以下**四个已留存的官方签名文件**到一个本地目录；哈希在 versions.json 中固定。此版本没有添加未经核实的下载地址或自动使用 latest 的后备路径。

- linux-cachyos-7.0.12-1-x86_64_v3.pkg.tar.zst
- linux-cachyos-7.0.12-1-x86_64_v3.pkg.tar.zst.sig
- linux-cachyos-headers-7.0.12-1-x86_64_v3.pkg.tar.zst
- linux-cachyos-headers-7.0.12-1-x86_64_v3.pkg.tar.zst.sig

~~~bash
sudo bash setup-cachyos7012-acpi.sh verify-packages /path/to/packages
sudo bash setup-cachyos7012-acpi.sh install-kernel /path/to/packages
~~~

脚本先检查四个 SHA256，再通过现有 pacman-key 信任库验证签名。不会降低签名要求或自动导入陌生密钥。

安装前要求：
1. 已安装 linux-cachyos 与 headers，现有版本的回退包及其签名仍在 /var/cache/pacman/pkg/。
2. 有可用的 LTS 救援内核、initramfs 和 GRUB 配置。文件检查不等于实际救援启动测试；维护前应自行验证可启动。
3. 没有待处理的 pacman 锁；AMD CPU 支持 x86-64-v3 所需指令。
4. 存在内核绑定 NVIDIA 包时会拒绝：先人工规划相容驱动，脚本不擅自卸载或修改 DKMS。即使没有该包，也需自行验证其他第三方模块在目标内核可用。

随后需输入 INSTALL-7.0.12 并确认 pacman 的事务。它会保留旧包、相关 boot 文件和配置，安装精确版本、生成 initramfs/GRUB 菜单并锁定包。**不更改默认启动项、不自动重启**；维护时自行选择新内核，启动成功后重新运行 check。

任何失败都应先检查日志和备份，**不要直接重启**。备份位于命令输出的 /var/lib/kkvm/kernel-7.0.12/ 时间戳目录。需要回滚内核时，用其中留存并验证签名的原版本两个包执行 pacman -U，再重新生成 initramfs 和 GRUB；不要混入其他备份目录中的包。无法进入系统时使用保留的 LTS 救援入口。自动安装不宣称能够无条件事务回滚所有第三方包钩子。

## 3. 在目标机器构建 QEMU

先确认已启动 7.0.12。准备工具及开发依赖（参考现有 QEMU 模块）：git、Python >=3.11、C 编译器、ninja、pkg-config，以及 GLib、pixman、libpng、SPICE、SDL2、OpenGL、libusb、libudev、PipeWire、liburing、libaio、libnuma、libseccomp、zstd 等开发库。

脚本不会为安装依赖而执行系统全量升级；缺失依赖时由 configure 明确报错，修复后重新构建。

~~~bash
bash setup-cachyos7012-acpi.sh build --jobs 4
~~~

默认最多 4 个编译任务，避免占满内存。每次使用新的 .local/cachyos7012-acpi-* 工作目录，失败保留记录，不清空旧构建。

构建步骤：
1. 校验运行内核和 headers；读取本机 FACP/IVRS 等 ACPI 表，验证签名、长度和 checksum。
2. 仅保留 APIC/DSDT/FACP/HPET/IVRS/MCFG/TPM2 中存在的表头 bytes10..35。FACP、IVRS 必须存在，其他表可缺省。
3. 下载固定 QEMU commit，校验 VERSION 和 AMD 补丁 SHA256，应用补丁。
4. 使用参考构建的固定默认磁盘型号，按当前宿主 FACP 更新基础 OEM 字段，再施加 identity overlay。修改前保存两份原始源码及 SHA256，生成精确 diff。
5. 编译并检查版本、RTL8125 设备注册；先安装到本地 staging，不覆盖系统 QEMU。

ACPI 表头为**目标机器自身**的数据，不硬编码某台旧电脑的身份。只是生成逻辑与参考部署一致；在参考机器数据上已验证生成补丁与既有 identity-only.patch 完全一致。

## 4. 隔离安装及新虚拟机引用

build 成功后会打印 build.json 的完整路径。按其输出执行：

~~~bash
sudo bash setup-cachyos7012-acpi.sh install /absolute/path/to/.local/cachyos7012-acpi-XXXXX/build.json
~~~

安装前复核整套 staging 文件哈希。每次构建使用独立的：

~~~text
/opt/kkvm/builds/cachyos7012-acpi-XXXXX/bin/qemu-system-x86_64-acpi-identity
~~~

整个模块/数据目录一起安装，不只复制一个 ELF；安装目标存在则拒绝覆盖。已有 emulator 安装目录完全保留，当前运行的 VM 不切换。

为**新部署**选择此构建时，将安装打印的真实路径设置给现有部署入口：

~~~bash
export KKVM_QEMU_EMULATOR=/opt/kkvm/builds/cachyos7012-acpi-XXXXX/bin/qemu-system-x86_64-acpi-identity
bash main.sh
# 选择 Deploy Auto/Unattended XML；其他部署问题仍按原流程回答。
~~~

环境变量未设置时保持原默认 emulator 路径。不要把示例 XXXXX 原样复制；不要用本命令对已运行的 VM 强行切换。

已有 VM 迁移应在维护窗口正常关机、备份 XML 后，仅调整 emulator 路径并验证启动；回退时恢复原路径和 XML，不需要回滚 Windows 磁盘。

## 恢复与检查

源码补丁恢复（仅用于构建工作目录，不能替代运行程序回退）：

~~~bash
python3 profiles/cachyos-7.0.12-acpi/acpi_identity.py restore \
  --source /absolute/path/to/work/source \
  --evidence /absolute/path/to/work/acpi-evidence
~~~

恢复 pacman 忽略项（使用命令输出的具体 pin 备份目录）：

~~~bash
sudo python3 profiles/cachyos-7.0.12-acpi/kernel.py restore-pin /var/lib/kkvm/kernel-7.0.12/EXACT-BACKUP/pin
~~~

两种恢复均验证原件 SHA256，并拒绝覆盖之后的用户改动；测试在额外副本进行，最终修改产物保留。

测试：

~~~bash
python3 -m unittest discover -s tests -v
bash -n setup-cachyos7012-acpi.sh modules/deploy.sh
~~~

交付时完成的是源码回放、补丁一致性、自动化单元测试及 shell 语法验证；未在生产主机运行新内核安装或新的完整 QEMU 编译/VM 启动，不能据此宣称这些步骤已实机成功。
