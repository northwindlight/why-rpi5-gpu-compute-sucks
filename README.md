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

### 0.3 QPU：一个为画三角形而生的「通用计算单元」

要讲清楚这块 GPU 为什么是屎，得先讲清楚正常 GPU 是怎么干活的——对照之下，QPU 的每一个设计决策都精准地踩在 GPGPU 的反面。

#### 0.3.1 传统 GPU 是怎么工作的

现代 GPU（NVIDIA 是典型，AMD、ARM Mali 大同小异）的编程模型叫 SIMT：你写的是标量线程代码，
硬件把 32 个线程捆成一个 warp，在同一条宽 SIMD 流水线上锁步执行。真正重要的是流水线**外面**那圈后勤系统：

- **海量常驻线程。** 一个 SM 上同时住着几十个 warp（几百上千个线程）。任何 warp 卡住——等内存、等依赖、
  等什么都行——调度器下一个周期就换另一个 warp 上。延迟不是靠「不 stall」解决的，是靠「stall 了也无所谓」解决的。
  **occupancy（常驻线程数）是整个模型的命根子。**
- **记分板（scoreboard）。** 每条指令的依赖关系由硬件实时追踪，编译器只需要把代码排得「大致合理」，
  剩下的冒险检测、停顿、恢复全是硬件的活儿。
- **大寄存器堆，多端口。** 一个 SM 配 256 KB 寄存器堆，同时喂得动多条流水线，想读几个操作数读几个。
- **每 warp 一个调度器，每周期发射。** 硬件调度器从几十个就绪 warp 里挑一个发射，
  编译器排错的代价被调度器当场消化掉。

这套设计的哲学是：**编译器尽力而为，硬件兜底**。所以哪怕 shader 编译得平庸，几十个 warp 的冗余
也能把流水线喂到六七成饱。

#### 0.3.2 QPU 是怎么工作的

现在来看 VideoCore 的 QPU。它是给片元着色器设计的——名字里的 Quad 就是「四个像素」。

> ⚠️ **先声明口径**：下面这批细节出自 **VideoCore IV（VC4）** 那本唯一公开的手册（2014 年，
> 讲的还是 BCM21553），**描述的是树莓派 1~4 那一代的 QPU**。Pi 5 是 **VideoCore VII（V3D 7.1）**，
> 是同一家族的**另一条演化线**：
> - mesa 里 **VC4 有自己的 QPU 编译器**（`src/gallium/drivers/vc4/vc4_qpu.c`、`vc4_qpu_schedule.c`、
>   `vc4_qpu_disasm.c`…），**V3D 有完全另一套**（`src/broadcom/compiler/` 的 `vir_*`），两边**互不引用**；
> - 而 `devinfo->has_accumulators = devinfo->ver < 71` 说明：累加器在 **V3D 4.2（Pi 4）上还在**，
>   到 **V3D 7.1 才被拆掉**。
>
> 所以 VC4 手册只能当**设计思路**的参考，不能当 Pi 5 的规格书。这个区分很重要，
> 而且 **7.1 到底改了什么、没改什么，本身就是本文后半段要批的重点**。

按那本手册，VC4 的 QPU 是这样的：

- **「16 路 SIMD」是时间复用出来的。** 物理上只有 **4 路 ALU**，一条指令在流水线上**连跑 4 个周期**，
  分四拍处理 16 个 lane。你看到的是 16 路向量机，实际上是 4 路 ALU 在原地踏步。
- **双发射，但要手工配对。** 一个加/非乘 ALU + 一个乘 ALU，每周期最多 1 add + 1 mul。
  想打满，编译器必须像拼积木一样把 add 和 mul 严丝合缝地配对。**配不上？那个发射槽就永久空着**
  ——没有硬件调度器来补救。
- **寄存器堆写入后下一拍不能读。** 两个 ALU 每周期最多要 4 个操作数，寄存器堆给不起。
  博通的解法是塞 **5 个累加器（r0–r5）** 当旁路，外加一堆潜规则：刚写进累加器的值要等一拍、
  水平旋转只在 mul ALU 的一个输入口且只能来自 r0–r3、「上一条指令的结果」有专用反馈路径……
  编译器要在一堆互相打架的资源约束里走钢丝。
- **只有两个硬件线程。** 传统 GPU 用几十个 warp 藏延迟，QPU 用 **2 个**。
  更绝的是：**线程切换是协作式的**——要软件显式发 thread switch 指令，硬件不会自动切。
  连延迟隐藏都是程序员的活儿。
- **固定延迟，程序员自己数拍子。** 纹理读取、特殊函数（倒数、exp2、log2）的结果都在**固定拍数**之后
  从 FIFO 里吐出来，程序必须在正确的时刻去接。分支有 **3 个延迟槽**，槽里的指令无条件执行。
  **没有记分板，没有动态调度，所有冒险都是编译期的静态责任。**

