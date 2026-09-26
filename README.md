# 为什么树莓派跑 GPU 计算就是屎

树莓派 5 的 VideoCore VII（V3D 7.1）在纸面上有个 GPU。真拿它跑计算，结论是：

> **它的算力本身就小 —— 实测 fp32 吞吐只有 4 核 CPU 的 1/3；
> 而且真实负载连这 1/3 都拿不到：指令发射率只有「同机器上已知最好 kernel」的 23.5%。**

下面每一条都是实测，**不依赖任何理论峰值假设**（见文末「未解的问题」）。

## TL;DR

| | 4×A76 (CPU) | V3D 7.1 (GPU) |
|---|---|---|
| fp32 实测吞吐（clpeak） | **176 GFLOPS** | **56.4 GFLOPS**（CPU 的 1/3） |
| ncnn 超分（320×136） | **493 ms** | 10,575 ms（**慢 21.4×**） |
| llama.cpp pp64 | **143.95 t/s** | 2.22 t/s（**慢 64.8×**） |
| 指令发射率（取指/周期） | — | clpeak **2.00** → ncnn **0.47** |

**同一个 GPU 上，clpeak 的纯 FMA kernel 能跑到 56.36 GFLOPS，而且单实例就已把 GPU 打满**
（并发跑两个实例，各自只拿到一半：28.19 / 28.18）。所以不是硬件坏了、也不是驱动烂 ——
是**真实负载在这种机器上根本喂不出吞吐**。

---

## 1. 环境与版本（复现所需的全部事实）

### 硬件

| 项 | 值 | 怎么查 |
|---|---|---|
| 板子 | Raspberry Pi 5 Model B **Rev 1.1** | `/proc/cpuinfo` |
| SoC / GPU | BCM2712 / **V3D 7.1.10.16** | `sudo cat /sys/kernel/debug/dri/*/v3d_ident` |
| GPU 结构（寄存器读出） | Slices **3**、TMUs **3**、QPUs **12** | 同上 |
| CPU | 4× Cortex-A76 | `nproc` |
| CPU 频率 | **2800 MHz（超频）** | `vcgencmd measure_clock arm` |
| 超频设置 | `arm_freq=2800`、`over_voltage_delta=50000` | `/boot/firmware/tryboot.txt` |

> ⚠️ CPU 是超频状态。要复现 CPU 侧数字请保持一致或按比例折算；GPU 侧数字不受 CPU 超频影响。

### 系统 / 工具链

| 项 | 版本 |
|---|---|
| OS | Debian GNU/Linux **13 (trixie)**，aarch64 |
| 内核 | **6.18.50+rpt-rpi-2712** |
| clang / gcc | **22.1.8** / 14.2.0 |
| Python | **3.13.5** |

### Mesa 驱动（自编 —— apt 源里没有可用的）

```
版本串：26.3.0-devel (git-a5d39a4b74)      ← 由 clinfo / clpeak --list-devices 读出
Vulkan API：1.3.363 (v3dv)    OpenCL：rusticl
```

从 mesa **main 分支**（gitlab.freedesktop.org/mesa/mesa）自编，**必须 release**：

```sh
git fetch --depth=1 origin main && git reset --hard FETCH_HEAD   # a5d39a4b74
CC=clang-22 CXX=clang++ meson setup build --buildtype=release \
  -Dcpp_rtti=false -Drust_std=2021 \
  -Dprefix=/usr/local -Dlibdir=lib/aarch64-linux-gnu \
  -Dgallium-drivers=v3d,zink,llvmpipe -Dvulkan-drivers=broadcom,swrast \
  -Dgallium-rusticl=true -Dglx=disabled -Dopengl=true -Degl=enabled
ninja -C build -j4 && sudo ninja -C build install && sudo ldconfig
```

要采硬件计数器还需 **`-Dperfetto=true -Dallow-fallback-for=perfetto`**（会自动下载 perfetto SDK）。

三个坑（都踩过）：

1. **`--buildtype=release` 才有 `-DNDEBUG`**。默认的 `debugoptimized` 断言是开着的 ——
   v3dv 的 `assert(pPoolSizes[i].descriptorCount > 0)` 会直接 abort。
2. `-Dcpp_rtti=false` 必须加：自编 clang 用的 LLVM 没开 RTTI，否则 meson 直接报错。
3. **`rusticl` 不设 `RUSTICL_ENABLE=v3d` 等于没设备**：`clinfo -l` 只显示平台、一个设备都没有。
   查当前生效的驱动就靠这个：
   ```sh
   RUSTICL_ENABLE=v3d clpeak --list-devices   # 会打印 Driver : 26.3.0-devel (git-...)
   ```

### 测量工具

| 工具 | 版本 / 来源 | 用途 |
|---|---|---|
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

