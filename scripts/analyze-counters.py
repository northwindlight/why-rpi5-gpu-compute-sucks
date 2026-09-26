#!/usr/bin/env python3
"""解读 V3D 硬件计数器采样。

用法: analyze-counters.py <counters.csv>
CSV 由 trace_processor_shell 用 q-counters.sql 导出: counter,ts,value

关键：这些是**累积计数器**，要看「忙的那段时间里的增量」。
用 cycle-count 的增量做归一，算各类活跃/停顿周期的占比。
"""
import csv
import sys
from collections import defaultdict


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    series = defaultdict(list)
    with open(sys.argv[1]) as f:
        for row in csv.DictReader(f):
            series[row["counter"]].append((int(row["ts"]), float(row["value"])))

    if not series:
        print("没有计数器数据")
        return 1

    # 采样窗口以 cycle-count 自身范围为准，但**丢掉第一个采样秒**：
    # perfmon 刚创建时首次读数带一个巨大跳变（实测第 0 秒的增量是平稳期的 ~40 倍），
    # 把它包进窗口会把所有比值稀释掉（llama.cpp 那次 compute-active 会从 96% 掉到 82%）。
    if "cycle-count" not in series or len(series["cycle-count"]) < 2:
        print("缺 cycle-count，无法定窗口")
        return 1
    cc_all = series["cycle-count"]
    t_start = cc_all[0][0] + 1_000_000_000
    cc = [s for s in cc_all if s[0] >= t_start]
    if len(cc) < 2:
        cc = cc_all
    t0, t1 = cc[0][0], cc[-1][0]

    def delta(name):
        s = [x for x in series.get(name, []) if t0 <= x[0] <= t1]
        return (s[-1][1] - s[0][1]) if len(s) >= 2 else None

    cyc = delta("cycle-count")
    print(f"采样窗口: {(t1 - t0) / 1e9:.2f} s   采样点/计数器: "
          f"{min(len(v) for v in series.values())}~{max(len(v) for v in series.values())}")
    print(f"得到计数器: {', '.join(sorted(series))}\n")

    if not cyc:
        print("缺 cycle-count，无法归一")
        return 1

    print(f"{'计数器':28s}{'增量':>18s}{'/cycle-count':>15s}")
    print(f"{'cycle-count (总周期)':28s}{cyc:18,.0f}{'1.000':>15s}")
    for name in sorted(series):
        if name == "cycle-count":
            continue
        d = delta(name)
        if d is None:
            continue
        print(f"{name:28s}{d:18,.0f}{d / cyc:15.4f}")

    print("\n=== 关键比值 ===")
    ca = delta("compute-active-cycles")
    co = delta("core-active")
    if ca:
        print(f"  compute-active-cycles / cycle-count = {ca / cyc * 100:6.2f} %"
              f"   ← ★compute 单元真正活跃的周期占比")
    if co:
        print(f"  core-active-cycles    / cycle-count = {co / cyc * 100:6.2f} %")
    for n in ("TMU-active-cycles", "TMU-stalled-cycles"):
        d = delta(n)
        if d:
            print(f"  {n:22s}/ cycle-count = {d / cyc * 100:6.2f} %")
    for n in ("L2T-stalls-mem", "L2T-stalls-no-wid"):
        d = delta(n)
        if d:
            print(f"  {n:22s}/ cycle-count = {d / cyc * 100:6.2f} %")
    hit, miss = delta("L2T-total-cache-hit"), delta("L2T-total-cache-miss")
    if hit is not None and miss is not None and (hit + miss):
        print(f"  L2T 命中率 = {hit / (hit + miss) * 100:6.2f} %  (hit={hit:,} miss={miss:,})")

    # QPU 取指 → 近似「每周期发射指令数」
    qh = delta("QPU-total-instr-cache-hit")
    qm = delta("QPU-total-instr-cache-miss")
    if qh is not None or qm is not None:
        qh = qh or 0.0
        qm = qm or 0.0
        tot = qh + qm
        print("\n=== QPU 指令取指 ===")
        print(f"  instr-cache 命中/未命中 = {qh:,.0f} / {qm:,.0f}"
              f"  (命中率 {qh / tot * 100 if tot else 0:.2f} %)")
        print(f"  取指总数 / cycle-count = {tot / cyc:.4f}"
              f"   ← 近似「每周期取指次数」(V3D 7.1 有 2 个 QPU，理论上限 2/周期)")
        print("  注：instr-cache 计数可能按 cache line 而非按指令计，绝对值存疑；")
        print("      但数量级足以区分「每周期都在取指」和「几百周期才取一条」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
