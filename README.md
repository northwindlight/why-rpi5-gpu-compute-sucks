# why-rpi5-gpu-compute-sucks

**为什么树莓派 5 跑 GPU 计算就是屎 —— 顺着实测，看看博通有多与众不同**

树莓派 5 的 VideoCore VII（V3D 7.1），在本文记录的软硬件配置下：

> **clpeak 的 Vulkan FP32 最好成绩约 56.4 GFLOPS，四核 CPU 约 176 GFLOPS；**
> **真跑应用，ncnn 超分慢 21.4 倍，llama.cpp 的 pp64 慢 64.8 倍。**

这篇文章要批评的是 **V3D 这套硬件、驱动、编译器和应用适配，在现代 GPGPU 负载上的实际交付**。
能通过 Vulkan 提交计算，离让用户得到计算加速，还差着一整条软件栈。跑通以后比自家 CPU 慢几十倍，
用户没有义务替这条栈的每一层分摊责任；但分析者有义务别把猜测写成硅片判决书。

本文从 GPU 性能查起，又查到四核 CPU 旁边的内存，在 Linux 里长出 **8 个模拟 NUMA 节点**。
博通的「与众不同」，是你本来只想跑个程序，最后却被迫研究这颗芯片的家谱。
GPU 的结果在第 3 节；内存这出节目在 **0.5 节**，有本机输出，也有官方说明。

## TL;DR

| | 4×A76 (CPU) | V3D 7.1 (GPU) |
| --- | --- | --- |
| FP32 实测吞吐（clpeak，各自最好测试项） | **176 GFLOPS** | **56.4 GFLOPS**（约 CPU 的 1/3） |
| ncnn 超分（320×136） | **493 ms** | **10,575 ms（慢 21.4×）** |
| llama.cpp pp64 | **143.95 t/s** | **2.22 t/s（慢 64.8×）** |
| llama.cpp tg16 | **21.05 t/s** | **0.32 t/s（慢 65.8×）** |

这些是记录配置下的成绩，不是所有版本、所有模型的永久判决。CPU 在 **2.8 GHz 超频**运行，
各路径的算法、数据布局也未强制相同；表格回答的是这台板子上用户选择哪个后端更快。

**2026-10-19 方法学更正：** 旧版把指令缓存访问事件当作指令发射，把比值 **23.5%**
写成发射槽利用率，还据此宣布「驱动完美也救不了」。这些推导已撤回。
12 个 QPU 与 56.4 GFLOPS 也没有原文所说的数量级矛盾，见第 6 节。
端到端的慢几十倍仍成立；计数器口径和根因归属必须另外证明。

---

## 0. 前情提要：从 GPU 查到内存，博通处处有惊喜

要理解这坨东西为什么长成这样，得先讲讲博通（Broadcom）。

### 0.1 博通作恶简史

VideoCore 原本是英国小公司 Alphamosaic 的技术，2004 年被博通买走，从此进入博通的标准剧本：**闭源、NDA、手册按关系发放**。在相当长的时间里，VideoCore 的 datasheet 是「签了保密协议也不一定能拿到」的东西——树莓派用户用的是全世界装机量最大的「开放硬件」之一，但 SoC 里最重要的那块芯片，文档是保密的。

更绝的是树莓派 1~4 代的启动顺序：**整台机器是 GPU 先醒的**。上电后 VideoCore 的 VPU 先跑起来，执行一段闭源固件，由它把 ARM 核从复位里放出来。你买的「ARM 开发板」里，ARM 其实是 GPU 的房客。CPU 要发个邮箱消息（mailbox）才能请 GPU 帮忙设置时钟、分配显存、开关电源域。这个架构浪漫地诠释了什么叫「你以为你是主人」。

博通的代表作还不止这一件：

- Wi-Fi 芯片全系闭源固件 blob，Linux 驱动（brcmfmac）要和一颗黑盒固件斗智斗勇，出了事只能等博通心情好；
- 690 亿美元收购 VMware 之后把永久许可改成订阅捆绑包，客户报告涨幅最高到 500%（个别案例 8~15 倍）。欧洲云行业组织 CISPE 一直在投诉并提起法律挑战。**2025 年 6 月 27 日，荷兰海牙地区法院裁定博通违反注意义务**——荷兰基础设施与水资源管理总局（Rijkswaterstaat）的年度费用从约 214 万欧元被抬到近 397 万欧元，法院要求博通必须在最长两年的迁移期内继续提供支持（年费 176.5 万欧元），不遵守每天罚 25 万欧元、上限 2500 万欧元。**「作恶多端」四个字在这家公司身上是有法院判决背书的。**

2014 年博通「大发慈悲」开源了一次：放出了 VideoCore IV 的架构手册和源码——**但完整驱动代码针对的是 BCM21553 手机平台，并不是树莓派现成可用的完整驱动**。树莓派基金会只好自己掏 1 万美元悬赏，请社区把这份「驴唇不对马嘴的善意」移植到树莓派的 BCM2835 上。赏金最后被社区黑客领走。

### 0.2 社区逆向史：文档是骂出来的

今天树莓派上能用的图形栈，本质上是一部社区逆向工程史：

- 早期 Herman Hermitage、phire（Scott Mansell）等人对着专利、二进制和实验寄存器，硬把 QPU 的指令编码和流水行为逆向了出来——在官方文档出现**之前**；
- Eric Anholt 被博通收编，写出了开源的 VC4/V3D Mesa 驱动，Linux 上的树莓派图形从此能用；
- OpenCL 从来没有官方支持，社区有人（doe300 的 VC4CL）几乎单枪匹马在 VC4 上逆向实现了一套 OpenCL 1.2；
- 到了 V3D 7.1 这一代，Vulkan 驱动 v3dv 由 Igalia 维护，OpenCL 靠 Mesa 的 rusticl 凑合。

