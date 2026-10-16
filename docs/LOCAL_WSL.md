# 单台 PC：WSL2 Ubuntu 本地 CUDA 与设备接入

目标是 **同一台 Windows PC**：WSL2 Ubuntu 跑 Python 后端、CUDA 推理和前端开发服务，Windows 浏览器打开网页，A1 与 IMU 插这台 PC。不是云端后端，也不需要 Mac。

```text
球内：充电宝 → UWB 标签 T5
                    ↓ UWB 无线
球外：A1 → USB 转串口 ─┐
      IMU → Type-C ───┴→ PC 的 USB → usbipd-win → WSL2 后端 :8010
      A2/A3 各自供电                               ↑
Windows 浏览器 ← 本机网页（生产构建 :8010，开发时 :5174）
```

## 1. 一次性检查 Windows、WSL 和 GPU

**Windows PowerShell**：

```powershell
wsl --update
wsl --list --verbose
```

确认 Ubuntu 的 VERSION 为 **2**。如果为 1，先备份发行版，再执行 `wsl --set-version Ubuntu 2`（用列表中的实际发行版名替换 `Ubuntu`）。更新若要求重启，先保存正在运行的工作。

PC 必须有支持 CUDA 的 **NVIDIA GPU**，并在 Windows 安装支持 WSL 的 NVIDIA 驱动。不要在 WSL 里安装 Linux NVIDIA 显卡驱动，不要用 `cuda-drivers` 覆盖 Windows 映射的驱动。

**WSL Ubuntu**：

```bash
nvidia-smi
# 若命令未在 PATH：
/usr/lib/wsl/lib/nvidia-smi
```

能列出 GPU 仅证明驱动可见，还需要下面的 PyTorch 实际运算检查。当前改动未在你的 PC、WSL GPU 或实物设备上执行验收。

## 2. 在 WSL 准备代码、Python 和本地资产

推荐仓库放在 WSL Linux 文件系统，例如 `~/refereelink-showcase`，不要复制 Windows 或云端的 `.venv`；需要在 WSL 重建 Linux 虚拟环境。已存在的可用 CUDA 环境可以复用。

在仓库根目录执行（需要已安装 Python 3.12+、Node.js 22.12+；项目 `.nvmrc` 使用 Node 24）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r backend_core/requirements-inference.txt
npm ci
sudo apt-get update
sudo apt-get install -y ffmpeg usbutils
```

推理依赖记录在 `backend_core/requirements-inference.txt`；PyTorch wheel 的 CUDA 版本与 GPU 架构必须兼容本机 Windows 驱动。已有环境应先核对版本，不盲目覆盖。若安装到 CPU wheel 或不支持本机显卡的 wheel，必须安装匹配的 CUDA 构建，不能关闭 CUDA 检查继续展示模型结果。既有远端环境版本记录不是本机兼容性验收。

在**启动后端所用的同一个 Python 环境**执行：

```bash
.venv/bin/python - <<'PY'
import torch
assert torch.cuda.is_available(), 'CUDA unavailable: check Windows driver and PyTorch wheel'
x = torch.ones(1, device='cuda')
assert (x + x).item() == 2
torch.cuda.synchronize()
print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name())
PY
```

这项检查不等于模型/精度验收，但能发现只有 `nvidia-smi` 可用而 PyTorch 无法运算的情况。启动后端仍会再次检查 CUDA；缺少 PyTorch、CUDA 不可用时直接报错，没有 CPU 或模拟推理回退。

本机还需恢复以下 **Git 外资产**，不能仅复制代码：

- `runtime/media/`：授权视频；实地多视角视频包按 [下载与恢复说明](MULTIVIEW_ASSETS.md) 解压。
- `runtime/weights/`：`yolo11s.pt`、`football-pitch-detection.pt`、`mvfoul.pth.tar`，以及所用模型要求的其他缓存/检查点。
- `runtime/media/calibration/match-1.npz`：实际视频对应的标定包，不能拿不匹配的标定冒用。
- 外部 VARS 代码默认在 `backend_core/third_party/sn-mvfoul/VARS model`。源码授权与模型要求见 [SOURCE.md](../backend_core/SOURCE.md)。
- 原生犯规展示所需的私有 qualification 记录及其绑定的权重/源码/素材。新环境缺少有效 qualification 时应保持阻塞，不能伪造或跳过。

默认路径都从当前仓库计算。可选：复制 `deploy/showcase.env.example` 为被 Git 忽略的 `deploy/showcase.env`，设置实际资产路径。该文件由启动脚本以 **Bash 语法**加载，只加载自己信任的配置；带空格的路径加引号。若使用仓库外环境，设置绝对路径 `SHOWCASE_PYTHON=/实际路径/bin/python`；除非明确分离环境，否则不要另外覆盖 `SHOWCASE_CORE_PYTHON`。

已有旧配置时，清理其中云端 `/srv/…`、旧解释器路径，以及 `web/.env.local`/shell 中指向云端的 `BACKEND_URL`、`LIVE_BACKEND_URL`。显式覆盖仍优先；程序不会自动迁移资产或替你修改私有配置。

**多视角功能边界**：本仓库不是完整 Backend。多视角案例、分析、相机/手机接入与人工复核 API 仍依赖原 Backend 服务。要全部本地运行，必须另外把该服务部署到这台 PC，默认 `SHOWCASE_UPSTREAM=http://127.0.0.1:8000`。未运行时该页面会报告离线/不可用，不会转去云端或伪造结果；本地 UWB/IMU 不依赖该 8000 服务。