#### 0.3.3 ★ 说穿了：这不是 GPU，是一颗被贴上 GPU 标签的 DSP

把上面两条并排看，结论其实很难回避：**QPU 的设计语言从头到尾是 DSP 的，不是 GPU 的。**

| 特征 | DSP 世界（TI C6x / Hexagon / CEVA） | GPU 世界（SIMT） | QPU |
| --- | --- | --- | --- |
| 调度 | **静态**，编译器排好、硬件不管 | 动态，硬件记分板 + 调度器 | **静态**（无记分板） |
| 延迟隐藏 | 软件排流水 / 少量线程 | **海量 warp，硬件切换** | **2 个线程，且要软件切** |
| 乘累加 | **累加器**是标志性部件 | 不特别 | **5 个累加器 r0–r5** |
| 指令打包 | **VLIW**，手工配对 | 硬件自己发射 | **手工配对 add/mul** |
| 分支 | 延迟槽 | 预测 / 硬件处理 | **3 个延迟槽** |
| 暂存器 | **软件管理的 scratchpad** | 缓存自动管理 | **VPM / 32 KB 共享内存** |
| 向量宽度 | 早期靠时间复用「伪 SIMD」 | 真 SIMD | **4 路 ALU 跑 4 拍** |

QPU 几乎是把 DSP 的教科书特征抄了一遍。它之所以被叫做「GPU 的计算单元」，
是因为**它旁边挂着一圈固定功能图形管线**（光栅化器、TMU、TFU）——「GPU」这个身份，
来自那圈管线，不来自这颗核本身。

这也解释了它为什么在图形上完全够用：**画三角形时，真正的延迟隐藏是光栅化器在干，
不是 QPU 在干。** 场景里有成千上万个互相独立的像素 quad 在光栅化器出口排队，
12 个 QPU 每个都有干不完的短而规整的活儿；片元 shader 很短、依赖链很短、四个像素天然无关
——延迟隐藏根本不用 QPU 自己操心，**系统级的并行度替它兜了底**。
这就是为什么 2004 年的 Alphamosaic 敢用 4 路物理 ALU、2 个线程、单端口寄存器堆
做出一颗够用的手机 GPU：面积小、功耗低，而图形负载的规整性刚好把所有短板都遮住了。

**GPGPU 一来，遮羞布全没了：**

- **计算负载是一整个巨大的 dispatch，不是无数个独立 quad。** 光栅化器队列是空的，
  「系统级并行度」这个免费午餐不存在了，QPU 只剩 **2 个协作式切换的线程**对抗一切延迟。
- **真实 shader 里全是依赖链和地址计算。** 地址计算吃 add pipe，算不动 add/mul 配对；
  依赖链一长，双发射槽大量空转。clpeak 那种「一条独立 FMA 链打到天荒地老」的 kernel
  是唯一恰好长在硬件舒适区里的负载——顺便说，它的实测发射率正好是 **2.0014 条/周期**，
  这个数字就是「add + mul 两个发射槽全部打满」的形状。
- **归约和水平操作是酷刑。** lane 之间想交换数据，要走累加器旋转这条羊肠小道；
  而卷积和 matmul 里全是归约。
- **编译器没有兜底。** SIMT 上编译器排错了，warp 调度器帮你消化；QPU 上编译器排错了，
  发射槽就是空的，谁也不会来救。
- **现代 GPU 的作弊码一个都没有。** 没有 tensor core，fp16 和 fp32 同速率——
  NVIDIA 过去十年靠矩阵单元和半精度拿到的两个数量级，这里一分都没有。

现在再看实测画像就完全说得通了：ncnn 的 shader 里 compute-active 高达 **99.86%**、
L2T 命中率 **97.6%**、内存 stall 约等于零——**QPU 从头到尾都在转，只是发射槽 76.5% 的时间是空的。**
这不是内存问题、不是驱动问题、不是编译器不努力，是**一台 2004 年为「四个像素」设计的机器，
被要求在 2026 年跑卷积和 matmul**。它真的很努力，油门已经踩进发动机舱了，
但它的变速箱只有两个挡，还是手动的。

#### 0.3.4 掉队：2006 年就分道扬镳，之后一路往错的方向改

时间线摆出来，掉队有多远一目了然：

| 年份 | 业界 | 博通 VideoCore |
| --- | --- | --- |
| 2004 | — | Alphamosaic 的 DSP 式 QPU 成型 |
| 2006 | **NVIDIA G80**：统一着色器、SIMT、动态调度、海量 occupancy —— 整个行业转向 | 继续 DSP 式静态调度 |
| 2010s | 各家的 occupancy 和 cache 层级一路加码 | 一路加 QPU 数量、加固定功能块 |
| 2017 | **Volta：Tensor Core** | 无 |
| 2018+ | 半精度倍率（fp16 2×）连手机 GPU 都成标配 | 无 |
| 2023（Pi 5） | — | **V3D 7.1：无矩阵单元、fp16 与 fp32 同速率、每 stage 8 个 SSBO、连自由运行的 GPU 时钟寄存器都没有** |