**关于驱动，有个常见误会要澄清**：树莓派自己的 apt 源里**是有能用的 Mesa 的**——
`26.2.2-1~bpo13+0~rpt1`（比 Debian 官方的 25.0.7 新不少），装上就能跑 Vulkan 和 OpenGL。
它只是**老**：本文要的三样东西它都没有——① main 分支上才有的最新 v3dv；② 带本地补丁的 rusticl；
③ 采硬件计数器需要的 Perfetto。所以要自编（编译见第 1 节，那三个坑每个都值得骂一句）。

所以本文能成立，本身就得感谢这群人。**让这篇报告能测出东西的，是这些公开驱动、工具和社区工作；不能把现代驱动团队的成果都说成只有逆向猜谜。**

### 0.3 QPU：特殊架构可以有，但别让用户替它完成产品化

#### 0.3.1 先把批评对象说准

面向吞吐量的 GPU，通常靠数据并行、多个常驻执行组和调度机制来隐藏延迟。
不同厂商、不同代际的执行宽度、寄存器和指令调度差别很大，不能拿一张 NVIDIA 的框图给所有 GPU 判卷。
**静态调度、向量分多拍执行，本身都不是「伪 GPU」的证据。**
真正要问的是：应用通过公开 API 使用它，能不能得到有竞争力的吞吐，出了问题能不能定位。

#### 0.3.2 VC4 的手册，不是 Pi 5 的规格书

公开的 **VideoCore IV** 手册介绍了 16 路向量由 4 路物理单元分多拍执行、add/mul 配对、
线程切换信号和分支延迟槽等设计。这些很有家族特色，但 **Pi 4 是 VideoCore VI，Pi 5 是 VideoCore VII**，
不能把 VC4 的线程数、旁路、访存规则整套搬过来。

本地 Mesa 也明确区分两条编译器路径：VC4 在 `src/gallium/drivers/vc4/`，
V3D 在 `src/broadcom/compiler/`。其中 `has_accumulators = ver < 71`
说明 Pi 5 的寄存器结构已经有变化，旧文还拿累加器旋转去解释 Pi 5 的归约，属于自己撞上自己。
**不能一边说累加器拆了，一边又说它靠那条累加器羊肠小道工作。**

#### 0.3.3 12 个 QPU，不是 12×16 路每拍全开

