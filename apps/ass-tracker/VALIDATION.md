# 0.5.0 验证记录

验证日期：2026-09-25。只使用合成图像、合成字幕及匿名时间边界，不分发工作字幕、视频或字体。

## 自动测试

本机 Windows 10 x64、Python 3.12；渲染使用运行包同版 FFmpeg 7.1.1 / libass。

- 仓库测试 16 项：现有三个宏的发布规则、多文件 DependencyControl 源、版本门禁、发布失败不更新源、拒绝覆盖不同资产、版本及提交不匹配、离线缓存和失败升级。
- 应用测试 44 项：五档模型、约束自由度、异常匹配、遮挡、重复纹理、大位移及重复帧；真实 Tk 控件状态、旧任务恢复、结果失效、外观选项只重新生成字幕；Lua 导出／回填／取消／撤销接口。
- 新增区域测试覆盖倾斜四边形、凹形、64 点边界、自交／重复边／零面积／越界／过小区域；掩膜外的独立运动、纯色、重复纹理、非有限匹配值、遮挡和失锁。五档在独立背景场景下的投影顶点误差小于 1 像素；没有把凹形误判成折叠。
- Tk 测试覆盖 640×480、800×600、1180×850、1500×700；按 100%、125%、150%、200% 设置 Tk 缩放，并在控件创建前单独重复四档缩放。断言导航／开始／应用按钮位于客户区内，完整预览不被裁切，只有设置区滚动。窗口缩放不调用解码器，坐标往返、选区和追踪结果保持不变；高级对话框和长错误提示也覆盖。**这是实际 Tk 几何与 API 检查，不等于四档 Windows 桌面 DPI 的人工视觉验收。**
- 多边形绘制、点击首点闭合、Enter 对应命令、撤点、取消、拖点、非法编辑保留旧选区、重画、切回矩形、保存／恢复，以及矩阵变换后的凹形预览均通过 API 测试。0.4.0／0.4.1 任务仅独立窗口迁移，桥接仍拒绝版本不匹配。
- libass 渲染测试包含原始旋转／透视叠加、曲线、B 样条、多轮廓裁切、反向裁切、绘图倍率、对齐和 `pbo`。使用轮廓重叠或逐像素比较；算法的 0.25 像素目标由投影曲线凸包误差界约束，渲染栅格抗锯齿差异不等于几何误差。
- 三行时间样例在合成 24000/1001 视频上严格覆盖 2431～2549，共 119 帧。
- `tools/check_portable.py` 从中文临时路径运行真正的 `ASSTracker.exe`，清除 Python 环境变量并将 PATH 限为 Windows 系统目录。检查 Tk、OpenCV、SciPy、ASS 拟合和捆绑 FFmpeg 编解码。这验证无外部 Python／FFmpeg 的运行依赖，不等同于一台从未安装 Python 的干净虚拟机。

## 界面验证与模拟的区分

本次安装后实际启动 Aegisub 9706-cibuilds-20caaabc0，未出现脚本加载错误，「自动化」菜单可见「ASS 追踪」。0.5.0 的独立窗口已用 25 帧合成任务实际启动，程序正常退出；相同任务的凹形追踪完整通过。未使用或修改工作字幕。

本次重试截图接口仍返回 `SetIsBorderRequired failed / 0x80004002`，Tk 的辅助功能树只暴露通用窗格，不能可靠定位画面和顶点。因而没有把“实际 Aegisub 点菜单、鼠标框选／拖点、点应用、看到回填并撤销”记为通过；四档 Windows DPI 的完整鼠标视觉验收仍需可用的界面环境。

`test_lua_to_real_python_gui_tracking_and_back` 使用真实 LuaJIT、Win32 进程桥、Python/Tk、FFmpeg 和追踪程序，但 Aegisub API 由内存模型模拟，框选与按钮由测试调用。它验证联动及回填协议，不能替代上面的完整界面验收。

## 可复现命令

0.4.0 的首次 Actions 构建通过，并已验证公开资产、SHA-256、逐文件校验及发布重跑。但公开包启动器测试发现 Windows PowerShell 没有等待 GUI 子进程的问题；0.4.1 改为显式等待进程，并把真实冻结程序的启动／取消检查加入 `tools/check_portable.py`。使用 0.4.1 或更高版本，旧资产保持不变。

```powershell
python tools/build_tracker.py --prepare-ffmpeg
$env:PATH = (Resolve-Path 'build/test-ffmpeg').Path + ';' + $env:PATH
python -m unittest discover -s tests -v
Push-Location apps/ass-tracker
python -m unittest discover -s tests -v
Pop-Location
python tools/build_tracker.py
python tools/check_portable.py
```

GitHub Actions 会在每次 PR／发布中重新执行这些检查。首次发布后的公开资产、SHA-256、DependencyControl 源以及下载缓存链路另外校验；以对应 Actions 运行记录和 Release 的 `release-manifest.json` 为准。

## 正式包安装复验（0.5.0）

[0.5.0 发布流水线](https://github.com/WenHe233/WenHe-Aegisub-Scripts/actions/runs/36099136621) 全部通过，源码提交为 `73413a0888c5801284ce324a21bfaea5aafcc9be`。发布后由 PowerShell 启动器从公开 Release 重新下载运行包，校验 SHA-256、版本、源码提交及逐文件哈希；另验证 DependencyControl 宏／模块的全部公开 URL 和 SHA-1。

公开 EXE 在移除外部 Python／FFmpeg PATH 的环境通过自检，并完成 25 帧六点凹形任务。实际验证默认安装、自定义中文路径、旧入口迁移、已有宏备份、0.4.1／0.5.0 并存缓存及缓存启动／取消／等待退出。已安装文件与公开包逐字节一致。原有三个宏的 DependencyControl 配置保持不变。

对 0.5.0 执行了真实发布重跑：复用已发布包并校验全部资产及源文件，识别已发布源后正常结束，未覆盖资产或重复提交。

## 历史正式包安装复验（0.4.1）

[0.4.1 发布流水线](https://github.com/WenHe233/WenHe-Aegisub-Scripts/actions/runs/36096165323) 全部通过。发布后再次由启动器从公开 Release 下载完整包并验证 SHA-256、版本及每个运行文件，确认源码提交为 `c7af45147de092b1591817dbfd9a02918f2b7e16`。

公开包在移除外部 Python／FFmpeg PATH 的环境中通过自检，并完成 25 帧合成镜头的追踪；缓存离线启动、图形程序取消和等待退出通过。默认 Aegisub 安装路径、自定义中文路径、原宏备份及旧入口迁移均已实测。DependencyControl 宏及所有模块文件的公开 URL／SHA-1 均重新校验，原有三个宏的源配置未改变。

发布重跑使用已发布的 0.4.0 资产进行实际验证：复用同一提交的包，逐文件及资产校验成功后识别已发布源，没有覆盖资产或重复提交。0.4.1 保持相同发布逻辑。