★ 而这里有个细节，比「原地不动」更说明问题：**博通其实动过刀。**
`has_accumulators = ver < 71` —— 他们在 7.1 上**把自己祖传二十年的累加器旁路拆了**
（VC4 手册里的 r0–r5，Pi 4 的 V3D 4.2 都还在）。所以他们有工程资源、也在改微架构，
**只是改的是「怎么维护祖传设计」，而不是「补上计算需要的东西」**：
矩阵单元没有、半精度倍率没有、SSBO 只有 8 个、GPU 时钟寄存器没有。

这就是「掉队」的准确含义：**不是他们停下了，是他们一直在修一辆和时代无关的车。**

#### 0.3.5 那博通为什么非要用这坨？——结构性的激励，不需要假设恶意

看到这里正常人的问题都是：**Mali 和 PowerVR 明明可以买，为什么非要自研这么一坨？**
答案不神秘，是四条结构性激励叠在一起：

1. **省授权费。** VideoCore 是自家的，买 Mali/PowerVR 要按颗交 IP 授权费。
   博通的机顶盒 SoC 出货量巨大，「每颗省几分钱」乘出来是真金白银。
2. **掌握启动链 = 掌握产品。** 0.1 节说过：整台机器是 GPU 先醒、固件放 ARM 核出来。
   在需要安全启动和 DRM 的机顶盒生意里，**这是功能不是 bug**。换 Mali 就等于把这把钥匙交给别人。
3. **闭源就是护城河。** 不给文档，就没有人能轻易替代或复刻；客户被锁定，代代挤牙膏也不怕。
   2014 年那次「开源」给的是 **BCM21553** 而不是树莓派在用的 BCM2835，得靠基金会掏 1 万美元悬赏
   让社区自己移植——这个细节比任何声明都更能说明态度。
4. **目标客户从来不是「你」。** VideoCore 的客户是电视、机顶盒、手机厂商：
   他们只要 4K 解码和 2D 合成够快，**GPGPU 不在需求列表上**。
   树莓派是搭便车的——博通给 Pi 供货，是因为 BCM2712 本来就是为别的市场设计的。

还有沉没成本：二十年的固件、驱动、验证资产、客户集成。换架构要全部重做，而现有客户不抱怨。

**所以不是「他们做不到更好」，是「在他们的商业模型里没有理由做得更好」。**
代价由谁承担？—— Pi 5 把 H.264 编码器整个砍掉（见 0.4），省下的硅面积进了博通的账本，
电费账单寄给用户；你想要 GPGPU，得先逆向十几年才有个能跑的驱动。

结论是结构性的，不需要假设任何人怀着恶意。**但结果和恶意没有区别**：
你手里是一颗没有文档、没有计算能力、还被砍了编码器的 GPU。

#### 0.3.6 那本文凭什么用实测值说话

**博通没给 V3D 7.1 的公开手册**（传统艺能，见 0.1），所以本文关于 Pi 5 的一切
只用从驱动**实测**拿得到的事实（设备查询结果，不是推测，也不是从 VC4 手册抄的）：

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

### QPU 微架构（0.3 节的 VC4 部分）

- **VideoCore IV 3D Architecture Reference Guide** —— 唯一的公开 QPU 手册（2014，讲的是 BCM21553，
  不是树莓派在用的芯片）：<https://docs.broadcom.com/docs/12358545>
- Herman Hermitage 的 VideoCore IV wiki（社区逆向的一手记录）：
  <https://github.com/hermanhermitage/videocoreiv/wiki/VideoCore-IV-3d-Graphics-Pipeline>
- 树莓派论坛关于 QPU 线程数的讨论（「每个 QPU 两个硬件线程、协作式切换」）：
  <https://forums.raspberrypi.com/viewtopic.php?t=77819>

### 博通 / VMware

（下面几条）
- 荷兰海牙地区法院裁定博通须在迁移期继续支持：[Network World](https://www.networkworld.com/article/4015489/dutch-court-forces-broadcom-to-support-vmware-migration-after-85-price-hike-backlash.html) / [The Register](https://www.theregister.com/software/2025/06/30/vmware-must-support-dutch-govt-agencys-migration-judge/)
- Broadcom–VMware 许可变更与 CISPE 投诉：[SDxCentral](https://www.sdxcentral.com/news/broadcom-faces-european-court-blow-over-vmware-support-cutoff/)

## License

MIT —— 见 [LICENSE](LICENSE)。数据与文字随便用，能注明出处更好；
如果这些测量帮你省了几天，或者你发现了本文的错误，欢迎开 issue。
