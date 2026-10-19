# why-rpi5-gpu-compute-sucks

**为什么树莓派 5 跑 GPU 计算就是屎 —— 能画图，算不快，连内存都有自己的规矩**

树莓派 5 的 VideoCore VII 能做什么？在这台 16 GB Pi 5 上，答案很分裂：

- **画图能用。** 无头 OpenGL 测试中，1080p 的简单模型、纹理和 Phong 着色场景超过 300 FPS，四窗口模糊合成约 92 FPS。
- **通用计算难用。** ncnn 超分的 Vulkan 路径比 CPU 慢 **21.4 倍**，llama.cpp 提示词处理慢 **64.8 倍**。
- **系统设计很有个性。** 四个 CPU 核心，Linux 里却冒出 **8 个模拟 NUMA 节点**，还得靠交错分配配合内存地址布局。

本文讨论的，就是这套博通硬件与软件生态的实际体验。GPU 能跑 Vulkan Compute，不等于应用能得到加速；
一块板子能顺畅画图，也不等于适合卷积、量化矩阵乘法和 LLM 推理。
**博通的「与众不同」，是你本来只想跑个程序，最后却被迫研究这颗芯片的家谱。**

所有应用成绩均来自本文这台机器。图形测试为离屏 demo，UHD 630 的比较属于规格推断；
原始日志、配置和复现入口随文提供。

## 1. 先看交付：CPU 与 GPU 跑起来差多少

| 工作负载 | 四核 Cortex-A76 CPU | V3D GPU | 结果 |
| --- | ---: | ---: | --- |
| clpeak FP32，各自最好测试项 | 约 **176 GFLOPS** | Vulkan 约 **56.4 GFLOPS** | GPU 实测约为 CPU 的 1/3 |
| ncnn 超分，320×136 输入 | **493 ms** | **10,575 ms** | GPU 慢 **21.4×** |
| llama.cpp pp64 | **143.95 t/s** | **2.22 t/s** | GPU 慢 **64.8×** |
| llama.cpp tg16 | **21.05 t/s** | **0.32 t/s** | GPU 慢 **65.8×** |

CPU 在 **2.8 GHz 超频**运行。这是 CPU 一边的有利条件；表格回答的是这台板子选择哪个后端更快，
不是同功耗或同频率竞赛。CPU / GPU 也没有被强制使用相同算法、相同 kernel 或相同数据布局。

clpeak 是寄存器算术微基准，能在简单、规整的循环里取得较高吞吐。
同一块 GPU 的 OpenCL/rusticl FP32 float4 约为 **27.68 GFLOPS**，Vulkan/v3dv 约为 **56.37 GFLOPS**。
**连纯算术测试换一个后端都能差一倍，API 标签显然不能代替实际性能。**

ncnn 使用 Real-ESRGAN 的 `realesr-animevideov3-x4` 模型；llama.cpp 使用 `Qwen3-0.6B-Q4_K_M`。
两种应用、两类计算负载，都没有从 GPU 后端得到加速。对想在板子上跑超分或 LLM 的用户，
**这套 V3D 计算栈的支持和适配就是差：程序跑通了，时间却多花几十倍。**

### 卷积和推理为什么不能用一张 FLOPS 表解释

CPU 路径可能使用 Winograd 等算法，Vulkan 路径的量化解码、矩阵布局、融合和同步安排也会影响成绩。
这些都是应用后端能否发挥硬件的组成部分。两套框架都慢，说明实际支持存在明显缺口；
要进一步分清硬件、编译器、驱动和框架 kernel 各占多少责任，仍需要逐层测量。

对照应用成绩，也能看到算术微基准的局限：GPU 在 clpeak 里只是慢约三倍，
到了应用里却慢二十到六十多倍。**用户买的是整条计算路径，不能拿一个好看的 FMA 循环替它交差。**

数据入口：[`data/release-vk.json`](data/release-vk.json)、
[`data/release-opencl.json`](data/release-opencl.json)、[`data/release-cpu.json`](data/release-cpu.json)。
应用软件与模型版本见第 6 节。

## 2. 它确实能画图：无显示器，也能测出图形性能

这次没有接显示器，没有启动 X11 或 Wayland 桌面。使用 glmark2 的 **null/surfaceless 离屏后端**，
让 V3D 渲染到 FBO，在帧缓冲轮换时用同步对象约束在途工作。

