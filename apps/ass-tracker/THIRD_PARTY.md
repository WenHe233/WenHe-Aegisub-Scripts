# 许可与第三方组件

本工具采用仓库的 GPL-3.0-only 许可，见 `LICENSE`。完整包保留各组件的许可文件，并通过独立进程调用 FFmpeg。

- Python 3.12：PSF License，https://www.python.org/downloads/source/
- NumPy：BSD，https://github.com/numpy/numpy
- SciPy：BSD，https://github.com/scipy/scipy
- Pillow：HPND，https://github.com/python-pillow/Pillow
- OpenCV：Apache-2.0；opencv-python 分发材料另含第三方许可，https://github.com/opencv/opencv-python
- PyInstaller：GPL with bootloader exception，https://github.com/pyinstaller/pyinstaller
- Tcl/Tk：保留运行包内授权文件，https://www.tcl.tk/software/tcltk/download.html
- JSON Lua：rxi/json.lua，MIT；原许可保留在模块头部，https://github.com/rxi/json.lua
- FFmpeg 7.1.1：Gyan GPL v3 essentials build；保留上游 README、构建配置和许可材料，https://github.com/GyanD/codexffmpeg/releases/tag/7.1.1

FFmpeg 源码固定为提交 `db69d06eeeab4f46da15030a80d539efb4503ca8`，同一 Release 提供 `ffmpeg-source.tar.gz`。构建源地址及 SHA-256 固定在 `tools/build_tracker.py`。FFmpeg 静态构建包含上游 README 列出的第三方库，其来源与许可请同时参阅上游构建材料。

Python 依赖的分发许可复制到完整包 `licenses/`，FFmpeg 上游材料位于 `ffmpeg/upstream/`。运行包不是用户字体的分发包，不包含测试所用的商业字体。