针对 Pi 5 编写汇编工具的 [Idein/py-videocore7](https://github.com/Idein/py-videocore7#about-videocore-vii-qpu)
使用的模型是：**12 QPU × 每 QPU 4 路物理单元 × 每路每周期 2 FLOP**。
因此 800 MHz 对应 **76.8 GFLOPS**。16 路是程序看到的向量宽度，不能直接代替物理每周期吞吐。

这解释了旧文那个「12 个 QPU 怎么才跑出 56.4」的疑惑。它仍是社区实现者给出的模型，
本文没有补测运行频率，也不把它冒充博通规格书。具体条件计算见第 6 节。
**这里值得骂的是资料难找、口径难对，不是凭空宣布十个 QPU 失踪。**

#### 0.3.4 真正摆在用户面前的限制

| 本文环境里能确认的事实 | 对计算开发的实际意义 |
| --- | --- |
| Vulkan 查询每 stage **8 个 SSBO**、**32 KiB** 共享内存 | kernel 设计和资源布局要适应较小的额度 |
| 测试后端未提供所需的矩阵/整数点积加速能力 | 不能照搬依赖这些能力的推理快路径；不能仅由 API 查询推断所有物理电路 |
| v3dv clpeak 的 FP16 与 FP32 吞吐接近 | **这条测试路径**没拿到半精度算力倍率，不能推广成所有指令形式的硬件定律 |
| V3D 7.1 的累加器模型与旧版不同 | 老手册和旧汇编经验不能照抄 |
| 本文 Mesa 路径用 CPU 时间和同步实现时间戳 | profiling 要读懂实现，不能把 job 窗口当 ALU 满载 |

没有矩阵专用加速并不意味着不能做 GPGPU；这些限制也不能单独证明某个框架必然慢几十倍。
但把它们叠在一起，至少能理解开发者为何需要额外适配。**特殊设计的成本不能只算硅片面积，
还要算有多少人得围着它改编译器、排 shader、补工具。**

#### 0.3.5 生意上的解释，也得标明是解释

自有 IP 的授权成本、既有固件与验证资产、产品目标市场，都可能影响架构选择。
这些是合理的商业分析方向，**本文没有博通内部决策资料，不能替它编会议纪要**。
换一个 Mali 也不等于自动交出启动链控制权，不能拿这种推断给博通定罪。

用户能够评价的是拿到手的产品：你当然可以有自己的设计取舍，
但当常见计算框架跑上去比 CPU 慢几十倍，不能靠一句「本来就不是干这个的」让实际支持缺口消失。
**特殊架构是厂商的自由，嫌它难用是用户的自由。**

#### 0.3.6 两个框架都慢，足以批评交付，尚不足以锁死根因

ncnn 的卷积超分和 llama.cpp 的量化模型推理，在本文配置中都明显落后于 CPU。
这让问题超出了某个模型的一次偶发慢路径，支持这样的评价：
**V3D 当前测试栈对这两类现代 GPGPU 工作负载的支持和适配很差。**

但两套后端可能共享编译器限制，也可能各自走了不合适的 kernel、同步或数据转换路径。
所以不能继续写「两个团队都慢 ⇒ 驱动无罪 ⇒ 全是硅片、永远救不了」。
**用户体验已经足够难看；想把责任拆到具体硬件单元，仍然得继续测。**

### 0.4 多媒体出身，却把 H.264 编码器也拿掉了

Pi 5 的 BCM2712 没有前代板子提供的 H.264 硬件编码器，H.264 解码改走软件，保留 HEVC 硬解。
官方认为更强的 CPU 可以承担相关工作。对需要持续转码的用户，结果很具体：
**以前能卸载的活儿，现在要占 CPU。** 至于具体增加多少功耗，本文没有测，不能直接写电费结论。

硬件编解码、3D 渲染、通用计算是不同功能；GPU 仍然能用于桌面和图形，旧文的「除了点亮 HDMI 什么也干不了」
说过了头。但站在想把板子用于推理、超分和视频处理的用户角度，
**计算路径慢，编码卸载又少一项，产品取舍确实让人很难鼓掌。**

### 0.5 连内存都要整活：四个核心，八个 NUMA 节点，大家一样远

你以为博通只在 GPU 上特立独行？2026-10-19，在本文这台 **16 GB Pi 5** 上查了一眼 `lscpu`：

```text
CPU(s):              4
Model name:          Cortex-A76
NUMA node(s):        8
NUMA node0 CPU(s):   0-3
NUMA node1 CPU(s):   0-3
...（node2 到 node6 同样是 0-3）
NUMA node7 CPU(s):   0-3
```

四个核心，八个节点，每个节点都说这四个核心是自己的。再看 `numactl --hardware`：
**每个节点约 2 GiB，节点距离矩阵从头到尾全是 10。** 没有谁近，没有谁远，整整齐齐，一碗水端平。

多路服务器的 NUMA，是告诉系统「这块内存离哪个 CPU 更近」。这块小板子的八个节点没有这样的远近关系。
答案直接写在启动参数里，连装都不装：

```text
numa=fake=8 numa_policy=interleave
```

**`fake`。是启动链自己带进来的，不是本文为了骂它起的外号。**
本机 `/boot/firmware/cmdline.txt` 没有这些手写参数，运行中的 `/proc/cmdline` 却有。
内核日志和 `numactl --show` 进一步确认，它真的在跨八个模拟节点交错分配：

```text
mempolicy: NUMA default policy overridden to 'interleave:0-7'
DMA IOMMU NUMA default policy overridden to 'interleave:0-7'

policy: interleave
interleavemask: 0 1 2 3 4 5 6 7
```

#### 为什么别人的板子不需要这出戏？

DRAM 内部有多个 bank。访问能不能分散到这些 bank，取决于**物理地址的哪些位被用来选 bank**。
选 bank 的位放得低，相邻地址就容易落到不同 bank；放得高，一大片地址就可能集中在其中一部分。
在后一种布局下，让操作系统从几个大区域轮流分配页面，就能帮助分散访问。

常见 x86 平台以及许多 ARM SoC，会通过内存控制器的通道交错、bank 地址映射等机制处理这件事；
Linux 看到一个 NUMA 节点，不妨碍底下有多个 bank 并行工作。
**ARM 是 CPU 架构，不是内存控制器统一设计规范，别把博通这套当成 ARM 的通病。**

Pi 5 的选择，树莓派官方自己写得很清楚。`SDRAM_BANKLOW` 控制 bank 位在地址中的位置，
文档列出的两种映射是：

```text
SDRAM_BANKLOW=1：{bank[3:1], row, bank[0], column}  ← Pi 5 默认推荐值
SDRAM_BANKLOW=3：{bank[3], row, bank[2:0], column}  ← Pi 4 默认推荐值
```

注意左边那些高位的 bank 位。Pi 5 把更多 bank 选择位放在地址高处，再借 Linux 的模拟 NUMA，
配合交错分配，把页面分散到相应区域。**别人通常藏在内存地址映射下面的事，在这里一路冒泡到了操作系统的 NUMA 拓扑里。**
以上默认值、位布局和启动器自动添加 `numa=fake=<n>` 的行为，均见
[树莓派官方 `SDRAM_BANKLOW` 文档](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#SDRAM_BANKLOW)。
八节点是本文机器的实际结果，不是对所有容量、所有固件版本的承诺。

#### 最有节目效果的，是官方的后半句

同一份文档还明确说明：**Pi 5 的内核如果不支持 NUMA，性能会受损；手动选 `SDRAM_BANKLOW=3`
可以缓解，但要追求最高性能，应确保 NUMA 可用。**

于是，想让一台四核单板机的内存按官方推荐方式发挥性能，软件栈得认识八个模拟节点，
还要默认跨节点交错分配。你换内核时，除了驱动，还得关心这套内存策略是否跟上。
**买板子时没说送多路服务器知识，开机以后倒是把 NUMA 选修课给你排上了。**

这套适配确实能工作，本机也正在正常使用。离谱之处在于：**一套底层内存布局的特殊要求，
变成了启动器、内核配置、内存分配策略共同遵守的约定，还直接出现在用户看到的拓扑里。**
GPU 的特殊性要靠编译器和驱动照顾，内存的特殊性要靠模拟 NUMA 照顾，软件栈一路给硬件打圆场。

证据边界也放在这里：本次只读核验了配置和分配策略，**没有关闭 NUMA 做性能对照**，因此不编收益百分比；
没有博通的设计决策资料，也不能据此断言它「做不了硬件交错」或认定这完全是博通单方面的决定。
能确认的是，BCM2712 上树莓派采用了这套软硬件配合方案，官方还专门写了不配合的性能后果。
**光把事实摆出来，就已经够与众不同了，用不着再给它编罪名。**

复查命令如下，2026-10-19 的本机输出保存在
[`data/numa-pi5-20261019.txt`](data/numa-pi5-20261019.txt)：

```sh
lscpu
numactl --hardware
numactl --show
cat /proc/cmdline
cat /boot/firmware/cmdline.txt
journalctl -k -b --no-pager -g 'NUMA default policy'
```

这些记录说明内存配置，**不用于解释前面 GPU 慢 21–65 倍的原因**，也没有改动原有 GPU 实测结果。

---

## 1. 环境与版本（复现所需的全部事实）

### 硬件

| 项 | 值 | 怎么查 |
| --- | --- | --- |
| 板子 | Raspberry Pi 5 Model B **Rev 1.1** | `/proc/cpuinfo` |
| SoC / GPU | BCM2712 / **V3D 7.1.10.16** | `sudo cat /sys/kernel/debug/dri/*/v3d_ident` |
| GPU 结构（寄存器读出） | Slices **3**、TMUs **3**、QPUs **12** | 同上 |
| CPU | 4× Cortex-A76 | `nproc` |
| CPU 频率 | **2800 MHz（超频）** | `vcgencmd measure_clock arm` |
| 超频设置 | `arm_freq=2800`、`over_voltage_delta=50000` | `/boot/firmware/config.txt`（2026-10-07 起永久配置；旧测试曾用 tryboot） |

> ⚠️ CPU 是超频状态，这是 CPU 侧比较的有利条件，不能说成「让了 GPU」。GPU 后端的端到端表现也可能受主机侧速度影响。复现时应记录 CPU / V3D 的负载频率，不能假定结果随频率严格线性折算。

### 系统 / 工具链

| 项 | 版本 |
| --- | --- |
| OS | Debian GNU/Linux **13 (trixie)**，aarch64 |
| 内核 | **6.18.50+rpt-rpi-2712** |
| clang / gcc | **22.1.8** / 14.2.0 |
| Python | **3.13.5** |

### Mesa 驱动

**先说结论：apt 有，但是老。** 树莓派自己的源提供 `26.2.2-1~bpo13+0~rpt1`
（Debian 官方只有 25.0.7），装上就能用 Vulkan / OpenGL。本文要自编是因为它给不了三样东西：
main 分支上最新的 v3dv、带本地补丁的 rusticl、以及采硬件计数器需要的 Perfetto。

```sh
apt-cache policy libgl1-mesa-dri     # → 26.2.2-1~bpo13+0~rpt1（树莓派源）
```

自编的这个：

```
版本串：26.3.0-devel (git-a5d39a4b74)      ← 由 clinfo / clpeak --list-devices 读出
Vulkan API：1.3.363 (v3dv)    OpenCL：rusticl
```

从 mesa **main 分支**（gitlab.freedesktop.org/mesa/mesa）自编，**必须 release**：

```
git fetch --depth=1 origin main && git reset --hard FETCH_HEAD   # a5d39a4b74
CC=clang-22 CXX=clang++ meson setup build --buildtype=release \
  -Dcpp_rtti=false -Drust_std=2021 \
  -Dprefix=/usr/local -Dlibdir=lib/aarch64-linux-gnu \
  -Dgallium-drivers=v3d,zink,llvmpipe -Dvulkan-drivers=broadcom,swrast \
  -Dgallium-rusticl=true -Dglx=disabled -Dopengl=true -Degl=enabled
ninja -C build -j4 && sudo ninja -C build install && sudo ldconfig
```

要采硬件计数器还需 **`-Dperfetto=true -Dallow-fallback-for=perfetto`**（会自动下载 perfetto SDK）。

三个坑（区分应用违规与驱动配置，不能都算到博通头上）：

1. 本文构建用 **`--buildtype=release`**。旧 ncnn 的 `descriptorCount=0` 会触发 v3dv 断言，根本原因是应用违反 Vulkan 约束；关闭断言不是修复。ncnn 上游已经修正这类空描述符计数，本文版本已包含修正，不能把这段历史当成驱动无故拒绝合法程序。
2. `-Dcpp_rtti=false` 必须加：自编 clang 用的 LLVM 没开 RTTI，否则 meson 直接报错。
3. **`rusticl` 不设 `RUSTICL_ENABLE=v3d` 等于没设备**：`clinfo -l` 只显示平台、一个设备都没有。不设这个变量，OpenCL 在这块板子上就等于不存在。查当前生效的驱动就靠这个：

```
RUSTICL_ENABLE=v3d clpeak --list-devices   # 会打印 Driver : 26.3.0-devel (git-...)
```

### 测量工具

| 工具 | 版本 / 来源 | 用途 |
| --- | --- | --- |
| **clpeak** | **2.1.4**，官方 release 二进制 `clpeak-2.1.4-linux-aarch64.zip`（github.com/krrishnarraj/clpeak） | fp32/fp16/int 测试吞吐（不是事件语义标定） |
| **ncnn** | git **`c6b351b`**（2026-09-24），`NCNN_VULKAN=ON`、`CMAKE_BUILD_TYPE=Release` | 超分负载（卷积） |
| **llama.cpp** | build **`b11146`**（= v0.5.0 release 对应的 nightly）。**两个包都要**：`llama-b11146-bin-ubuntu-vulkan-arm64.tar.gz` 和 `llama-b11146-bin-ubuntu-arm64.tar.gz`（纯 CPU） | LLM 负载（matmul） |
| **Perfetto CLI** | **v56.1-c794fceab**，`linux-arm64.zip`（含 `traced`/`perfetto`/`trace_processor_shell`） | 采硬件计数器 |
| **pps-producer** | 由上面那个 mesa 构建产出（`build/src/tool/pps/pps-producer`） | 读 V3D 硬件计数器 |

模型：

- `realesr-animevideov3-x4.param/.bin` —— Real-ESRGAN portable 包里的
- `Qwen3-0.6B-Q4_K_M.gguf` —— `huggingface.co/unsloth/Qwen3-0.6B-GGUF`

---

## 2. 怎么测的

### 2.1 fp32 吞吐（clpeak）

```
# GPU（Vulkan / v3dv）
RUSTICL_ENABLE=v3d clpeak --vulkan --vk-device 0 --single-precision-compute
# GPU（OpenCL / rusticl）
RUSTICL_ENABLE=v3d clpeak --opencl --single-precision-compute
# CPU（★必须固定迭代数，见坑 1）
clpeak --cpu --single-precision-compute --iters 20000
```

### 2.2 GPU 时间线 —— 不需要 Perfetto

v3dv 的 u_trace 支持 `print_csv`，而且**不在 `#ifdef HAVE_PERFETTO` 里**，任何构建都能用：

```
MESA_GPU_TRACES=print_csv MESA_GPU_TRACEFILE=/tmp/gpu.csv <vulkan 程序>
```

输出 job 相关的纳秒时间戳（`begin_job_csd` / `end_job_csd` / `begin_job_tfu` / `begin_cmdbuf`）。`scripts/analyze-gpu-trace.py` 计算记录窗口的区间并集和间隙，**不能直接把并集叫作 GPU 硬件忙碌时间**。

> 本文驱动使用依赖同步后的 CPU 时间戳。软件时间戳会受调度、同步和采样位置影响；
> 本地当前源码也不能自动代表旧 trace 采集时的全部实现。第 2.4 节说明这些数据能回答什么。

### 2.3 硬件性能计数器 —— `gpu.counters.v3d`

```
# 1) 起 PPS 守护进程（要 root）+ 起 traced（socket 指到 /tmp，免得建 /dev/socket）
export PERFETTO_PRODUCER_SOCK_NAME=/tmp/perfetto-producer \
       PERFETTO_CONSUMER_SOCK_NAME=/tmp/perfetto-consumer
traced --background
sudo V3D_DS_COUNTER="cycle-count,compute-active-cycles,core-active,TMU-active-cycles,\
TMU-stalled-cycles,L2T-stalls-mem,L2T-total-cache-hit,L2T-total-cache-miss,\
QPU-total-instr-cache-hit,QPU-total-instr-cache-miss" \
  pps-producer &

# 2) 采（config 里要有 data_sources { config { name: "gpu.counters.v3d" } }）
perfetto --txt -c trace.cfg -o out.proto &

# 3) 跑负载；然后导出采样并分析
trace_processor_shell -q scripts/q-counters.sql out.proto > counters.csv
python3 scripts/analyze-counters.py counters.csv
# 复算旧报告的裁剪口径：另加 --skip-first-seconds 1
```

计数器名字取自 mesa `src/broadcom/common/v3d_performance_counters.h` 里 `V3D_VERSION >= 71` 那张表。`SET_GLOBAL` ioctl 是让**系统级**守护进程能计数（而非只算单个上下文）的关键；内核不支持时 pps 会直接 FATAL 提示。

### 2.4 测量的地基：API、计数器、采集窗口是三件事

**软件能返回纳秒时间戳，不等于芯片暴露了自由运行的硬件 GPU 时钟。**
本地 Mesa 的 `V3DDriver::gpu_timestamp()` 使用 CPU boot time，
`cpu_gpu_timestamp()` 返回不支持；v3dv 的查询通过内核 CPU job 等待依赖后写入时间戳。
这能说明所用实现，不能仅凭一个未实现的钩子，证明硅片里绝对没有任何计时电路。

更正旧版另一处过头的说法：**端到端耗时和吞吐本身就是测量目标，不是「上界」。**
需要谨慎的是把软件包围的窗口当成纯 GPU 执行时间。两个 CPU 采样点之间可能混有等待，
也可能有边界延迟；没有验证采样位置之前，连「一定是纯执行时间的严格上界」也不能一概而论。

#### 计数器还有采集覆盖问题

2026-10-19 复查树莓派 `rpi-6.18.y` 分支，发现 `PERFMON_GET_VALUES` 调用
`v3d_perfmon_stop(..., true)`：保存计数后关闭计数使能、清空 `active_perfmon`；
调度器的 `v3d_switch_perfmon()` 可在后续 job 运行时重新启动。
**读计数器并不是始终无副作用地拍张照片。** 高频读取可能只覆盖一个长 job 的前一小段。

本次核对的分支源码还没有与旧采集时加载的内核二进制逐项匹配，因此这是明确的**采集风险**，
不是声称已经重现当时丢掉了多少周期。旧 clpeak 记录在约 26 秒窗口里只增长约 1400 万个 cycle-count，
也提醒我们不能把窗口长度当成完整计数时长。源码版本、函数和复查记录见
[`data/methodology-review-20261019.md`](data/methodology-review-20261019.md)。

| 数据 | 可以说什么 | 不能直接说什么 |
| --- | --- | --- |
| ncnn / llama.cpp 端到端时间、t/s | 本文配置下哪个后端更快 | 所有版本的性能、唯一硬件根因 |
| clpeak GFLOPS | 特定测试项、后端的实测吞吐 | 理论峰值、完美驱动上限 |
| 软件 trace 窗口及其并集 | 记录中 job 窗口密集、软件视角间隙小 | GPU 全程满载、排除提交与同步成本 |
| compute-active / cycle-count | **采到的周期里**两个事件的比值 | 墙钟利用率、所有 QPU 都在做有效运算 |
| L2T 命中与 stall 事件 | 对应缓存和具体停顿事件的样本 | 排除所有访存延迟和带宽瓶颈 |
| 指令缓存 hit + miss / cycle-count | 指令缓存访问事件密度 | 指令发射数、add/mul 槽利用率 |

**比值不用墙钟，不代表它自动免疫于事件定义错误和采集覆盖偏差。**
旧文把这个漏洞当成证据最硬的地方，恰好把自己绊倒了。

---

## 3. 结果：支持差已经测到了，差在哪里仍要拆

### 3.1 ncnn：能跑 Vulkan，超分却慢 21.4 倍

在同一块板子、同一超分模型和 **320×136** 输入上，CPU **493 ms**，Vulkan **10,575 ms**。
这是用户选择后端时直接面对的差距。Vulkan 路径能执行计算，却没有交付加速。

CPU 路径可能使用 Winograd 等不同算法，不能用同一份「直接卷积 FLOP 数」去算两边 ALU 利用率。
算法和 kernel 适配本身也是后端支持的一部分；它们会影响性能，也正是不能把全部差距算到硅片头上的原因。

旧时间线中 CSD 等窗口密集，软件窗口并集约 **98.7%**、窗口间隙约 **1.3%**。
保留这个观察，撤回「因此 GPU 无空闲、提交开销已排除」的推断。

### 3.2 23.5%：一个真实算式，曾经配了一段错误解释

| 旧记录 | (指令缓存 hit + miss) / cycle-count |
| --- | --- |
| clpeak | **2.0014** |
| ncnn | **0.4696** |
| ncnn / clpeak | **约 23.5%** |

Mesa 和内核对这两个事件的命名是 **所有 slice 的指令缓存 hit / miss**。
本文未标定它与实际执行指令、预取、缓存请求粒度之间的对应关系；
clpeak 算出一个接近整数的数，也不足以完成这种标定。

另外，QPU 的 add/mul 可以打包在同一条机器指令中。
**「2 个缓存事件/周期」不是「每周期执行 2 条指令」，更不是「两个 ALU 槽打满」。**

因此 **23.5% 只保留为旧采集窗口中的事件密度相对值**。
“76.5% 发射槽空着”“每 2.1 周期才执行一条”“吞吐还能提高 4.26 倍”均不成立。
而且事件密度还可能受第 2.4 节的采集覆盖影响，不能直接用它给根因定案。

### 3.3 两个框架的旧计数器记录：线索，不是排除法判决

| 旧记录中的比值 | ncnn | llama.cpp |
| --- | --- | --- |
| compute-active / cycle-count | 99.86% | 96.29% |
| L2T-stalls-mem / cycle-count | 约 0.03% | 接近 0% |
| L2T hit / (hit + miss) | 97.56% | 约 96.75% |

这组样本值得继续研究，但 compute job 活跃不等于 ALU 有效工作，L2 命中也不等于访问没有延迟。
`L2T-stalls-mem` 只对应一个具体事件，不能替所有访存停顿发免罪证。
要进一步分离执行、TMU 等待、指令依赖、寄存器压力与同步，需要验证采集窗口，再看 shader 和更多事件。

原始输出留在 `data/counters-*.txt`，**旧分析器附带的“2 个 QPU”“发射率”等文字已经作废**。
数据文件保留原样，免得更正时顺手把历史证据也洗了。

### 3.4 llama.cpp：换成 LLM，支持缺口依然明显

| | 纯 CPU 构建 | Vulkan (V3D) | CPU 快多少 |
| --- | --- | --- | --- |
| pp64 | **143.95 t/s** | **2.22 t/s** | **64.8×** |
| tg16 | **21.05 t/s** | **0.32 t/s** | **65.8×** |
| avg_cpu | 276% | 32% | — |

这里 CPU 基线使用独立的 CPU-only 构建，避免把混有 Vulkan 后端的 `-ngl 0` 当成纯 CPU。
旧 trace 有 **3612 个 CSD job**，平均记录窗口约 **42.2 ms**，窗口并集约 **99.9%**。

旧文把平均 job 窗口套进一个假定形状的 matmul，算成 **0.8 GFLOPS**。
没有逐个核对 dispatch 的形状、量化/反量化、实际操作和采样边界，这不够资格叫实测 matmul 吞吐，现撤回。
**每秒 0.32 个 token 已经够难看，无须再给它凑一个不可靠的 FLOPS。**

ncnn 和 llama.cpp 是不同负载、不同后端实现，都落后于 CPU，说明本文测试栈的 GPGPU 实用性确实很差。
这不是对两个上游项目的普遍评价，也不能区分到底多少来自硬件、Mesa 编译器或框架适配。

### 3.5 如果明天驱动、编译器和手册都改善，能救多少？

**目前不知道。旧版声称“完美驱动依然慢 5 倍”的上界推导撤回。**

它把指令缓存事件误当发射率，假定吞吐线性增长，还把 ncnn 的比值直接套到 llama.cpp。
即使前提没有错，不同算法、融合、减少搬运或同步，也可能改变每个结果需要的指令数；
clpeak 的已知最好成绩同样不是硬件不可逾越的天花板。

资料公开、编译器改进、面向 QPU 的 kernel 都可能带来收益，本文没有证据替它们规定“最多改善几个百分点”。
社区的 py-videocore7 已有手写 QPU SGEMM 示例，但其成绩不能直接替代本文 ncnn 或 llama.cpp 的测试。

**值得骂的是现在交付得差，不是擅自证明它永远不可能变好。**
现代 GPGPU 支持不只是一张 API 版本证书，还包括可用的计算工具、稳定的 profiling、编译器和常见算子的适配。
对本文这两类需求，用户得到的是慢几十倍，以及一堆需要自己查源码的解释题。
**博通的架构可以与众不同，但“能提交计算”离“好用的计算平台”差多少，这笔账不能让社区替它结清。**

---

## 4. 撞过的坑（方法学，都很隐蔽）

1. **clpeak 的 CPU 后端自动迭代校准在 MT 档是坏的**——同一会话、同一时钟连跑三次 `float MT` = 173.83 / **112.14** / 173.64；加 `--iters` 固定后 175.98 / 175.96 / 175.16。**凡引用 CPU MT 数字必须固定迭代。**（GPU 侧自动校准可靠，重复性 0.05% 内。）
2. **`llama-bench -ngl 0` 不是 CPU 基线**——用 Vulkan 版二进制时后端仍会插手：pp64 只有 2.27 t/s vs **纯 CPU 版 143.95 t/s（差 63×）**，`avg_cpu` 也从 276% 掉到 124%（4 线程只吃 1.24 核，是很好的判据）。**测纯 CPU 必须用 CPU-only 构建。**
3. 两个 tarball 解压目录**同名**（都叫 `llama-b11146/`）会互相覆盖；llama.cpp **自动加载目录里所有 `libggml-*.so`**——CPU 目录残留一个 `libggml-vulkan.so` 就把 Vulkan 挂上了。
4. **已知快的 kernel 不是事件语义的标定器**：比值接近整数，不能证明缓存事件就是指令发射。要有可控指令数、重复次数和覆盖完整的实验。
5. **`V3D_DS_COUNTER` 不设 = 静默不采**（驱动直接 return，不报错）。
6. **空闲时计数不涨，不能只归因于时钟门控**：计数使能、job 切换和读取副作用都要核对。`compute-active-cycles / cycle-count` 只是所采周期中的比值，不能直接代表墙钟占用率。
7. **计数器有启动瞬态**：perfmon 刚创建时首次读数带一个大跳变（实测第 0 秒的 `cycle-count` 增量是平稳期的 ~40 倍）。把它包进统计窗口会把比值稀释掉（llama.cpp 的 compute-active 会从 96.29% 掉到 82.10%）。`analyze-counters.py` 旧版会跳过第一个采样秒。
8. **v3dv + perfetto 的上游 bug**：`perfetto.h` 用 `#ifdef THREAD_SANITIZER` 判断，而 mesa 无条件发 `-DTHREAD_SANITIZER=0` → 去找 `<sanitizer/tsan_interface.h>`，不带 sanitizer 头的 clang（比如自编的）直接编译失败。当时核对的 main 仍发 `=0`，当前版本需重新确认。

---

## 5. 结论：难用已经够骂，不必伪造一个硬件极限

**在本文记录的环境和任务上，想更快地做超分和 LLM 推理，选 CPU。**

- ncnn 超分的 V3D Vulkan 路径慢 **21.4 倍**；llama.cpp 的 pp64 / tg16 慢 **64.8 / 65.8 倍**。
- clpeak 的 FP32 最好实测约 **56.4 GFLOPS**，约为超频四核 CPU 实测的 **1/3**；这是测试比较，不是硅片上限。
- 所测 v3dv FP16 路径没体现出明显的算力倍率，资源额度和未暴露的加速能力也限制了快路径选择。
- 工具和资料还需要格外小心：缓存事件不能冒充发射，软件时间戳不能冒充硬件忙碌窗口。

这些足以说明 **本文测试的 V3D 软硬件栈，对 ncnn 和 llama.cpp 代表的现代 GPGPU 任务支持很差**。
硬件、编译器、驱动、框架适配各有多少责任仍待测。未来 kernel 能变快，
不等于今天的糟糕体验不存在；今天慢几十倍，也不等于已经证明明天永远救不了。

部分场景还可以用 GPU 卸载 CPU 工作，但要接受实测延迟。
例如旧 ncnn 记录约为 Vulkan **1% CPU**、CPU 路径 **313% CPU**；llama.cpp 则是 **32% 对 276%**，
不能把前一组的 1% 套到所有工作负载。桌面渲染和其他图形用途也不能被这两项计算测试一笔抹掉。

而内存这一边，四个核心照样能长出八个模拟 NUMA 节点，靠启动器自动传参、内核支持和交错分配
一起照顾 DRAM 的地址布局。它和 GPU 算得慢是两件事，放在同一块板子上，却有一种熟悉的气味：
**每深入一层，就多一条这颗芯片专属的规矩；最后都得由软件去记，由用户去查。**

博通的与众不同，不用靠发布会上的形容词。你只要想知道 GPU 为什么这么慢、CPU 为什么有八个内存节点，
就会亲自把这份「不同」从头体验一遍。**能把用户用成半个驱动工程师，也算一种本事。**

---

## 6. 已澄清的误会与真正未解的问题

### 6.1 12 个 QPU 与 56.4 GFLOPS，并无数量级矛盾

`v3d_ident` 和 Mesa 的 `NSLC × QUPS` 都支持 **3 slice × 4 QPU = 12 QPU**。
错的是把 16 路逻辑向量宽度当成每周期物理吞吐。
按 [py-videocore7 的社区模型](https://github.com/Idein/py-videocore7#about-videocore-vii-qpu)：

```text
峰值模型 = 12 QPU × 4 路物理单元 × 2 FLOP/周期 × V3D 频率
```

| 条件频率 | 模型峰值 | 56.36 GFLOPS 相对比例 |
| --- | --- | --- |
| 600 MHz | 57.6 GFLOPS | 97.8% |
| 800 MHz | 76.8 GFLOPS | 73.4% |

**这只是条件计算，不是补测结果。** 旧实验没有同步保全 V3D 负载频率，不能挑一个比例宣布结案。
也不能根据“12 QPU”倒推指令缓存事件应该是 12/周期。
旧文「2.00 只有理论上限 17%」的说法作废。

### 6.2 真实 kernel 为什么慢：还缺可区分原因的实验

需同时检查 shader 指令、寄存器占用、workgroup 布局、访存与依赖、同步和数据转换。
ncnn 的 CPU / GPU 算法差异、llama.cpp 的量化 kernel 选择，也需要单独核对。
旧计数器没有排除这些方向，不能提前写成“只有依赖链或线程数二选一”。全窗口与裁剪窗口的差异也须保留：llama.cpp 的 compute-active 比值分别为 **82.10% / 96.29%**，不能默认挑后者作为全程画像。

### 6.3 旧计数器采到了多大一部分工作

需匹配当时内核与 Mesa 的确切版本，验证每次读取的停止/重启行为，再用已知长度的 job
比较读取频率和最终计数。只拿同一份有覆盖疑问的数据做比值，不能消除偏差。
并发 clpeak 的计数器 trace 当时也未采到；两个实例各拿一半吞吐，说明它们共享了瓶颈资源，
**不等于证明每个 QPU 或每条 ALU 都打满**。

### 6.4 软件时间线和硬件执行怎样对齐

需要确认每个时间戳的依赖、提交与完成位置，并核对 profiling 本身的扰动。
现有 trace 无法直接给出精确墙钟 ALU 利用率；“本机在任何条件下都测不了”也超出了证据。
准确结论是 **本文目前的采集方法没有回答这个问题**。

---

## 7. 复现

```
scripts/
  run-clpeak.sh            跑 clpeak 全量并落盘（含驱动版本）
  analyze-gpu-trace.py     解析 MESA_GPU_TRACES=print_csv，算软件记录窗口并集 / job 分布
  analyze-counters.py      解析硬件计数器采样，算所采事件比值（不推断发射率）
  q-counters.sql           从 Perfetto trace 里导出计数器采样
data/
  {baseline,release}-{opencl,vk,cpu}.json   clpeak 原始结果
  gpu-trace-ncnn.csv / gpu-trace-llamacpp.csv      GPU job 时间线原始数据
  counters-ncnn-qpu.txt                            旧缓存事件密度 0.4696；附带解释已作废
  counters-clpeak-control.txt                      旧缓存事件密度 2.0014；不是发射上限
  counters-llamacpp.txt
  methodology-review-20261019.md                  口径更正、源码与采集风险
  numa-pi5-20261019.txt                            四核八节点的本机记录
```

## 8. 参考

### QPU 微架构与性能模型

- [Idein/py-videocore7](https://github.com/Idein/py-videocore7#about-videocore-vii-qpu)：Pi 5 汇编工具作者给出的吞吐模型，属于社区实现资料。
- **VideoCore IV 3D Architecture Reference Guide** —— 唯一的公开 QPU 手册（2014，讲的是 BCM21553，
  不是树莓派在用的芯片）：<https://docs.broadcom.com/docs/12358545>
- Herman Hermitage 的 VideoCore IV wiki（社区逆向的一手记录）：
  <https://github.com/hermanhermitage/videocoreiv/wiki/VideoCore-IV-3d-Graphics-Pipeline>
- 树莓派论坛的历史 QPU 线程讨论（不能直接用作 Pi 5 的线程规格）：
  <https://forums.raspberrypi.com/viewtopic.php?t=77819>

### 内核、计数器与方法学更正

- [2026-10-19 复查记录](data/methodology-review-20261019.md)：内核源码版本、计数器读取与调度的关系，以及撤回的推论。

### 内存布局与模拟 NUMA（0.5 节）

- [树莓派官方 `SDRAM_BANKLOW` 文档](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#SDRAM_BANKLOW)：bank 地址位布局、Pi 4 / Pi 5 推荐值、启动器添加模拟 NUMA 参数，以及内核无 NUMA 支持时的性能说明（2026-10-19 核对）。
- [本机 NUMA 核验输出](data/numa-pi5-20261019.txt)：16 GB Pi 5，四核、八节点、距离矩阵、实际分配策略及相关内核配置。

### 博通 / VMware

（下面几条）
- 荷兰海牙地区法院裁定博通须在迁移期继续支持：[Network World](https://www.networkworld.com/article/4015489/dutch-court-forces-broadcom-to-support-vmware-migration-after-85-price-hike-backlash.html) / [The Register](https://www.theregister.com/software/2025/06/30/vmware-must-support-dutch-govt-agencys-migration-judge/)
- Broadcom–VMware 许可变更与 CISPE 投诉：[SDxCentral](https://www.sdxcentral.com/news/broadcom-faces-european-court-blow-over-vmware-support-cutoff/)

## License

MIT —— 见 [LICENSE](LICENSE)。数据与文字随便用，能注明出处更好；
如果这些测量帮你省了几天，或者你发现了本文的错误，欢迎开 issue。