实际驱动字符串是 **Broadcom / V3D 7.1.10.2**，不是 CPU 软件渲染器 llvmpipe。
当前 Mesa 同时提供 **桌面 OpenGL 3.1、OpenGL ES 3.1、Vulkan 1.3**。
桌面 OpenGL 与 ES 的模型、纹理、Phong 着色三项像素校验均通过。

### 两种分辨率，两个图形 API，各跑两轮

每个场景持续 **2 秒**，两轮结果取范围。无屏幕呈现，也没有显示器垂直同步限帧。
这是自选五场景的探索性测试，**不是完整 glmark2 默认套件**，因此不拿它的平均分与网上总分比较。

| 场景 | OpenGL 800×600 | OpenGL ES 800×600 | OpenGL 1920×1080 | OpenGL ES 1920×1080 |
| --- | ---: | ---: | ---: | ---: |
| 模型渲染，VBO | 1482–1492 FPS | 1469–1475 FPS | **366–369 FPS** | 367–370 FPS |
| 纹理，linear | 1288–1292 FPS | 1282–1286 FPS | **336–343 FPS** | 344 FPS |
| Phong 着色 | 1239–1245 FPS | 1243–1249 FPS | **328–329 FPS** | 327–330 FPS |
| 2D 卷积效果 | 1206–1211 FPS | 1194–1204 FPS | **306 FPS** | 305–306 FPS |
| 四窗口模糊合成 | 343–344 FPS | 341–342 FPS | **92 FPS** | 92 FPS |

**频率条件必须一起看。** 这次没有手动锁频或更改超频配置；监控期间读到的 `fw-clk-v3d` 为
500–1150 MHz，而八次正式性能运行中的全部采样点都是 **1150 MHz**。
因此这组结果按 **报告频率 1.15 GHz** 记录，不能标成 800 MHz 默认频率下的成绩。
全程 CPU 温度采样约 **44.7–52.4°C**，含测试切换与调试阶段。

### 这是什么水平？

在这几个小场景里，VC7 能处理 1080p 的基础几何、采样和逐像素着色；
四窗口模糊合成负载更重，帧时间约 **10.9 ms**。分辨率从 800×600 提升到 1080p，
像素数增长约 4.32 倍，多数场景帧率下降到四分之一上下，说明这些配置对像素相关工作量相当敏感。

它适合基础桌面合成、可视化界面、轻量 3D、WebGL 和通过图形管线实现的图像效果。
浏览器是否启用硬件加速还得检查实际后端；这里的 `desktop` 场景也只是基准程序模拟窗口，
不是在给真实桌面合成器打分。复杂游戏、多层高成本效果或大模型推理，均不能由这张表担保。

**能用来画图，算是尽到了 GPU 的本职；通用计算跑成这个样子，另一本账照样难看。**

Vulkan 方面，Debian vkmark **2025.01-1** 的 headless 插件在本环境中启动发生 SIGSEGV，
未取得图形成绩。调用栈停在无调试符号的 vkmark 主程序内；这是该工具与环境组合的失败，
尚不能定位到硬件、驱动或工具中的哪一层。

日志、参数、像素校验、频率采样及逐项 CSV：[`data/graphics-20261019/`](data/graphics-20261019/)。

### 与 UHD 630 相比，谁更强？

**我的判断是：在内存与功耗条件正常的典型桌面配置里，UHD 630 的图形性能应当明显强于 VC7。**
这不是借 Intel 的 OpenGL 版本覆盖来取胜，单看执行规模就有差距。
但本文没有同参数跑过 UHD 630，不能给出实测 FPS 倍数。

下面取 **24 EU 的 UHD 630 配置**，以 Core i7-8700 的最高图形频率 **1.20 GHz** 作规格示例：

| 算术规模参照 | VC7 | UHD 630，24 EU 配置 |
| --- | --- | --- |
| FP32 每周期模型 | 12 QPU × 4 物理路 × 2 FLOP = **96 FLOP/周期** | 24 EU × 16 FLOP = **384 FLOP/周期** |
| 参照频率 | 本次图形运行报告 **1.15 GHz** | i7-8700 图形最高动态频率 **1.20 GHz** |
| 按模型计算的 FP32 峰值 | **110.4 GFLOPS** | **460.8 GFLOPS** |

