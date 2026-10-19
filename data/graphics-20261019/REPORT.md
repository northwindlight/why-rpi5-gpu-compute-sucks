# Pi 5 无头图形性能记录

测试时间：2026-10-19 11:22:59 +0800。Debian 13，内核 6.18.50+rpt-rpi-2712。
实际 renderer 为 Broadcom / V3D 7.1.10.2，Mesa 26.3.0-devel (a5d39a4b74)。
桌面 OpenGL 与 OpenGL ES 都为 3.1。

## 方法

- glmark2 release，源码提交 `22c527cb0556f3a1ac4445aaa52cc532760928d5`。
- null/surfaceless 后端，离屏 FBO，无屏幕呈现或垂直同步限帧。
- 自选 5 个场景，每场景 2 秒；两种分辨率、两个 API，各跑两轮。不是完整默认套件。
- 性能运行用默认三 FBO。模型、纹理与 Phong 的像素校验使用单 FBO，两个 API 均通过。
- 未通过本测试脚本改变或锁定频率。全程 `fw-clk-v3d` 采样范围为 500–1150 MHz；
  正式八次性能运行内的 159 个采样点全为 **1150 MHz**。
- CPU 温度 44.65–52.35°C，为全程监控范围，含切换和调试。
- 这些是短时离屏基准成绩，不是浏览器、真实桌面、游戏或功耗测试。

## 两轮 FPS 范围

| 场景 | OpenGL 800×600 | GLES 800×600 | OpenGL 1080p | GLES 1080p |
| --- | ---: | ---: | ---: | ---: |
| build，VBO | 1482–1492 | 1469–1475 | 366–369 | 367–370 |
| texture，linear | 1288–1292 | 1282–1286 | 336–343 | 344 |
| shading，phong | 1239–1245 | 1243–1249 | 328–329 | 327–330 |
| effect2d，默认核 | 1206–1211 | 1194–1204 | 306 | 305–306 |
| desktop，四窗口 blur | 343–344 | 341–342 | 92 | 92 |

Vulkan：Debian vkmark 2025.01-1，headless 插件本次启动 SIGSEGV，未得到成绩。
日志走到 swapchain 格式选择；无符号调用栈位于 vkmark 主程序内，尚不能确定根因。
该项没有参与任何性能比较。

## 文件与复现

- `results.csv`：40 个场景结果，各含 FPS、帧时间、API、尺寸、轮次和参数。
- `manifest.json`：实际 argv、环境、每次起止与返回码。绝对路径记录的是采集时本机布局。
- `monitor.json`：0.5 秒间隔的频率、温度、loadavg 采样；不使用性能事件轮询。
- `*-round*.log`：完整工具输出；`*-validation.log`：像素校验结果。
- `vkmark-backtrace.log`：Vulkan 工具失败的调用栈；`vk-warmup.log` 因崩溃时未输出而为空。
- `SHA256SUMS`：原始采集文件的内容校验值。

构建和基准命令见仓库 README 第 6 节。不要拿自选五场景的总分与完整 glmark2 总分比较。
