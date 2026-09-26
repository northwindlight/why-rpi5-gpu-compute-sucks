# why-rpi5-gpu-compute-sucks

**为什么树莓派 5 跑 GPU 计算就是屎 —— 一次有完整证据链的实测**

树莓派 5 的 VideoCore VII（V3D 7.1）在纸面上有个 GPU。真拿它跑计算，结论是两句话：

> **第一，它的力气本来就小 —— 实测 fp32 吞吐只有 4 核 CPU 的 1/3；**
> **第二，真实负载连这 1/3 都拿不到：指令发射率只有「同机器上已知最快 kernel」的 23.5%。**

每一条都是实测，**不依赖任何理论峰值假设**（原因见文末「未解的问题」——那本身就是一个笑话）。

## TL;DR

| | 4×A76 (CPU) | V3D 7.1 (GPU) |
| --- | --- | --- |
| fp32 实测吞吐（clpeak） | **176 GFLOPS** | **56.4 GFLOPS**（CPU 的 1/3） |
| ncnn 超分（320×136） | **493 ms** | 10,575 ms（**慢 21.4×**） |
| llama.cpp pp64 | **143.95 t/s** | 2.22 t/s（**慢 64.8×**） |
| 指令发射率（取指/周期） | — | clpeak **2.00** → ncnn **0.47** |

同一个 GPU 上，clpeak 的纯 FMA kernel 能跑到 56.36 GFLOPS，而且单实例就已经把 GPU 打满（并发跑两个实例，各自只拿到一半：28.19 / 28.18）。所以**不是硬件坏了、也不是驱动烂——是真实负载在这种机器上根本喂不出吞吐**。

翻译成人话：这块 GPU 就像一台怠速拉到红线、却挂在空挡上的发动机。转速表（compute-active）99.86%，车速（吞吐）是隔壁自行车的三分之一。

---

## 0. 前情提要：这块 GPU 是什么来路

要理解这坨东西为什么长成这样，得先讲讲博通（Broadcom）。

### 0.1 博通作恶简史

VideoCore 原本是英国小公司 Alphamosaic 的技术，2004 年被博通买走，从此进入博通的标准剧本：**闭源、NDA、手册按关系发放**。在相当长的时间里，VideoCore 的 datasheet 是「签了保密协议也不一定能拿到」的东西——树莓派用户用的是全世界装机量最大的「开放硬件」之一，但 SoC 里最重要的那块芯片，文档是保密的。

更绝的是树莓派 1~4 代的启动顺序：**整台机器是 GPU 先醒的**。上电后 VideoCore 的 VPU 先跑起来，执行一段闭源固件，由它把 ARM 核从复位里放出来。你买的「ARM 开发板」里，ARM 其实是 GPU 的房客。CPU 要发个邮箱消息（mailbox）才能请 GPU 帮忙设置时钟、分配显存、开关电源域。这个架构浪漫地诠释了什么叫「你以为你是主人」。

博通的代表作还不止这一件：

- Wi-Fi 芯片全系闭源固件 blob，Linux 驱动（brcmfmac）要和一颗黑盒固件斗智斗勇，出了事只能等博通心情好；
- 690 亿美元收购 VMware 之后把永久许可改成订阅捆绑包，客户报告涨幅最高到 500%（个别案例 8~15 倍）。欧洲云行业组织 CISPE 一直在投诉并提起法律挑战。**2025 年 6 月 27 日，荷兰海牙地区法院裁定博通违反注意义务**——荷兰基础设施与水资源管理总局（Rijkswaterstaat）的年度费用从约 214 万欧元被抬到近 397 万欧元，法院要求博通必须在最长两年的迁移期内继续提供支持（年费 176.5 万欧元），不遵守每天罚 25 万欧元、上限 2500 万欧元。**「作恶多端」四个字在这家公司身上是有法院判决背书的。**

2014 年博通「大发慈悲」开源了一次：放出了 VideoCore IV 的架构手册和源码——**但给的是 BCM21553，一颗和树莓派没有任何关系的手机芯片**。树莓派基金会只好自己掏 1 万美元悬赏，请社区把这份「驴唇不对马嘴的善意」移植到树莓派的 BCM2835 上。赏金最后被社区黑客领走。

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

所以本文能成立，本身就得感谢这群人。**没有社区逆向，连「证明它是屎」的仪器都不存在。**

### 0.3 QPU：一种为画三角形而存在的「通用计算单元」

这块 GPU 的计算单元叫 QPU（Quad Processing Unit）。名字里就藏着它的出身：**quad，四个像素**。它是给片元着色器设计的，「通用计算」属于顺便。