```sh
# GPU（Vulkan / v3dv）
RUSTICL_ENABLE=v3d clpeak --vulkan --vk-device 0 --single-precision-compute
# GPU（OpenCL / rusticl）
RUSTICL_ENABLE=v3d clpeak --opencl --single-precision-compute
# CPU（★必须固定迭代数，见坑 1）
clpeak --cpu --single-precision-compute --iters 20000
```

### 2.2 GPU 时间线 —— 不需要 Perfetto

v3dv 的 u_trace 支持 `print_csv`，而且**不在 `#ifdef HAVE_PERFETTO` 里**，任何构建都能用：

```sh
MESA_GPU_TRACES=print_csv MESA_GPU_TRACEFILE=/tmp/gpu.csv <vulkan 程序>
```

输出每个 job 的纳秒级起止（`begin_job_csd` / `end_job_csd` / `begin_job_tfu` / `begin_cmdbuf`）。
用区间并集算「GPU 忙占比」和「job 之间的空隙」：`scripts/analyze-gpu-trace.py`。

> **V3D 没有自由运行的 GPU 时钟寄存器**。这些时间戳是内核在提交/完成时取的 CPU 时钟样本
> （`DRM_V3D_EXT_ID_CPU_TIMESTAMP_QUERY`），是「job 在内核眼里的起止」，不是纯 GPU 执行时钟。

### 2.3 硬件性能计数器 —— `gpu.counters.v3d`

```sh
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

计数器名字取自 mesa `src/broadcom/common/v3d_performance_counters.h` 里 `V3D_VERSION >= 71` 那张表。
`SET_GLOBAL` ioctl 是让**系统级**守护进程能计数（而非只算单个上下文）的关键；
内核不支持时 pps 会直接 FATAL 提示。

---

## 3. 结果

### 3.1 排除法：每一项都有读数

| 候选原因 | 判定 | 依据 |
|---|---|---|
| GPU 空闲 | ❌ | GPU 忙占比 **98.7%**（ncnn）/ **99.9%**（llama.cpp） |
| 提交开销 | ❌ | job 间空隙 **1.3%** / **0.1%** |
| 内存停顿 | ❌ | `L2T-stalls-mem` **0.03%** / **~0%** |
| 缓存不友好 | ❌ | ncnn L2T 命中率 **97.6%**；clpeak 只有 87.5% 却快 4 倍 |
| **指令发射率不足** | ✅ | **0.4696 / 2.0014 = 23.5%** |

### 3.2 根因：指令发射率只有参照值的 23.5%

`QPU-total-instr-cache-hit/miss` 名字里带 "cache"，一开始无法确定它数的是**指令**还是 **cache line**
—— 所以先拿 **clpeak 的纯 FMA kernel**（同机器上最快的已知 kernel）来**标定仪器**：

| | 取指总数 ÷ cycle-count |
|---|---|
| **clpeak 纯 FMA**（56.36 GFLOPS） | **2.0014** |
| **ncnn SR** | **0.4696** |

⇒ **ncnn 的 shader 每 2.1 个周期才发射一条指令，而 clpeak 是每周期 2 条。**
QPU 在 99.86% 的周期里「active」，却只在 **23.5%** 的发射槽里干活。

**这是相对参照值的比较，不是相对理论峰值的比较** —— 好处是不需要知道硬件的理论发射宽度。
（clpeak 的 2.00 看着像个整数上限，且单实例已打满，但我们**没有**证明它就是理论极限，
见文末。）

发射率 23.5% 与吞吐量级自洽（shader 里还有非 FMA 指令，FMA 率必低于发射率）。

### 3.3 硬件画像：两个框架高度一致

| 计数器 / cycle-count | ncnn (SR, 卷积) | llama.cpp (LLM, matmul) |
|---|---|---|
| **compute-active-cycles** | 99.86 % | 96.29 % |
| `L2T-stalls-mem` | 0.03 % | ~0.00 % |
| L2T 命中率 | 97.56 % | 96.74 % |
| `TMU-active-cycles` | 8.99 % | 7.09 % |

两个独立框架、两种负载形态，画像一致：**compute 单元几乎全程活跃、几乎不停顿、缓存命中率 97%**，
却只产出「clpeak 能拿到的」几个百分点。

### 3.4 交叉验证：llama.cpp（独立框架、独立团队）

| | 纯 CPU 构建 | Vulkan (V3D) | 比值 |
|---|---|---|---|
| pp64（prompt，计算密集） | **143.95 t/s** | 2.22 t/s | **64.8×** |
| tg16（逐 token，访存密集） | **21.05 t/s** | 0.32 t/s | 65.8× |
| `avg_cpu` 占用 | 276 % | 32 % | — |

GPU 时间线：**3612** 个 CSD job，平均 42.2 ms，**GPU 忙占比 99.9%**，空隙 0.1%。
按 dispatch 折算：一次约 16×1024×1024 的 matmul ≈ 33.5 MFLOP 花 42 ms = **0.8 GFLOPS
（是 clpeak 56.36 的 1.4%）**。

---

## 4. 撞过的坑（方法学，都很隐蔽）

1. **clpeak 的 CPU 后端自动迭代校准在 MT 档是坏的** —— 同一会话、同一时钟连跑三次
   `float MT` = 173.83 / **112.14** / 173.64；加 `--iters` 固定后 175.98 / 175.96 / 175.16。
   **凡引用 CPU MT 数字必须固定迭代。**（GPU 侧自动校准可靠，重复性 0.05% 内。）
2. **`llama-bench -ngl 0` 不是 CPU 基线** —— 用 Vulkan 版二进制时后端仍会插手：
   pp64 只有 2.27 t/s vs **纯 CPU 版 143.95 t/s（差 63×）**，`avg_cpu` 也从 276% 掉到 124%
   （4 线程只吃 1.24 核，是很好的判据）。**测纯 CPU 必须用 CPU-only 构建。**
3. 两个 tarball 解压目录**同名**（都叫 `llama-b11146/`）会互相覆盖；llama.cpp
   **自动加载目录里所有 `libggml-*.so`** —— CPU 目录残留一个 `libggml-vulkan.so` 就把 Vulkan 挂上了。
4. **计数器要拿已知最快的 kernel 标定**，否则不知道它数的是什么单位。
5. **`V3D_DS_COUNTER` 不设 = 静默不采**（驱动直接 return，不报错）。
6. **`cycle-count` 是时钟门控的**：GPU 空闲时精确不涨。所以
   `compute-active-cycles / cycle-count` 是「**GPU 被时钟驱动的期间**」的比值，
   **不是**对整段墙钟的占比。
7. **计数器有启动瞬态**：perfmon 刚创建时首次读数带一个大跳变（实测第 0 秒的 `cycle-count`
   增量是平稳期的 ~40 倍）。把它包进统计窗口会把比值稀释掉
   （llama.cpp 的 compute-active 会从 96.29% 掉到 82.10%）。`analyze-counters.py` 会跳过第一个采样秒。
8. **v3dv + perfetto 的上游 bug**：`perfetto.h` 用 `#ifdef THREAD_SANITIZER` 判断，
   而 mesa 无条件发 `-DTHREAD_SANITIZER=0` → 去找 `<sanitizer/tsan_interface.h>`，
   不带 sanitizer 头的 clang（比如自编的）直接编译失败。上游 main 至今仍发 `=0`。

