# ASS 追踪

从 Aegisub 选中参考帧上已排好版的屏字，一键打开追踪窗口；框选、追踪后点 **应用并返回 Aegisub**，结果会自动回填。原行保留为注释，可以撤销。

支持 Windows x64。完整包包含 Python 运行时及 FFmpeg，不需要另装 Python、配置 PATH 或购买 Mocha。追踪针对同一平面的整体运动。

## 安装

推荐从仓库的 DependencyControl 源安装 `ASS 追踪`，宏和联动组件会一同安装。首次点击追踪会下载匹配版本的完整运行包，并校验 SHA-256；首次下载期间 Aegisub 进度窗口可以取消。以后使用缓存可离线运行。

源地址：`https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json`

也可以从 [Releases](https://github.com/WenHe233/WenHe-Aegisub-Scripts/releases) 下载 `wenhe.ASSTracker-版本-Windows-x64.zip`，完整解压，在 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# 便携版或自定义路径：
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -AutomationRoot 'D:\Aegisub\automation'
```

安装器先验证并缓存完整包，再安装宏与组件；已有文件备份到自动化目录的 `backups`。旧 `ass_tracker.lua` 入口会移入备份以避免重复菜单。之后重新扫描自动化脚本或重启 Aegisub。

运行缓存：`%LOCALAPPDATA%\WenHe\AegisubScripts\ASSTracker\versions\版本`。不同版本分别缓存，升级失败不会覆盖旧运行包。宏、模块和程序需使用同一版本。校验失败时重新下载完整包，并将损坏版本目录移走后重装；不要混用新旧组件。

## 使用

1. 打开原视频与字幕，将播放位置停在已排好版的参考帧；选中的每行必须都有显式 `\pos(x,y)`，且在该帧可见。
2. 选择「自动化 → ASS 追踪 → 开始追踪（自动回填）」。Aegisub 的宏会等待追踪窗口结束。
3. 在参考帧拖出矩形，包含同一平面上的文字、角点和边缘。手机屏字建议包含气泡边缘或几组文字，避免混入手指和背景。
4. 选择运动，点击开始追踪。拖动帧条检查绿色轮廓，特别检查首尾、跳动和遮挡附近。
5. 点击「应用并返回 Aegisub」。关掉窗口或取消时不改字幕；失败结果不能应用。

五项逐级联动，开启后项会包含前项，取消前项会关闭后项。默认开启平移、缩放、旋转。

| 最高开启项 | 求解范围 | 选用建议 |
| --- | --- | --- |
| 平移 | X/Y 位置 | 滚动消息、纯平移 |
| 缩放 | 平移＋等比缩放 | 没有旋转的推拉 |
| 旋转 | 平移＋等比缩放＋旋转 | 普通屏字跟随 |
| 斜切 | 完整仿射，含非等比缩放 | 拉伸、平行四边形变化 |
| 透视 | 完整平面透视 | 手机屏幕转向、梯形变化 |

选项表示允许求解，不会强制画面产生相应运动。简单镜头优先使用足够描述其运动的较低档位。五档分别约束模型求解；失锁不会自动降档或编造后续轨迹。

高级设置包括搜索半径和分析宽度；纯平移还提供最低匹配分数、位移精度。提高分析宽度可保留细节，但增加内存和耗时。改变框选、参考帧或运动参数后需重追；只改裁切／外观跟随开关会复用轨迹重新生成字幕。

## 标签支持与边界

| 内容 | 行为 |
| --- | --- |
| `\pos`、`\org`、已有缩放／旋转／`\fax` | 在参考排版上叠加运动；字号不重复缩放 |
| 矩形及矢量 `\clip`／`\iclip` | 可跟随或保持屏幕位置；支持倍率 1～10、多轮廓、贝塞尔与 B 样条 |
| 单段纯绘图 `\p1`～`\p10` | 支持曲线、位置、对齐、基线偏移与已有几何变换 |
| 描边、阴影距离、`\blur` | 默认随缩放，支持样式继承及行内覆盖；可关闭 |
| `\be`、字体、颜色、透明度、层级 | 保留原设置 |

矢量路径遵守 ASS 的 `m/n/l/b/s/p/c` 语义。仿射直接变换控制点，透视下按轮廓自适应细分，误差目标为原视频 0.25 像素；最多 8192 个点／段，无法满足时提示简化。矩形裁切需要小数或形变时转成矢量，避免整数裁切丢失精度。

ASS 每段文字只有一组描边／模糊值，因此非等比缩放及透视外观使用原位置处局部面积缩放率平方根近似，阴影方向保留。`\be` 是迭代次数，不能当像素半径缩放。

目前不接受 `\t`、`\move`、淡入淡出、卡拉 OK、样式重置；改变形状的档位还要求单行统一几何、正缩放，暂不接受 `\fay`、旧式 `\a`／`\fr`、数字字重、换行和混合文字／绘图。请把排版拆成独立行、用 `\an`／`\frz` 和字体名称指定字重。每行最多一个裁切。错误会指出行号，拒绝静默丢掉效果。

## 帧数与常见问题

- 帧号从 0 开始，结束帧为不包含的边界；使用 Aegisub 当前视频时间码，不按平均 FPS 猜测。示例 `01:41.37–01:46.34` 在对应 24000/1001 视频中为 2431～2549，共 119 帧。
- 一次最多 2400 帧，解码缓存最多 768 MiB。按镜头分段；不同出现时间的消息分开处理。
- 低帧率动画有重复帧或突然跳动时，选择辨识度高的区域、适当扩大搜索半径；遮挡、画面外运动、重复文字和切镜仍可能失锁。
- 非平移档位要求视频与字幕分辨率宽高比一致；在 Aegisub 中先正确重采样。
- 首次窗口迟迟不打开：检查 Aegisub 进度提示和 GitHub 下载连接。可以使用完整包离线安装。
- 窗口异常退出：错误消息会给出会话临时目录的 `error.log`。取消时进程树和 FFmpeg 会退出。

## 源码运行与维护

使用 Python 3.12，安装 `requirements.txt`，并让 FFmpeg、ffprobe 可从 PATH 找到：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m ass_tracker
.\.venv\Scripts\python -m ass_tracker selection.job.json --headless --output result.json
```

Aegisub 保留手动导出任务／导入结果入口。任务的 `mode` 为 `translation / translation_scale / similarity / affine / perspective`；旧任务缺省按平移处理，缺少 `scale_appearance` 时保持旧外观行为。新任务记录工具版本和脚本属性。

测试：在本目录运行 `python -m unittest discover -s tests -v`；Lua 联动测试另需 `lupa==2.8`。合成渲染测试需要带 libass 的 FFmpeg。Windows 测试包含真实 Tk、进程启动与回填接口模拟，[验证记录](VALIDATION.md) 单独说明真实 Aegisub 界面验证范围。

版本以 `ass_tracker/VERSION` 为准，宏的 `script_version` 必须同步，CI 会校验。运行代码、模块、安装器、依赖或打包逻辑改变必须提升版本；文档和测试独立调整可不触发发布。仓库根目录运行 `python tools/build_tracker.py` 构建，首次需要安装 `requirements-build.txt`。

PR 自动测试并构建完整包；main 成功后发布资产并校验，最后更新 DependencyControl 源。可重跑失败流程，已有发布内容不覆盖。开发过程不要提交视频、工作字幕、任务结果、虚拟环境和本机路径配置。