VC7 模型来自实际开发 Pi 5 汇编工具的
[Idein/py-videocore7](https://github.com/Idein/py-videocore7#about-videocore-vii-qpu)，属于社区实现资料。
Coffee Lake 的 1 slice × 3 subslice × 8 EU 配置可对照
[Intel compute-runtime 的 CflHw1x3x8 定义](https://github.com/intel/compute-runtime/blob/24.35.30872.22/shared/source/gen9/hw_info_cfl.cpp)。
Intel Gen9 的每 EU 每周期 16 个 FP32 运算，见
[Intel 架构文档第 7 页与第 17 页](https://www.intel.com/content/dam/develop/external/us/en/documents/the-compute-architecture-of-intel-processor-graphics-gen9-v1d0-166010.pdf)；
UHD 630 的产品与 1.20 GHz 频率参照见
[Intel Core i7-8700 规格](https://www.intel.com/content/www/us/en/products/sku/126686/intel-core-i78700-processor-12m-cache-up-to-4-60-ghz/specifications.html)。

这是 **算术吞吐模型约 4.2 倍的差距**，不是“游戏帧率必然差 4.2 倍”。
光栅化、纹理、带宽、缓存、分块渲染和 CPU 提交速度都会改变具体场景的结果；
UHD 630 的实际频率与共享内存条件也随整机而变。

所以本文对档位的评价是：**VC7 是能承担轻量图形工作的单板机 GPU，不应按 UHD 630 的替代品来期待。**
要量化两者差多少，需要给 UHD 630 跑相同的五个离屏场景、分辨率和同步方式。

## 3. QPU 的特色：口径得自己拼，软件得额外适配

`v3d_ident` 读出 **3 个 slice、12 个 QPU**；Mesa 也按 `NSLC × QUPS` 计算 QPU 数量。
逻辑上看到 16 路向量，不意味着每周期有 16 路物理运算能力。
按 py-videocore7 的模型，800 MHz 对应 **76.8 GFLOPS**；1.15 GHz 对应 **110.4 GFLOPS**。
计算测试没有同步保全 V3D 负载频率，因此不把 56.4 GFLOPS 换算成一个确定的峰值利用率。

公开的 VideoCore IV 手册能帮助理解家族背景，但 Pi 4 是 VideoCore VI，Pi 5 是 VideoCore VII。
本地 Mesa 明确区分 VC4 与 V3D 编译器，且 `has_accumulators = ver < 71`
表明 7.1 的寄存器结构已有变化。开发者需要的是当前架构的信息，不能靠一份老手册一路脑补。

本文环境中，实际可见的计算限制包括：

| 项目 | 本文观测 | 对计算开发的影响 |
| --- | --- | --- |
| 存储缓冲区 | 每 stage **8 个 SSBO** | 资源拆分与 kernel 布局需要适配 |
| 共享内存 | **32 KiB** | 分块和数据复用受到容量约束 |
| 矩阵 / 整数点积加速 | 所测后端未提供所需快路径能力 | 不能直接使用依赖这些能力的推理路径 |
| FP16 算术 | v3dv clpeak half4 **56.29**，float4 **56.37 GFLOPS** | 该路径没有体现明显的半精度算力倍率 |
| 计时与 profiling | 依赖软件时间戳和具体事件计数 | 定位性能需要核对实现与采集覆盖 |

API 查询反映的是这条后端暴露的能力，具体物理电路还需硬件资料佐证。
没有矩阵单元并不妨碍 GPU 进行通用计算；问题是常见工作负载有没有合适的执行路径。

**特殊设计的成本不能只算硅片面积，还要算有多少人得围着它改编译器、排 shader、补工具。**
资料、工具和优化需要社区与驱动团队持续建设。厂商可以有自己的取舍，
用户也有权根据实际成绩评价它难不难用。

## 4. 连内存都要整活：四个核心，八个 NUMA 节点，大家一样远

2026-10-19，在本文这台 **16 GB Pi 5** 上查看 `lscpu`：

```text
CPU(s):              4
Model name:          Cortex-A76
NUMA node(s):        8
NUMA node0 CPU(s):   0-3
NUMA node1 CPU(s):   0-3
...（node2 到 node6 同样是 0-3）
NUMA node7 CPU(s):   0-3
```

`numactl --hardware` 显示：**每个节点约 2 GiB，节点距离矩阵全部为 10**。
没有谁近，没有谁远，四个核心在每个节点里重复出现，一碗水端平。

答案就在启动参数里：

```text
numa=fake=8 numa_policy=interleave
```

**`fake`。这是启动链自己带来的名字。** 本机 `cmdline.txt` 没有手写这些参数，
运行中的 `/proc/cmdline` 却有；内核日志也确认默认策略是 `interleave:0-7`。
这不是多路服务器那种 CPU 与本地内存的远近关系，而是借用 NUMA 机制调整页面分配。

### bank 地址映射，为什么要一路冒泡到操作系统

DRAM 有多个 bank。物理地址中哪些位负责选择 bank，会影响连续地址能否分散访问它们。
选择位放得低，较近的地址容易跨 bank；放得高，一大片地址就可能集中在部分 bank。
让系统从不同的大区域交错分配页面，可以帮助分散访问。

树莓派官方列出的地址布局是：

```text
SDRAM_BANKLOW=1：{bank[3:1], row, bank[0], column}  ← Pi 5 默认推荐值
SDRAM_BANKLOW=3：{bank[3], row, bank[2:0], column}  ← Pi 4 默认推荐值
```

Pi 5 的推荐布局把更多 bank 选择位放在地址高处，再配合模拟 NUMA 的交错分配。
常见 x86 平台和许多 ARM SoC 通过内存控制器的地址映射、交错等机制处理 bank 并行，
不需要向用户展示这样的多节点拓扑。ARM 并不统一规定内存控制器，不能把这当成 ARM 的通病。

最有节目效果的是官方的说明：**Pi 5 内核如果不支持 NUMA，性能会受损；改用 `SDRAM_BANKLOW=3`
可以缓解，而追求最高性能应确保 NUMA 可用。**
默认值、位布局、启动器添加参数的行为及这条性能说明，均见
[树莓派官方文档](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#SDRAM_BANKLOW)。

于是，一台四核小板子的内存性能，需要启动器、内核配置和页面分配策略共同遵守约定。
**买板子时没说送多路服务器知识，开机以后倒是把 NUMA 选修课给你排上了。**

这套适配确实可以正常工作。本文没有开关 NUMA 做性能对照，也没有博通内部设计决策资料；
能确认的是这套方案存在，以及官方写明了内核不配合的后果。
八节点是本文机器的结果，并非所有容量、固件的固定配置。
它也不是前面 GPU 计算慢的已证实原因。

本机记录：[`data/numa-pi5-20261019.txt`](data/numa-pi5-20261019.txt)。

## 5. 性能不好查，工具也得读源码才能用明白

图形与计算生态的成熟程度，还体现在性能问题能否被方便、准确地定位。
这里有三种东西需要分清：应用端到端时间、驱动记录的时间窗口、硬件事件计数。

### 时间戳：有纳秒数字，不等于拿到了纯执行时间

本地 Mesa 的 `V3DDriver::gpu_timestamp()` 使用 CPU boot time，
`cpu_gpu_timestamp()` 返回不支持；v3dv 的时间戳查询通过内核 CPU job 等待依赖后写入。
这些是可读源码里的实现事实。两个软件时间戳之间可能包含同步、调度和边界延迟，
所以 trace 窗口的并集不能直接叫作 GPU 硬件忙碌时间。

例如保存的记录里，ncnn 的软件窗口并集约 **98.7%**，llama.cpp 约 **99.9%**。
它们说明软件记录窗口很密集，却不能据此排除提交、同步、访存或执行单元空转。

### 计数器：缓存事件、指令执行与 ALU 利用率各有口径

`QPU-total-instr-cache-hit/miss` 描述的是所有 slice 的指令缓存事件。
缓存请求粒度、预取行为与指令执行之间的关系，需要另外标定；
把它们除以 `cycle-count`，得到的是缓存事件密度。
QPU 的 add/mul 可以共存于同一条机器指令，缓存事件数不能直接变成发射槽利用率。

同样，compute-active 高不等于所有 QPU 都在做有用算术；
L2 命中率高也不代表没有访问延迟，`L2T-stalls-mem` 只描述某种 AXI 读阻塞事件。

还有一个采集覆盖细节：树莓派 `rpi-6.18.y` 的
[`v3d_perfmon_get_values_ioctl()`](https://github.com/raspberrypi/linux/blob/5d64d1ea73716526ed9c0ed184a9cb5f6042e32c/drivers/gpu/drm/v3d/v3d_perfmon.c)
会保存计数并停止当前计数器，后续 job 调度时可以重新启动。
因此高频轮询可能只覆盖长 job 的一小段，而不是连续采完整个工作负载。
这份源码尚未与计算实验时加载的内核二进制逐项匹配，具体影响仍待核实。

**应用慢几十倍是一件很容易测到的事；想解释为什么慢，却得先把测量工具拆开读一遍。**
对开发板而言，这种折腾可以成为学习材料；对想得到计算加速的用户，它就是额外成本。

## 6. 环境与复现

### 本机配置

| 项目 | 配置 |
| --- | --- |
| 板子 | Raspberry Pi 5 Model B Rev 1.1，16 GB |
| CPU | 4× Cortex-A76，2.8 GHz 超频 |
| GPU | BCM2712 / VideoCore VII，寄存器版本 V3D 7.1.10.16；驱动字符串 V3D 7.1.10.2 |
| 系统 / 内核 | Debian 13 aarch64 / 6.18.50+rpt-rpi-2712 |
| Mesa | 26.3.0-devel，`a5d39a4b743b719cfc2a0da410910ea3fd8ff770`，release 构建 |
| 图形测试频率 | 正式运行中的 `fw-clk-v3d` 采样均报告 1150 MHz；未在测试脚本中锁频 |
| 计算测试频率 | 未同步保存 V3D 负载频率，不与图形测试假定同频 |
| clang / GCC | 22.1.8 / 14.2.0 |

CPU 超频配置为 `arm_freq=2800`、`over_voltage_delta=50000`。
`/boot/firmware/config.txt` 是当前永久配置入口。本文没有进行功耗对照。

### 计算测试

- clpeak **2.1.4**，各后端分别测试；CPU 固定迭代次数。
- ncnn **`c6b351b`**，`NCNN_VULKAN=ON`、`CMAKE_BUILD_TYPE=Release`，模型为 `realesr-animevideov3-x4`。
- llama.cpp **b11146**，CPU-only 和 Vulkan 二进制分别放置，模型为 `Qwen3-0.6B-Q4_K_M.gguf`。
- 计数器采集使用 Perfetto CLI **v56.1-c794fceab** 与所编译 Mesa 的 pps-producer。

```sh
# Vulkan / v3dv
clpeak --vulkan --vk-device 0 --single-precision-compute
# OpenCL / rusticl
RUSTICL_ENABLE=v3d clpeak --opencl --single-precision-compute
# CPU，固定迭代
clpeak --cpu --single-precision-compute --iters 20000
```

CPU 后端的自动迭代校准在本机曾给出明显波动，固定迭代后结果约为 176 GFLOPS。
llama.cpp 的 CPU 基线使用独立 CPU-only 构建，目录里不混入 `libggml-vulkan.so`，
避免自动加载后端影响测试路径。

### 无头图形测试

glmark2 源码提交 **`22c527cb0556f3a1ac4445aaa52cc532760928d5`**，release 构建：

```sh
meson setup build --buildtype=release -Dflavors=null-glesv2,null-gl \
  -Ddata-path=/absolute/path/to/glmark2/data
ninja -C build -j4

# 桌面 OpenGL；ES 使用 build/src/glmark2-es2-null
EGL_PLATFORM=surfaceless build/src/glmark2-null --off-screen --size 1920x1080 \
  -b build:use-vbo=true:duration=2.0 \
  -b texture:texture-filter=linear:duration=2.0 \
  -b shading:shading=phong:duration=2.0 \
  -b effect2d:duration=2.0 \
  -b desktop:windows=4:effect=blur:duration=2.0
```

两种 API、两种分辨率、每种两轮。三项像素校验使用 `--validate --off-screen=1`；
性能运行使用默认三个 FBO。单帧校验与多帧缓冲轮换需要区分，否则校验可能读到另一张 FBO。

必须在能访问 `/dev/dri` 的环境中运行，并核实 `GL_RENDERER`。
如果实际输出是 llvmpipe，测到的是 CPU 软件渲染，不能写成 VC7 成绩。
基准软件的构建与离屏方式见 [glmark2 上游](https://github.com/glmark2/glmark2)。

### 驱动与工具配置

本文计算实验使用自编 Mesa；图形驱动在发行版源中也有提供，并不是没有驱动可装。
自编是为了选定版本、本地 rusticl 配置与 Perfetto 功能，构建参数如下：

```sh
CC=clang-22 CXX=clang++ meson setup build --buildtype=release \
  -Dcpp_rtti=false -Drust_std=2021 \
  -Dprefix=/usr/local -Dlibdir=lib/aarch64-linux-gnu \
  -Dgallium-drivers=v3d,zink,llvmpipe -Dvulkan-drivers=broadcom,swrast \
  -Dgallium-rusticl=true -Dglx=disabled -Dopengl=true -Degl=enabled
```

采计数器另需 `-Dperfetto=true -Dallow-fallback-for=perfetto`。
本机 LLVM 没开 RTTI，因此关闭 Mesa 的 C++ RTTI；`RUSTICL_ENABLE=v3d` 用于显式启用该设备。
这些是本文环境条件，不应把每个构建选择都当作博通硬件缺陷。

```sh
MESA_GPU_TRACES=print_csv MESA_GPU_TRACEFILE=/tmp/gpu.csv your_application
# 已导出为 counter,ts,value 的采样
python3 scripts/analyze-counters.py counters.csv
python3 scripts/analyze-gpu-trace.py data/gpu-trace-ncnn.csv
```

计数分析默认使用全窗口，`--skip-first-seconds` 可显式裁剪；是否裁剪应由工作负载边界决定。
脚本只报告事件比值与软件窗口统计，不给出硬件根因判决。

## 7. 给 VC7 安排什么工作

**图形方面，它能承担轻量 3D、界面合成和一些图像效果；计算方面，本文的超分与 LLM 任务应选 CPU。**
无头图形结果说明它能好好画一些东西，但还没有证据把它抬到 UHD 630 的性能档位。
ncnn 与 llama.cpp 的端到端结果则说明，现代 GPGPU 应用的支持不能只看 API 是否存在。

硬件、驱动、编译器、算子和工具共同决定体验。资料公开与专用优化当然可能改善性能，
但用户今天得到的，仍是算得慢、定位费劲、处处得适应特殊规则的计算平台。

再看看那四个核心旁边的八个模拟内存节点，整块板子的气质就很统一了：
**每深入一层，就多一条这颗芯片专属的规矩；最后都得由软件去记，由用户去查。**

博通可以有自己的工程取舍。只是这些取舍落到开发者手里时，
有时候像买了块开发板，有时候像领了一份没有附答案的驱动工程师入职试卷。

## 数据与参考入口

- 算术测试：`data/{baseline,release}-{opencl,vk,cpu}.json`。
- 应用软件时间线：`data/gpu-trace-ncnn.csv`、`data/gpu-trace-llamacpp.csv`。
- 无头图形：[`data/graphics-20261019/REPORT.md`](data/graphics-20261019/REPORT.md)，含逐项结果、运行参数和频率采样。
- NUMA：[`data/numa-pi5-20261019.txt`](data/numa-pi5-20261019.txt)。
- [py-videocore7](https://github.com/Idein/py-videocore7)：Pi 5 QPU 汇编工具与吞吐模型。
- [Intel Gen9 架构文档](https://www.intel.com/content/dam/develop/external/us/en/documents/the-compute-architecture-of-intel-processor-graphics-gen9-v1d0-166010.pdf)：EU 算术吞吐与 GT2 配置示例。
- [Intel i7-8700 产品规格](https://www.intel.com/content/www/us/en/products/sku/126686/intel-core-i78700-processor-12m-cache-up-to-4-60-ghz/specifications.html)：UHD 630 与图形频率。
- [树莓派 SDRAM_BANKLOW 文档](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#SDRAM_BANKLOW)：bank 地址布局与模拟 NUMA 的配套要求。

## License

MIT，见 [LICENSE](LICENSE)。数据与文字可复用；欢迎带着可复现的配置和结果讨论。