---

## 5. 结论

**别在树莓派 5 上用 GPU 跑计算。** 理由不是「一身力气使不出来」，而是**它本来就没多少力气**：

- fp32 实测吞吐只有 4 核 A76 的 1/3
- 而且真实负载连这 1/3 都拿不到（发射率 23.5%）
- fp16 在 v3dv 上与 fp32 **同速率**（`half4` 56.28 ≈ `float4` 56.36），没有倍率
- 每 stage 只有 **8 个 SSBO**、32 KB 共享内存、没有矩阵单元、**没有 GPU 时钟寄存器**

`ncnn` 和 `llama.cpp` 是两个独立团队、两种完全不同的负载形态，在同一块 V3D 上给出**同一个画像** ——
所以这不是某个框架的优化问题。

**唯一还站得住的用法是「卸载」而不是「加速」**：
Vulkan 路径 CPU 只吃 **1%**（对比 CPU 路径的 313%），代价是慢 21–65 倍。
要不要这个交换，取决于你的场景是「CPU 被占满了要腾出来」还是「想更快」——
后者直接上 CPU 就对了。

---

## 6. 未解的问题（诚实标注）

1. **理论 fp32 峰值到底是多少？** `v3d_ident` 报 **QPUs: 12**（Slices 3 × 每 slice 4 个），
   而不是本文早期假设的 2 个。若 12 × 16 lane 成立，理论峰值会远高于实测的 56.36 GFLOPS，
   那么「clpeak 达到峰值 97%」的说法就是错的 —— **本文因此已撤掉一切「占理论峰值百分之几」的表述**，
   所有比较都改成「相对 clpeak 这个已知最快 kernel」。
   要定案需要权威的 V3D 7.1 微架构文档，或解 `DRM_V3D_PARAM_V3D_CORE0_IDENT1` 的
   `nslc`/`qups` 字段并解释 debugfs 标签的确切含义。
2. **发射率 23.5% 的近因是「依赖链长」还是「常驻 warp 少、切不过来」？**
   证据排除了空闲/内存/缓存/提交开销，剩下「发射率低」；但 V3D 7.1 的计数器里
   **没有 occupancy 类**，这最后一刀分不开，要看生成的汇编/寄存器占用。
3. **并发实验的计数器部分没采到**（采集窗口比两个并发 clpeak 的总时长短），
   只有 GFLOPS 结论（各得一半 ⇒ 单实例已打满）。要补需重开 traced 重跑。

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

## License

MIT —— 见 [LICENSE](LICENSE)。数据与文字随便用，能注明出处更好；
如果这些测量帮你省了几天，或者你发现了本文的错误，欢迎开 issue。
