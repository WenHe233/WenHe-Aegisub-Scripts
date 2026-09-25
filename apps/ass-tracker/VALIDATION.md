# 0.4.1 验证记录

验证日期：2026-09-25。只使用合成图像、合成字幕及匿名时间边界，不分发工作字幕、视频或字体。

## 自动测试

本机 Windows 10 x64、Python 3.12；渲染使用运行包同版 FFmpeg 7.1.1 / libass。

- 仓库测试 16 项：现有三个宏的发布规则、多文件 DependencyControl 源、版本门禁、发布失败不更新源、拒绝覆盖不同资产、版本及提交不匹配、离线缓存和失败升级。
- 应用测试 34 项：五档模型、约束自由度、异常匹配、遮挡、重复纹理、大位移及重复帧；真实 Tk 控件状态、旧任务恢复、结果失效、外观选项只重新生成字幕；Lua 导出／回填／取消／撤销接口。
- libass 渲染测试包含原始旋转／透视叠加、曲线、B 样条、多轮廓裁切、反向裁切、绘图倍率、对齐和 `pbo`。使用轮廓重叠或逐像素比较；算法的 0.25 像素目标由投影曲线凸包误差界约束，渲染栅格抗锯齿差异不等于几何误差。
- 三行时间样例在合成 24000/1001 视频上严格覆盖 2431～2549，共 119 帧。
- `tools/check_portable.py` 从中文临时路径运行真正的 `ASSTracker.exe`，清除 Python 环境变量并将 PATH 限为 Windows 系统目录。检查 Tk、OpenCV、SciPy、ASS 拟合和捆绑 FFmpeg 编解码。这验证无外部 Python／FFmpeg 的运行依赖，不等同于一台从未安装 Python 的干净虚拟机。

## 界面验证与模拟的区分

真实 Aegisub 9706-cibuilds-20caaabc0 已启动并加载宏及模块。发现并修复了 DependencyControl 的 Lua chunk 名称不带 `@` 时模块路径丢失首字符的问题，已加入回归测试。

本机界面自动化的截图接口返回 `SetIsBorderRequired failed / 0x80004002`，文件对话框控件操作也报缓存元素不可用。因而没有把“实际 Aegisub 点菜单、鼠标框选、点应用、看到回填并撤销”记为通过；这仍需要可用的界面环境做人工验收。

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