## 3. 把 A1 和 IMU 的 USB 交给 WSL

先完成 A1/A2/A3/T5 的 Location 烧录和编号，A1 接 USB 转串口，IMU 用 **数据线**接 PC。A2/A3 只供电，球内标签由充电宝供电。IMU 留在球外，数据与足球定位独立，不做融合。

### Windows：一次共享，每次需要时 attach

官方命令适用于 `usbipd-win 5.0+`。先保持一个 Ubuntu 终端打开。**管理员 PowerShell** 安装工具：

```powershell
winget install --interactive --exact dorssel.usbipd-win
usbipd list
```

逐个插拔 A1 转接器、IMU，确认列表中各自的 BUSID。安装器可能要求重启；`--interactive` 用于让你先确认。安装器创建的 USB/IP 服务与防火墙规则应按本机安全策略管理，不要关闭整个防火墙。

以下 `BUSID` 是占位符，执行前替换为刚才查到的**一个设备**的值，两设备分别执行。首次共享需管理员权限：

```powershell
usbipd bind --busid BUSID
```

随后普通 PowerShell 即可转接：

```powershell
usbipd attach --wsl --busid BUSID
usbipd list
```

两设备都应为 Attached。attach 期间 Windows 串口助手不能同时使用它。拔插、重启 Windows/WSL 后可能需要重新查询 BUSID 并 attach；不是烧一次板就永久转接。

### WSL：确认 Linux 串口和权限

```bash
lsusb
.venv/bin/python -m serial.tools.list_ports -v
ls -l /dev/serial/by-id/
```

设备应出现 `/dev/ttyUSB…` 或 `/dev/ttyACM…`。有 `/dev/serial/by-id/…` 时优先使用稳定路径；没有该目录时按枚举结果填写，插拔后重新确认。页面不能填 Windows `COM3`，也不能让 UWB/IMU 共用同一个端口。不要提交真实设备路径或标识。

若端口属于 `dialout` 组但当前用户无权限：

```bash
sudo usermod -aG dialout "$USER"
newgrp dialout
```

重新启动后端，使它继承新组权限。若节点所属组不同，按实际设备设置限定范围的 udev 权限；不要 `chmod 666`，也不要用 root 运行整个后端。`lsusb` 可见但无串口节点时，先更新 WSL 内核、检查 `dmesg` 和 USB 串口驱动；不要用更换固件或放宽解析校验掩盖 USB 接入问题。