**先把一个常见误会掐死**：Pi 5 是 **VideoCore VII（V3D 7.1）**，
跟网上能查到的那些 QPU 细节（几乎全部出自 **VideoCore IV / VC4** 那份唯一公开的手册）**不是同一颗核**。
两者是**同一个产品家族的演化**，不是同一套设计：

| 证据 | 说明 |
| --- | --- |
| mesa 里 **VC4 有自己的 QPU 编译器**（`src/gallium/drivers/vc4/vc4_qpu.c`、`vc4_qpu_schedule.c`、`vc4_qpu_disasm.c`…），**V3D 有完全另一套**（`src/broadcom/compiler/` 里的 `vir_*` / `v3d_compiler`），两边**互不引用** | ISA 与编译器是分开的两套 |
| mesa 的 V3D 驱动只认 `ver=42`（Pi 4）和 `ver=71`（Pi 5），而 `devinfo->has_accumulators = devinfo->ver < 71` | 累加器在 **V3D 4.2 上还在**，到 **7.1 才被去掉** —— 这条演化线有硬证据 |

所以 VC4 手册只能当「设计思路的参考」，**绝不能当 Pi 5 的规格书**。那本手册（2014 年，
讲的还是 BCM21553）里的 VC4 QPU 长这样：16 路 SIMD 但**内部是 4 路复用 4 个周期**、
add/mul 双发射、寄存器堆写入后下一拍不能读所以要靠 **5 个累加器 r0–r5** 走旁路、
分支有 3 个延迟槽。**这些是树莓派 1~4 那一代的故事**——Pi 4 的 V3D 4.2 都还有累加器，
到了 Pi 5 的 7.1 才没有。

**而博通没给 V3D 7.1 的公开手册**（传统艺能，见 0.1）。本文只用从驱动**实测**拿得到的 V3D 7.1 事实
（都是设备查询结果，不是推测，也不是从 VC4 手册抄的）：

| 项 | 值 | 来源 |
| --- | --- | --- |
| warp / subgroup 宽度 | **16** | llama.cpp Vulkan 设备查询 |
| 共享内存 | **32 KB** | 同上 + `vulkaninfo` |
| int dot / 矩阵单元 | **都没有** | 同上 |
| 每 stage SSBO | **8 个** | `vulkaninfo` |
| GPU 时钟寄存器 | **没有** | mesa 源码注释 |
| QPU 数 | **12**（Slices 3） | `v3d_ident` |
| 累加器 | **没有** | mesa：`ver < 71` |

★ 最后两条值得单独说。VC4 手册说树莓派的 VC4 是「**3 个 slice、每 slice 4 个 QPU**」= 12 个 QPU；
而 `v3d_ident` 在 V3D 7.1 上报的同样是 **Slices 3 / QPUs 12**。这个数字直接决定「这 GPU 理论上有多强」，
**但它跟实测值对不上**——详见第 6 节，那也是本文开头说「不依赖理论峰值」的原因。

一句话总结：**这是一颗 2000 年代手机多媒体芯片家族的传人**——名字、哲学、连累加器这种细节都一路传下来，
但每一代都重新设计过。它的祖宗十八代都在解码 MP3 和画 2D 菜单，
「GPGPU」这个概念从头到尾就不在它的基因里。

### 0.4 压死骆驼的最后一把刀：Pi 5 把硬件编码器也砍了

历代树莓派（1 到 4）的 VideoCore 里都带一个 H.264 硬件编码器。Pi 5 的 BCM2712 **把这个编码器整个砍掉了**——没有硬件编码器，H.264 硬解也没了，只剩一个 HEVC 解码器。

树莓派官方的白皮书对此的解释堪称行为艺术：「没关系，软件编码够用了，x264 单核就能压 1080p30」。翻译一下：**硅片面积省下来的钱进了博通的口袋，电费账单寄给你。**

所以这块 GPU 现在的完整履历是：算力是 CPU 的 1/3，真实负载拿不到这 1/3，编码器被砍，解码只剩 HEVC，矩阵单元没有，OpenCL 是社区赏的。**它现在真的什么用都没有了**——除了点亮 HDMI。

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
| 超频设置 | `arm_freq=2800`、`over_voltage_delta=50000` | `/boot/firmware/tryboot.txt` |

> ⚠️ CPU 是超频状态。要复现 CPU 侧数字请保持一致或按比例折算；GPU 侧数字不受 CPU 超频影响。也就是说，**CPU 还是让了 GPU 的**——超频后的 CPU 打默认频率的 GPU，赢得更多。

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

