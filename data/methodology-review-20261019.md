# 2026-10-19 方法学复查

这是对旧报告解释的复查，不是新一轮 GPU 性能测试。原有 JSON、CSV、计数器文本原样保留；
历史文本中“2 个 QPU”“发射率”“GPU 真忙”等解释已撤回，以本记录及修订 README 为准。

## 1. 本地源码查找

- 未在本次检索的 `/home/northwind`、`/usr/src`、`/tmp`、`/opt` 中找到完整树莓派专用内核工作树。
  这不证明它从未存在，或不存在于未挂载介质。
- `/home/northwind/projects/embedded/linux-6.18` 实际是 **6.18.44**，Git remote 为南京大学镜像的
  `linux-stable.git`，不是树莓派内核分支；其中可读通用 V3D 实现。
- `/home/northwind/a733-work/kernel` 是全志环境的 **6.6.98**。
- `/usr/src/linux-headers-6.18.50+rpt-common-rpi` 和 `linux-headers-6.18.50+rpt-rpi-2712`
  是头文件/构建支持，不等于完整驱动源码。当前 `/lib/modules/.../source` 链接也指向头文件树。

## 2. 树莓派分支源码（在线只读核对）

分支：`rpi-6.18.y`。本次固定的提交：`5d64d1ea73716526ed9c0ed184a9cb5f6042e32c`。
该提交不自动等于旧实验时加载的内核二进制，未做源码包/二进制逐项匹配。

- [v3d_perfmon.c](https://github.com/raspberrypi/linux/blob/5d64d1ea73716526ed9c0ed184a9cb5f6042e32c/drivers/gpu/drm/v3d/v3d_perfmon.c)：
  `v3d_perfmon_get_values_ioctl()` 调用 `v3d_perfmon_stop(..., true)`。
  `stop` 累加硬件寄存器值，随后关闭 `V3D_V4_PCTR_0_EN` 并清空 `active_perfmon`。
- [v3d_sched.c](https://github.com/raspberrypi/linux/blob/5d64d1ea73716526ed9c0ed184a9cb5f6042e32c/drivers/gpu/drm/v3d/v3d_sched.c)：
  `v3d_switch_perfmon()` 选择 global/job perfmon，必要时调用 `v3d_perfmon_start()`；
  start 重新配置和清零硬件计数器，累计结果仍保存在 perfmon 的软件 values 中。
- 由此可见，读取不是始终无副作用的连续快照；长 job 内高频读取可能只计到首段，
  后续重复读取可能拿到平台值，直到另一个调度入口重启。
  **这解释了一种覆盖不足的机制，还没有证明旧实验具体丢失了多少。**

下载后与固定提交内容逐字节核对相同，SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| v3d_perfmon.c | `aacf53c36ed597b7242e8daf8755b8d5d40b1e5e819c61ef44bea1fbc9ac2a3f` |
| v3d_sched.c | `e38736292e21dc07ac9f161b43eddd179b598cf0c4acf2b8bba2241dedb6efaf` |

## 3. 事件定义

内核事件表及本地 Mesa `a5d39a4b743b719cfc2a0da410910ea3fd8ff770` 的
`src/broadcom/common/v3d_performance_counters.h` 都把 instr-cache hit/miss 描述为所有 slice 的指令缓存事件。
缓存请求与指令执行的对应关系未经可控实验标定；比值接近整数不能证明事件单位。
QPU 指令里的 add/mul 可以共存，更不能将 2 个缓存事件解释为 2 个 ALU 槽全满。

同一 V3D 7.1 事件表还列有 `QPU-active`、`QPU-stalls`、`QPU-wait-bubble`、TMU/SFU 等细分等待事件。
旧实验没有完整采集这些事件，不能先宣布已经排除所有等待。
`L2T-stalls-mem` 描述为 AXI blocks read，也不代表所有内存停顿。

## 4. 12 QPU 的吞吐模型

[Idein/py-videocore7 的实现者说明](https://github.com/Idein/py-videocore7#about-videocore-vii-qpu)
按 12 QPU × 4 物理路 × 2 FLOP/周期计算，800 MHz 为 76.8 GFLOPS。
这是社区实现资料；16 路逻辑向量宽度不能直接当作每周期 16 路物理吞吐。

56.36 / 76.8 = 73.4%；若条件频率为 600 MHz，56.36 / 57.6 = 97.8%。
旧实验没有保全负载频率，不能把任一条件比例当成实测峰值利用率。
并发两实例各拿到约一半吞吐，说明竞争共享资源，不证明所有 ALU 已满。

## 5. 重放保存的采样，比较裁剪策略

使用现存 `trace_processor_shell` 重新导出原 protobuf，再运行修订的分析脚本。
旧报告裁掉首秒；新版默认保留全窗口，显式 `--skip-first-seconds 1` 可复算旧口径。

| 负载 | 裁剪首秒 | 窗口秒 | cycle-count 增量 | compute-active 比值 | 指令缓存事件/周期 |
| --- | --- | --- | --- | --- | --- |
| clpeak | 否 | 26.98 | 15,949,903 | 99.87% | 2.0024 |
| clpeak | 是 | 25.98 | 13,978,146 | 99.86% | 2.0014 |
| ncnn | 否 | 56.68 | 94,726,065 | 99.86% | 0.4706 |
| ncnn | 是 | 55.68 | 89,486,404 | 99.86% | 0.4696 |
| llama.cpp | 否 | 116.47 | 1,187,653,627 | 82.10% | 未采集 |
| llama.cpp | 是 | 115.47 | 1,012,370,419 | 96.29% | 未采集 |

旧 23.5% 算式可复现，但只能表示该裁剪窗口中的缓存事件密度之比，不能称为发射槽利用率。
llama.cpp 全窗口和裁剪窗口差异很大，没有工作负载边界证据时不能直接认定首秒无效。

复查还重放了仓库现有两个软件 trace：ncnn 窗口并集约 98.7%，llama.cpp 约 99.9%；
这些窗口包含 cmdbuf 等软件层边界，并不证明 GPU 硬件占用率。
CSD 数量分别为 184 / 3612，平均窗口约 114.8 / 42.2 ms。
把其中一个平均窗口与假定 matmul FLOP 数相除，不能得到可靠的硬件 matmul GFLOPS。

复算输入（本地历史文件，不是本次新采集）：

- `/home/northwind/ncnn-lab/clpeak-qpu.proto`
- `/home/northwind/ncnn-lab/v3dv-qpu.proto`
- `/home/northwind/llama-lab/llama-counters.proto`
- 仓库 `data/gpu-trace-ncnn.csv`、`data/gpu-trace-llamacpp.csv`

复算命令示例：

```sh
trace_processor_shell query -f scripts/q-counters.sql /path/to/trace.proto > counters.csv
python3 scripts/analyze-counters.py counters.csv
python3 scripts/analyze-counters.py counters.csv --skip-first-seconds 1
python3 scripts/analyze-gpu-trace.py data/gpu-trace-ncnn.csv
python3 scripts/analyze-gpu-trace.py data/gpu-trace-llamacpp.csv
```

## 6. 保留与撤回

保留：记录配置下的 clpeak 吞吐、ncnn 延迟、llama.cpp t/s，以及 V3D 栈在这些实际工作负载上支持差的评价。

撤回：23.5% 发射率、严格排除空闲/提交/内存、全部责任归硅片、完美驱动最多改善 4.26 倍、
不可靠的 0.8 GFLOPS matmul 推导、用 VC4 线程/累加器规则直接解释 Pi 5。

本次没有修改 Mesa 或内核，没有重新跑 GPU 负载，也没有更新上游项目。