## 4. 现场启动：一个后端同时提供网页

在 WSL 仓库根目录，提前构建一次：

```bash
npm run build
```

每次展示前，完成供电与 USB attach，关闭其他串口助手，然后：

```bash
npm run start:backend
```

该命令等价于 `bash deploy/start-local.sh`，默认用 `.venv/bin/python` 启动 `127.0.0.1:8010`，按 Ctrl+C 停止；不依赖 systemd。Windows 浏览器通过 WSL localhost forwarding 打开 **http://localhost:8010/**。运行以下命令确认健康接口中的 `cuda_available=true`、`device=cuda` 和 GPU 名称，而不是把“网页能打开”当 GPU 验收：

```bash
curl --fail http://127.0.0.1:8010/api/health
```

若改前端代码，再开一个 WSL 终端执行 `npm run dev`，Windows 浏览器打开 **http://localhost:5174/**；Vite 默认代理到本机 8010，不需云端地址。保存正式展示用的页面前先确认访问的是本机，不是旧云端书签。

页面进入“足球定位 → 设备数据”，填两个 WSL 串口、标签 ID **5** 和 A1/A2/A3 的实测坐标，点击“连接串口”。必须有 **有效数据**，不能只有“串口已打开”。UWB/IMU 均为 **115200/8N1**，IMU 上电等约 5 秒。标签平移和球外 IMU 分别验证；不把回放当实物，不把 IMU 波形说成足球加速度。同平面二维定位与烧录/真机验收边界见 [UWB 指南](hardware/TWR30_SETUP.md) 和 [IMU 指南](hardware/IMU_SETUP.md)。

## 5. 最短排查顺序

上述是默认独立 USB 接法。实验性的 [标签 STM32 / UWB 桥接模式](hardware/IMU_UWB_BRIDGE.md) 只需把 A1 的 USB attach 到 WSL；但它需要尚未完成的自定义标签/A1 固件。不能因为页面能切换就把 IMU 插到标签上当作已经可用。

| 现象 | 检查 |
| --- | --- |
| 找不到后端 Python | 是否在 WSL 创建 `.venv`，或设置了正确的 `SHOWCASE_PYTHON`；不要指向 Windows `.exe` 或旧云端目录 |
| CUDA 启动失败 | Windows NVIDIA 驱动、WSL2、当前解释器的 CUDA PyTorch、实际 GPU 张量运算；没有 CPU 兜底 |
| WSL 内能访问，Windows 浏览器不能 | 先确认 WSL 的 curl 成功，检查 WSL localhost forwarding、VPN/代理与本机防火墙；不要默认开放外网监听 |
| 网页仍访问云端 | 关闭旧标签，检查浏览器地址及 `web/.env.local`/shell 的 `BACKEND_URL`、`LIVE_BACKEND_URL`，重启 Vite |
| Windows 看得到 USB，页面无串口 | `usbipd list` 是否 Attached；WSL 中是否有串口节点；权限和独占占用是否正确 |
| 端口开了但无数据 | 固件/接线/115200/8N1、标签 ID、四块 UWB 供电、IMU 数据线及初始化；看拒帧原因，不关校验 |
| 多视角离线 | 本机 8000 上游服务与其案例、相机和素材配置，不是本机 8010 或 CUDA 替代了它 |

可选的 user systemd 模板在 `deploy/refereelink-showcase.service`，默认仓库路径为 `~/refereelink-showcase`；位置不同时同时修改 WorkingDirectory 和 ExecStart。需要系统已启用 WSL systemd；服务不会自动 attach USB，组权限变更后需重启服务。远程部署可显式设置前端 `BACKEND_URL` 和后端监听地址，但本地默认不暴露 `0.0.0.0`。

官方依据：[Microsoft USB 接入](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)、[WSL 网络与 localhost](https://learn.microsoft.com/en-us/windows/wsl/networking)、[NVIDIA CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)。软件检查不等于你的 WSL GPU、真实模型或传感器已验收；`hardware_verified=false` 保持不变。