三个坑（都踩过，都是血泪）：

1. **`--buildtype=release` 才有 `-DNDEBUG`**。默认的 `debugoptimized` 断言是开着的——v3dv 的 `assert(pPoolSizes[i].descriptorCount > 0)` 会直接 abort。用 debug 构建跑 Vulkan，程序不是慢，是**当场去世**。
2. `-Dcpp_rtti=false` 必须加：自编 clang 用的 LLVM 没开 RTTI，否则 meson 直接报错。
3. **`rusticl` 不设 `RUSTICL_ENABLE=v3d` 等于没设备**：`clinfo -l` 只显示平台、一个设备都没有。不设这个变量，OpenCL 在这块板子上就等于不存在。查当前生效的驱动就靠这个：

```
RUSTICL_ENABLE=v3d clpeak --list-devices   # 会打印 Driver : 26.3.0-devel (git-...)
```

### 测量工具

| 工具 | 版本 / 来源 | 用途 |
| --- | --- | --- |
| **clpeak** | **2.1.4**，官方 release 二进制 `clpeak-2.1.4-linux-aarch64.zip`（github.com/krrishnarraj/clpeak） | fp32/fp16/int 峰值、作标定参照 |
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

输出每个 job 的纳秒级起止（`begin_job_csd` / `end_job_csd` / `begin_job_tfu` / `begin_cmdbuf`）。用区间并集算「GPU 忙占比」和「job 之间的空隙」：`scripts/analyze-gpu-trace.py`。

