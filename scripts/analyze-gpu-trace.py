#!/usr/bin/env python3
"""分析 MESA_GPU_TRACES=print_csv 的 GPU trace。

用法: analyze-gpu-trace.py <csv>

CSV 每行: frame,batch,time_ns,event[,k=v,...]
时间戳来自驱动的软件时间戳路径。这里只统计记录窗口；
窗口可包含同步、调度和采样边界延迟，不能直接等同 GPU 硬件忙碌时间。
"""
import sys
from collections import defaultdict


def parse(path):
    events = []
    with open(path) as f:
        header = f.readline()
        for line in f:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 4:
                continue
            frame, batch, ts, name = parts[0], parts[1], int(parts[2]), parts[3]
            kv = {}
            for p in parts[4:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k.strip()] = v.strip()
            events.append((frame, int(batch), ts, name, kv))
    return events


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ev = parse(sys.argv[1])

    kinds = defaultdict(int)
    for *_x, name, _kv in ev:
        kinds[name] += 1
    print("=== 事件类型 ===")
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {k:24s} {v}")

    # 配对 begin_/end_ 求每个 job 时长（用栈，支持嵌套）
    stacks = defaultdict(list)
    jobs = []
    for frame, batch, ts, name, kv in ev:
        if name.startswith("begin_"):
            stacks[name[6:]].append((ts, kv, frame, batch))
        elif name.startswith("end_"):
            key = name[4:]
            if stacks[key]:
                t0, kv0, f0, b0 = stacks[key].pop()
                jobs.append({"kind": key, "dur_ns": ts - t0, "ts": t0,
                             "frame": f0, "batch": b0, "kv": kv0})

    if not jobs:
        print("没有成对的 begin/end job")
        return 1

    ts_all = [e[2] for e in ev]
    span = max(ts_all) - min(ts_all)

    by_kind = defaultdict(list)
    for j in jobs:
        by_kind[j["kind"]].append(j["dur_ns"])

    print(f"\n=== job 时长（按类型）  trace 跨度 = {span/1e6:.1f} ms ===")
    print(f"{'job 类型':22s}{'个数':>6s}{'总时长ms':>12s}{'占比':>9s}"
          f"{'平均us':>10s}{'最大ms':>10s}")
    total_busy = 0
    for k, ds in sorted(by_kind.items(), key=lambda x: -sum(x[1])):
        s = sum(ds)
        total_busy += s
        print(f"{k:22s}{len(ds):6d}{s/1e6:12.1f}{s/span*100:8.1f}%"
              f"{s/len(ds)/1e3:10.1f}{max(ds)/1e6:10.2f}")

    # 求记录窗口的区间并集；这些窗口并不等于 GPU 纯执行区间。
    iv = sorted((j["ts"], j["ts"] + j["dur_ns"]) for j in jobs)
    merged = []
    for a, b in iv:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    busy = sum(b - a for a, b in merged)
    gaps = sum(a2 - b1 for (_, b1), (a2, _) in zip(merged, merged[1:]))

    print(f"\n=== 软件记录窗口覆盖 ===")
    print("  注意：窗口并集不能证明 GPU 满载，也不能排除提交、同步和访存开销。")
    print(f"  trace 跨度(首个事件→末个事件) : {span/1e6:10.1f} ms")
    print(f"  job 时长之和(会重复计重叠)    : {total_busy/1e6:10.1f} ms")
    print(f"  记录窗口并集（非硬件占用）        : {busy/1e6:10.1f} ms")
    print(f"  → 记录窗口并集 / 跨度           : {busy/span*100:9.1f} %")
    print(f"  记录窗口之间的空隙合计            : {gaps/1e6:10.1f} ms  "
          f"({gaps/span*100:.1f}%)")
    if merged:
        print(f"  空隙个数                      : {len(merged)-1}")

    print(f"\n=== 最长的 15 个 job ===")
    print(f"{'类型':16s}{'时长ms':>10s}{'batch':>7s}  参数")
    for j in sorted(jobs, key=lambda x: -x["dur_ns"])[:15]:
        kv = " ".join(f"{k}={v}" for k, v in j["kv"].items())
        print(f"{j['kind']:16s}{j['dur_ns']/1e6:10.3f}{j['batch']:7d}  {kv[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
