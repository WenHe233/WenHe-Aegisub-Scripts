# 合成多边形示例

不需要工作视频、字幕或额外字体。安装源码依赖后，从 `apps/ass-tracker` 运行：

```powershell
python examples/make_polygon_demo.py
python -m ass_tracker ../../build/polygon-demo/concave.job.json
```

生成器把无损视频和任务写入仓库忽略的 `build/polygon-demo`；需要 FFmpeg 在 PATH。也可通过「打开 Aegisub 任务」将生成的任务加载到完整包的独立窗口。

画面中的凹形纹理区域做平移、缩放和旋转，凹口及外围为独立变化的背景。任务默认「旋转」档，已经保存六点选区。点「开始追踪」，检查首尾轮廓；回到第 0 帧可拖动顶点，或点「重新框选」自行绘制。绿色为已生效选区，橙色为草稿；按 Esc 可取消草稿并恢复已完成的结果。

窗口可缩到 640×480；开始／保存按钮应仍然可见，上方设置区可滚动。观察选区在窗口缩放前后仍贴住相同纹理。示例用于练习窗口和选区，算法精度与失败处理由 `tests/test_regions.py` 的断言测试验证。
