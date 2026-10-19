#!/usr/bin/env python3
"""解读 V3D 硬件计数器采样。

用法: analyze-counters.py <counters.csv> [--skip-first-seconds 1]
CSV 由 trace_processor_shell 用 q-counters.sql 导出: counter,ts,value

关键：这些是**累积计数器**，要看「忙的那段时间里的增量」。
用 cycle-count 的增量归一，只报告所采事件的比值。
不能据此直接推断墙钟占用率、指令发射率或完整工作负载的停顿比例。
"""
import argparse
import csv
import sys
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--skip-first-seconds", type=float, default=0.0,
                        help="显式裁剪采样开头；旧报告用 1 秒，默认保留全部")
    args = parser.parse_args()
    if args.skip_first_seconds < 0:
        parser.error("--skip-first-seconds 不能为负数")

    series = defaultdict(list)
    with open(args.csv_path) as f:
        for row in csv.DictReader(f):
            series[row["counter"]].append((int(row["ts"]), float(row["value"])))

    if not series:
        print("没有计数器数据")
        return 1

    # 旧报告裁掉首秒；现在仅在显式指定时裁剪，避免默认把工作负载删掉。
    if "cycle-count" not in series or len(series["cycle-count"]) < 2:
        print("缺 cycle-count，无法定窗口")
        return 1
    cc_all = series["cycle-count"]
    t_start = cc_all[0][0] + int(args.skip_first_seconds * 1_000_000_000)
    cc = [s for s in cc_all if s[0] >= t_start]
    if len(cc) < 2:
        print("裁剪后不足两个周期采样点，无法计算")
        return 1
    t0, t1 = cc[0][0], cc[-1][0]

    def delta(name):
        s = [x for x in series.get(name, []) if t0 <= x[0] <= t1]
        return (s[-1][1] - s[0][1]) if len(s) >= 2 else None

    cyc = delta("cycle-count")
    print("注意：仅报告所采事件比值；读取计数器可能改变计数覆盖，不等于连续硬件利用率。")
    print(f"显式裁剪开头: {args.skip_first_seconds:g} s")
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
              f"   ← 所采周期中的 compute-active 比值；不是 ALU 或墙钟占用率")
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

    # 指令缓存事件的粒度未标定，不能当作执行/发射指令数。
    qh = delta("QPU-total-instr-cache-hit")
    qm = delta("QPU-total-instr-cache-miss")
    if qh is not None or qm is not None:
        qh = qh or 0.0
        qm = qm or 0.0
        tot = qh + qm
        print("\n=== QPU 指令缓存访问事件 ===")
        print(f"  instr-cache 命中/未命中 = {qh:,.0f} / {qm:,.0f}"
              f"  (命中率 {qh / tot * 100 if tot else 0:.2f} %)")
        print(f"  (instr-cache hit + miss) / cycle-count = {tot / cyc:.4f}")
        print("  注：这是缓存事件密度，不能据此推出 QPU 数量、每周期发射数或发射槽利用率。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