> **V3D 没有自由运行的 GPU 时钟寄存器**（是的，2026 年的「GPU」连一块表都没有）。这些时间戳是内核在提交/完成时取的 CPU 时钟样本（`DRM_V3D_EXT_ID_CPU_TIMESTAMP_QUERY`），是「job 在内核眼里的起止」，不是纯 GPU 执行时钟。

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
```

计数器名字取自 mesa `src/broadcom/common/v3d_performance_counters.h` 里 `V3D_VERSION >= 71` 那张表。`SET_GLOBAL` ioctl 是让**系统级**守护进程能计数（而非只算单个上下文）的关键；内核不支持时 pps 会直接 FATAL 提示。

---

## 3. 结果

### 3.1 排除法：每一个借口都被当场击毙

| 候选原因 | 判定 | 依据 |
| --- | --- | --- |
| GPU 空闲 | ❌ | GPU 忙占比 **98.7%**（ncnn）/ **99.9%**（llama.cpp） |
| 提交开销 | ❌ | job 间空隙 **1.3%** / **0.1%** |
| 内存停顿 | ❌ | `L2T-stalls-mem` **0.03%** / **~0%** |
| 缓存不友好 | ❌ | ncnn L2T 命中率 **97.6%**；clpeak 只有 87.5% 却快 4 倍 |
| **指令发射率不足** | ✅ | **0.4696 / 2.0014 = 23.5%** |

它没闲着，它没等内存，它缓存命中率比参照 kernel 还高，它就是**单纯地发射不出指令**。

### 3.2 根因：指令发射率只有参照值的 23.5%

`QPU-total-instr-cache-hit/miss` 名字里带 "cache"，一开始无法确定它数的是**指令**还是 **cache line**——所以先拿 **clpeak 的纯 FMA kernel**（同机器上最快的已知 kernel）来**标定仪器**：

| | 取指总数 ÷ cycle-count |
| --- | --- |
| **clpeak 纯 FMA**（56.36 GFLOPS） | **2.0014** |
| **ncnn SR** | **0.4696** |

⇒ **ncnn 的 shader 每 2.1 个周期才发射一条指令，而 clpeak 是每周期 2 条。**
QPU 在 99.86% 的周期里「active」，却只在 **23.5%** 的发射槽里干活。

一个**合理但未证实的猜测**：这类从 DSP/多媒体时代传下来的设计，通常依赖编译器手工配对指令、
寄存器冒险多、可切换的硬件线程少——于是「一条独立 FMA 链打到天荒地老」的 clpeak
正好长在硬件的舒适区里，而真实 shader 里全是依赖链和地址计算，发射槽大量空转：**active，但不产出**。
（注意这只是猜测的方向：0.3 节已说明 **VC4 手册的机制不能直接套到 V3D 7.1**，
而 7.1 的公开手册不存在，所以这条只能算待验证的解释。）

**注意口径：这是相对参照值的比较，不是相对理论峰值的比较。** clpeak 的 2.00 看着像个整数上限，
但我们**没有**证明它就是理论极限——恰恰相反，第 6 节会说明它很可能远不是。

发射率 23.5% 与吞吐量级自洽（shader 里还有非 FMA 指令，FMA 率必低于发射率）。

### 3.3 硬件画像：两个框架高度一致

| 计数器 / cycle-count | ncnn (SR, 卷积) | llama.cpp (LLM, matmul) |
| --- | --- | --- |
| **compute-active-cycles** | 99.86 % | 96.29 % |
| `L2T-stalls-mem` | 0.03 % | ~0.00 % |
| L2T 命中率 | 97.56 % | 96.74 % |
| `TMU-active-cycles` | 8.99 % | 7.09 % |

两个独立团队、两种负载形态，画像一致：**compute 单元几乎全程活跃、几乎不停顿、缓存命中率 97%**，却只产出「clpeak 能拿到的」几个百分点。油门踩进发动机舱，车速 20。

### 3.4 交叉验证：llama.cpp（独立框架、独立团队）

| | 纯 CPU 构建 | Vulkan (V3D) | 比值 |
| --- | --- | --- | --- |
| pp64（prompt，计算密集） | **143.95 t/s** | 2.22 t/s | **64.8×** |
| tg16（逐 token，访存密集） | **21.05 t/s** | 0.32 t/s | 65.8× |
| `avg_cpu` 占用 | 276 % | 32 % | — |

GPU 时间线：**3612** 个 CSD job，平均 42.2 ms，**GPU 忙占比 99.9%**，空隙 0.1%。
按 dispatch 折算：一次约 16×1024×1024 的 matmul ≈ 33.5 MFLOP 花 42 ms = **0.8 GFLOPS（是 clpeak 56.36 的 1.4%）**。

一颗「GPU」跑 matmul 跑出 0.8 GFLOPS，这个数字值得裱起来。

---

## 4. 撞过的坑（方法学，都很隐蔽）

1. **clpeak 的 CPU 后端自动迭代校准在 MT 档是坏的**——同一会话、同一时钟连跑三次 `float MT` = 173.83 / **112.14** / 173.64；加 `--iters` 固定后 175.98 / 175.96 / 175.16。**凡引用 CPU MT 数字必须固定迭代。**（GPU 侧自动校准可靠，重复性 0.05% 内。）
2. **`llama-bench -ngl 0` 不是 CPU 基线**——用 Vulkan 版二进制时后端仍会插手：pp64 只有 2.27 t/s vs **纯 CPU 版 143.95 t/s（差 63×）**，`avg_cpu` 也从 276% 掉到 124%（4 线程只吃 1.24 核，是很好的判据）。**测纯 CPU 必须用 CPU-only 构建。**
3. 两个 tarball 解压目录**同名**（都叫 `llama-b11146/`）会互相覆盖；llama.cpp **自动加载目录里所有 `libggml-*.so`**——CPU 目录残留一个 `libggml-vulkan.so` 就把 Vulkan 挂上了。
4. **计数器要拿已知最快的 kernel 标定**，否则不知道它数的是什么单位。
5. **`V3D_DS_COUNTER` 不设 = 静默不采**（驱动直接 return，不报错）。
6. **`cycle-count` 是时钟门控的**：GPU 空闲时精确不涨。所以 `compute-active-cycles / cycle-count` 是「**GPU 被时钟驱动的期间**」的比值，**不是**对整段墙钟的占比。
7. **计数器有启动瞬态**：perfmon 刚创建时首次读数带一个大跳变（实测第 0 秒的 `cycle-count` 增量是平稳期的 ~40 倍）。把它包进统计窗口会把比值稀释掉（llama.cpp 的 compute-active 会从 96.29% 掉到 82.10%）。`analyze-counters.py` 会跳过第一个采样秒。
8. **v3dv + perfetto 的上游 bug**：`perfetto.h` 用 `#ifdef THREAD_SANITIZER` 判断，而 mesa 无条件发 `-DTHREAD_SANITIZER=0` → 去找 `<sanitizer/tsan_interface.h>`，不带 sanitizer 头的 clang（比如自编的）直接编译失败。上游 main 至今仍发 `=0`。

---

## 5. 结论

**别在树莓派 5 上用 GPU 跑计算。**理由不是「一身力气使不出来」，而是**它本来就没多少力气**：

- fp32 实测吞吐只有 4 核 A76 的 1/3
- 而且真实负载连这 1/3 都拿不到（发射率 23.5%）
- fp16 在 v3dv 上与 fp32 **同速率**（`half4` 56.28 ≈ `float4` 56.36），没有倍率——半精度省带宽不省算力
- 每 stage 只有 **8 个 SSBO**、32 KB 共享内存、没有矩阵单元、**没有 GPU 时钟寄存器**
- 硬件编码器被砍了，H.264 硬解也没了，这颗芯片的多媒体遗产只剩一个 HEVC 解码器

