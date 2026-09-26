#!/usr/bin/env bash
# 用法: run-clpeak.sh <tag>   tag 例如 baseline / release
# 全量跑 clpeak(clpeak 2.1.4)：OpenCL(rusticl/V3D) + Vulkan(V3D + llvmpipe) + CPU(A76)
set -u
TAG="${1:?usage: $0 <tag>}"
OUT="$(cd "$(dirname "$0")/../data" && pwd)"
export RUSTICL_ENABLE=v3d

{
  echo "==================== clpeak run: $TAG ===================="
  echo "date        : $(date -Iseconds)"
  echo "clpeak      : $(clpeak --version 2>&1)"
  echo "mesa driver :"
  clinfo -l 2>&1 | sed 's/^/  /'
  echo "  v3dv (Vulkan V3D) / rusticl 版本串见下方各段输出"
  echo

  echo "-------------------- 1/3 OpenCL (rusticl / V3D) --------------------"
  clpeak --opencl --json-file "$OUT/$TAG-opencl.json" 2>&1
  echo "[exit=$?]"

  echo
  echo "-------------------- 2/3 Vulkan (V3D + llvmpipe) -------------------"
  clpeak --vulkan --json-file "$OUT/$TAG-vk.json" 2>&1
  echo "[exit=$?]"

  echo
  echo "-------------------- 3/3 CPU (Cortex-A76) -------------------------"
  clpeak --cpu --json-file "$OUT/$TAG-cpu.json" 2>&1
  echo "[exit=$?]"

  echo
  echo "==================== done: $TAG ===================="
} > "$OUT/$TAG.txt" 2>&1
echo "wrote $OUT/$TAG.txt"
ls -l "$OUT/$TAG"*.json