`ncnn` 和 `llama.cpp` 是两个独立团队、两种完全不同的负载形态，在同一块 V3D 上给出**同一个画像**——所以这不是某个框架的优化问题，是这块硅的问题。

**唯一还站得住的用法是「卸载」而不是「加速」**：Vulkan 路径 CPU 只吃 **1%**（对比 CPU 路径的 313%），代价是慢 21–65 倍。要不要这个交换，取决于你的场景是「CPU 被占满了要腾出来」还是「想更快」——后者直接上 CPU 就对了。

这块 GPU 的一生：祖传手机多媒体芯片架构，社区逆向了十几年才用上，官方开源是悬赏出来的，算力是 CPU 的三分之一，编码器在新一代被砍掉省成本。**捡到屎不可怕，可怕的是这坨屎还有完整的家谱。**

---

## 6. 未解的问题（诚实标注）

1. **理论 fp32 峰值到底是多少？三个数字凑不齐。**
   - `v3d_ident` 报 **QPUs: 12**（Slices 3 × 每 slice 4 个）
   - VC4 手册说树莓派的 VC4 也是「3 slice × 4 QPU = 12」
   - 但实测 clpeak 只跑到 **56.36 GFLOPS**，指令发射率只有 **2.00/周期**

   若 V3D 7.1 真是 12 个 QPU、每个 16 宽、每周期各发射一条，那发射上限是 12/周期——
   **clpeak 的 2.00 就只有 ~17%**。也就是说「clpeak 是上限」只能算「已知最好」，
   远不是「理论极限」。要定案需要两样东西：权威的 V3D 7.1 微架构文档（**博通不给**，见第 0 节），
   以及 **V3D 的核心频率**（`vcgencmd measure_clock v3d` 空闲时读数是 0，得边跑负载边采）。
   **所以本文所有比较一律用「相对 clpeak」的口径，不写「占理论峰值百分之几」。**
   讽刺的是：连「这块 GPU 理论上有多强」都是一个需要逆向工程才能回答的问题。

2. **发射率 23.5% 的近因是「依赖链长」还是「常驻 warp 少、切不过来」？** 证据排除了空闲/内存/缓存/提交开销，剩下「发射率低」；但 V3D 7.1 的计数器里**没有 occupancy 类**，这最后一刀分不开，要看生成的汇编/寄存器占用。

3. **并发实验的计数器部分没采到**（采集窗口比两个并发 clpeak 的总时长短），只有 GFLOPS 结论（各得一半 ⇒ 单实例已打满）。要补需重开 traced 重跑。

---

## 7. 复现

```
scripts/
  run-clpeak.sh            跑 clpeak 全量并落盘（含驱动版本）
  analyze-gpu-trace.py     解析 MESA_GPU_TRACES=print_csv，算 GPU 忙占比 / job 分布
  analyze-counters.py      解析硬件计数器采样，算 compute-active / stall / 发射率
  q-counters.sql           从 Perfetto trace 里导出计数器采样
data/
  clpeak-{baseline,release}-{opencl,vk,cpu}.json   clpeak 原始结果
  gpu-trace-ncnn.csv / gpu-trace-llamacpp.csv      GPU job 时间线原始数据
  counters-ncnn-qpu.txt                            含 0.4696（23.5% 那个数）
  counters-clpeak-control.txt                      含 2.0014（标定参照）
  counters-llamacpp.txt
```

## 8. 参考

- VideoCore IV 3D Architecture Reference Guide（唯一的公开 QPU 手册，2014，讲的是 BCM21553）：
  <https://docs.broadcom.com/docs/12358545>
- 荷兰海牙地区法院裁定博通须在迁移期继续支持：[Network World](https://www.networkworld.com/article/4015489/dutch-court-forces-broadcom-to-support-vmware-migration-after-85-price-hike-backlash.html) / [The Register](https://www.theregister.com/software/2025/06/30/vmware-must-support-dutch-govt-agencys-migration-judge/)
- Broadcom–VMware 许可变更与 CISPE 投诉：[SDxCentral](https://www.sdxcentral.com/news/broadcom-faces-european-court-blow-over-vmware-support-cutoff/)

## License

MIT —— 见 [LICENSE](LICENSE)。数据与文字随便用，能注明出处更好；
如果这些测量帮你省了几天，或者你发现了本文的错误，欢迎开 issue。
