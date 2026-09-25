"""Small Tk UI. Image coordinates remain in original video pixels."""
from pathlib import Path
import copy
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from .core import validate_job, read_reference, run_job, save_json, Cancelled
from .motion import MODES, LABELS, HINTS, toggled
from .subtitles import generate
from . import __version__


class App:
    def __init__(self, root, initial=None, bridge_dir=None):
        self.root = root
        self.bridge_dir = Path(bridge_dir) if bridge_dir else None
        self.bridge_job_id = None
        self.closing = False
        self.revision = 0
        self.need_regenerate = False
        root.title(f"ASS 追踪 {__version__}")
        root.geometry("1180x850")
        self.job = self.result = self.path = self.photo = None
        self.busy = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.drag = None
        self.scale_x = self.scale_y = 1
        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        if not self.bridge_dir:
            ttk.Button(toolbar, text="打开 Aegisub 任务", command=self.open_job).pack(side="left")
            ttk.Button(toolbar, text="更换视频路径", command=self.replace_video).pack(side="left", padx=5)
        self.title = ttk.Label(toolbar, text="先在 Aegisub 导出选中的字幕行")
        self.title.pack(side="left", padx=10)
        self.mode = tk.StringVar(value='similarity')
        motion = ttk.Frame(root, padding=(16,4))
        motion.pack(fill='x')
        ttk.Label(motion,text='追踪运动：').pack(side='left')
        self.motion_vars = []
        for i,label in enumerate(LABELS):
            var = tk.BooleanVar(value=i<=2)
            self.motion_vars.append(var)
            ttk.Checkbutton(motion,text=label,variable=var,
                command=lambda i=i:self.motion_changed(i),state='disabled' if i==0 else 'normal').pack(side='left',padx=8)
        self.mode_hint = ttk.Label(root,padding=(16,2))
        self.mode_hint.pack(anchor='w')
        appearance = ttk.Frame(root,padding=(16,4))
        appearance.pack(fill='x')
        self.move_clips = tk.BooleanVar(value=True)
        self.scale_appearance = tk.BooleanVar(value=True)
        ttk.Checkbutton(appearance,text='裁切跟随',variable=self.move_clips,command=self.appearance_changed).pack(side='left')
        ttk.Checkbutton(appearance,text='描边／阴影／模糊随缩放',variable=self.scale_appearance,
                        command=self.appearance_changed).pack(side='left',padx=16)
        self.advanced = tk.BooleanVar(value=False)
        ttk.Checkbutton(appearance,text='高级设置',variable=self.advanced,command=self.update_motion_ui).pack(side='right')
        settings = ttk.Frame(root, padding=(8, 0))
        self.settings = settings
        self.settings_after = appearance
        self.radius = tk.IntVar(value=220)
        self.threshold = tk.DoubleVar(value=.85)
        self.width = tk.IntVar(value=960)
        self.quantum = tk.DoubleVar(value=.5)
        self.translation_widgets = []
        for label, var, values in [("搜索半径（原图像素）", self.radius, None),
                                    ("最低匹配分数", self.threshold, None),
                                    ("分析宽度", self.width, [640, 960, 1280, 1920]),
                                    ("位移精度（像素）", self.quantum, [.25, .5, 1])]:
            title=ttk.Label(settings, text=label)
            title.pack(side="left", padx=(8, 3))
            widget = ttk.Combobox(settings, textvariable=var, values=values, width=6, state="readonly") if values else ttk.Entry(settings, textvariable=var, width=6)
            widget.pack(side="left")
            if var in (self.threshold,self.quantum): self.translation_widgets.extend([title,widget])
        ttk.Label(root, text="在参考帧框选同一运动平面的文字和边缘。启用后项会自动包含前项；遮挡或换镜头请分段。", padding=8).pack(anchor="w")
        self.canvas = tk.Canvas(root, width=1100, height=620, bg="#202428", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8)
        self.canvas.bind("<ButtonPress-1>", self.mouse_down)
        self.canvas.bind("<B1-Motion>", self.mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.mouse_up)
        nav = ttk.Frame(root, padding=8)
        nav.pack(fill="x")
        self.frame = tk.IntVar(value=0)
        self.slider = ttk.Scale(nav, from_=0, to=1, variable=self.frame)
        self.slider.pack(side="left", fill="x", expand=True)
        self.slider.bind("<ButtonRelease-1>", lambda _: self.show_frame())
        self.frame_entry = ttk.Entry(nav, textvariable=self.frame, width=9)
        self.frame_entry.pack(side="left", padx=5)
        self.frame_entry.bind("<Return>", lambda _: self.show_frame())
        ttk.Button(nav, text="查看此帧", command=self.show_frame).pack(side="left")
        ttk.Button(nav, text="设为参考帧", command=self.set_reference).pack(side="left", padx=5)
        self.status = tk.StringVar(value="所有帧号从 0 开始。框选区域是追踪目标，不是字幕的放置区域。")
        ttk.Label(root, textvariable=self.status, padding=(8, 4), wraplength=1100).pack(anchor="w")
        bottom = ttk.Frame(root, padding=8)
        bottom.pack(fill="x")
        self.run_button = ttk.Button(bottom, text="开始追踪", command=self.track)
        self.run_button.pack(side="left")
        ttk.Button(bottom, text="取消计算", command=self.cancel.set).pack(side="left", padx=5)
        self.progress = ttk.Progressbar(bottom, length=200)
        self.progress.pack(side="left", padx=8)
        self.reviewed = tk.BooleanVar(value=False)
        if not self.bridge_dir:
            ttk.Checkbutton(bottom, text="已检查跟随框", variable=self.reviewed).pack(side="left", padx=10)
        self.save_button = ttk.Button(bottom, text="应用并返回 Aegisub" if self.bridge_dir else "保存供 Aegisub 导入的结果", command=self.save, state="disabled")
        self.save_button.pack(side="right")
        for var in (self.radius,self.threshold,self.width,self.quantum):
            var.trace_add('write',lambda *_:self.invalidate())
        self.update_motion_ui()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        if initial:
            root.after(200, lambda: self.open_job(initial))

    def close(self):
        self.closing = True
        self.cancel.set()
        self.status.set('正在关闭追踪窗口…')
        # Let the worker stop FFmpeg and release its cache before Python exits.
        if not self.busy:
            self.root.destroy()

    def worker(self, function, done):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.run_button.configure(state="disabled")
        def run():
            try:
                value = function()
                self.events.put(("done", (done, value)))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        if self.bridge_dir and (self.bridge_dir / 'cancel').exists() and not self.closing:
            self.close()
            if not self.busy:
                return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    label, amount = payload
                    self.progress["value"] = amount * 100
                    self.status.set(f"{label} {amount:.0%}；窗口可响应，随时可以取消。")
                else:
                    self.busy = False
                    self.run_button.configure(state="normal")
                    if self.closing:
                        self.root.destroy()
                        return
                    if kind == "done":
                        callback, value = payload
                        callback(value)
                    elif kind == "error":
                        self.status.set(payload)
                        messagebox.showerror("未完成", payload)
                    elif kind == "cancelled":
                        self.status.set("已取消，未更改字幕。")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def invalidate(self):
        self.revision += 1
        self.result = None
        self.reviewed.set(False)
        self.save_button.configure(state="disabled")

    def update_motion_ui(self):
        level=MODES.index(self.mode.get())
        for i,var in enumerate(self.motion_vars): var.set(i<=level)
        self.mode_hint.configure(text='当前：'+'＋'.join(LABELS[:level+1])+'。'+HINTS[level])
        for widget in self.translation_widgets:
            if level==0: widget.pack(side='left',padx=3)
            else: widget.pack_forget()
        if self.advanced.get(): self.settings.pack(fill='x',after=self.settings_after)
        else: self.settings.pack_forget()

    def motion_changed(self,index):
        self.mode.set(toggled(self.mode.get(),index,self.motion_vars[index].get()))
        self.update_motion_ui()
        self.invalidate()

    def appearance_changed(self):
        self.need_regenerate = True
        self.save_button.configure(state='disabled')
        if self.busy or not self.result or self.result['status']!='complete': return
        self.need_regenerate = False
        result = copy.deepcopy(self.result)
        result['job'].update(move_clips=self.move_clips.get(),scale_appearance=self.scale_appearance.get())
        self.job.update(move_clips=self.move_clips.get(),scale_appearance=self.scale_appearance.get())
        revision=self.revision
        def rebuild():
            result['generated']=generate(result['job'],result['track'])
            return result
        self.worker(rebuild,lambda r:self.tracked(r,revision))

    def open_job(self, path=None):
        if self.busy:
            return
        if self.bridge_dir and self.job:
            return
        path = path or filedialog.askopenfilename(filetypes=[("追踪任务", "*.job.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            job = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not Path(job.get("video", "")).is_file():
                video = filedialog.askopenfilename(title="原视频路径已失效，请选择对应视频")
                if not video:
                    return
                job["video"] = video
            validate_job(job)
        except Exception as exc:
            messagebox.showerror("任务无效", str(exc))
            return
        self.job, self.path = job, Path(path)
        if self.bridge_dir:
            self.bridge_job_id = job['job_id']
        self.mode.set(job.get('mode','translation'))
        self.update_motion_ui()
        self.invalidate()
        self.title.configure(text=f"{len(job['lines'])} 行 · 帧 {job['start_frame']}—{job['end_frame']-1}")
        self.slider.configure(from_=job["start_frame"], to=job["end_frame"]-1)
        self.frame.set(job["reference_frame"])
        options = job.get("options", {})
        self.radius.set(options.get("search_radius", 220))
        self.threshold.set(options.get("threshold", .85))
        self.width.set(options.get("max_width", 960))
        self.quantum.set(options.get("quantum", .5))
        self.move_clips.set(job.get("move_clips", True))
        self.scale_appearance.set(job.get('scale_appearance',False))
        self.show_frame()

    def replace_video(self):
        if self.busy or not self.job or self.bridge_dir:
            return
        video = filedialog.askopenfilename(title="选择与任务帧号一致的原视频")
        if video:
            self.job["video"] = video
            self.invalidate()
            self.show_frame()

    def show_frame(self):
        if self.busy or not self.job:
            return
        frame = max(self.job["start_frame"], min(self.job["end_frame"]-1, round(self.frame.get())))
        self.frame.set(frame)
        job = dict(self.job, reference_frame=frame)
        self.status.set(f"正在解码第 {frame} 帧…")
        preview_width = max(100, min(1100, self.canvas.winfo_width(),
                            int(max(100, self.canvas.winfo_height()) * job['video_width'] / job['video_height'])))
        self.worker(lambda: read_reference(job, preview_width, self.cancel.is_set), lambda img: self.draw(img, frame))

    def draw(self, img, frame):
        self.canvas.delete("all")
        self.photo = ImageTk.PhotoImage(Image.fromarray(img))
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.scale_x = img.shape[1] / self.job["video_width"]
        self.scale_y = img.shape[0] / self.job["video_height"]
        self.displayed_frame = frame
        roi = self.job.get("roi")
        row = None
        if self.result:
            row = self.result["track"][frame-self.job["start_frame"]]
        if roi and (not row or row["ok"]):
            x, y, w, h = roi
            if row and 'H' in row:
                from .planar import warp, corners
                points=warp(corners(roi),row['H'])
                points*= [self.scale_x,self.scale_y]
                self.canvas.create_polygon(*points.ravel(),fill='',outline='#5ef59b',width=2,tags='roi')
            else:
                if row:
                    x += row["dx"]
                    y += row["dy"]
                self.canvas.create_rectangle(x*self.scale_x, y*self.scale_y, (x+w)*self.scale_x, (y+h)*self.scale_y,
                                             outline="#5ef59b", width=2, tags="roi")
        if row and row['ok'] and 'inliers' in row:
            self.status.set(f"帧 {frame} · 平面匹配 {row['inliers']} 点 · 95% 重投影误差 {row['error']:.2f} px。检查四边形是否贴住同一平面。")
        elif row and row["ok"]:
            self.status.set(f"帧 {frame} · 位移 ({row['dx']:.2f}, {row['dy']:.2f}) px · 匹配 {row['score']:.3f}。拖动帧条检查滚动转折。")
        elif row:
            self.status.set(f"帧 {frame} · {row.get('reason', '失锁')}。缩短任务、换参考帧或重新框选后再追踪。")
        else:
            self.status.set(f"帧 {frame} · 参考帧 {self.job['reference_frame']}。仅在参考帧可框选；此帧应与字幕排版时的画面一致。")

    def set_reference(self):
        if self.busy or not self.job:
            return
        frame = round(self.frame.get())
        if any(not line["start_frame"] <= frame < line["end_frame"] for line in self.job["lines"]):
            messagebox.showerror("参考帧无效", "所选字幕必须都在参考帧可见。请按不同出现时间分开追踪。")
            return
        self.job["reference_frame"] = frame
        self.job.pop("roi", None)
        self.invalidate()
        self.show_frame()

    def mouse_down(self, event):
        if self.busy or not self.job or getattr(self, "displayed_frame", None) != self.job["reference_frame"]:
            return
        self.invalidate()
        self.drag = (event.x, event.y)
        self.canvas.delete("roi")

    def mouse_move(self, event):
        if self.drag:
            self.canvas.delete("roi")
            self.canvas.create_rectangle(*self.drag, event.x, event.y, outline="#5ef59b", width=2, tags="roi")

    def mouse_up(self, event):
        if not self.drag:
            return
        x1, y1 = self.drag
        self.drag = None
        x2, y2 = event.x, event.y
        x1, x2 = sorted([max(0, min(self.job["video_width"], v/self.scale_x)) for v in [x1, x2]])
        y1, y2 = sorted([max(0, min(self.job["video_height"], v/self.scale_y)) for v in [y1, y2]])
        self.job["roi"] = [round(x1), round(y1), round(x2-x1), round(y2-y1)]
        self.status.set(f"追踪区域 x={x1:.0f}, y={y1:.0f}, 宽={x2-x1:.0f}, 高={y2-y1:.0f}。点击开始追踪。")

    def track(self):
        if self.busy or not self.job:
            return
        if not self.job.get("roi"):
            messagebox.showinfo("先框选", "请在参考帧拖动鼠标框选追踪区域。")
            return
        try:
            self.job["options"] = dict(search_radius=self.radius.get(), threshold=self.threshold.get(),
                                       max_width=self.width.get(), quantum=self.quantum.get())
        except tk.TclError:
            messagebox.showerror("参数错误", "搜索半径和匹配分数必须是数字。")
            return
        self.job["move_clips"] = self.move_clips.get()
        self.job['scale_appearance'] = self.scale_appearance.get()
        self.job['mode'] = self.mode.get()
        self.invalidate()
        self.need_regenerate = False
        revision=self.revision
        job = copy.deepcopy(self.job)
        self.worker(lambda: run_job(job, lambda *p: self.events.put(("progress", p)), self.cancel.is_set), lambda r:self.tracked(r,revision))

    def tracked(self, result, revision=None):
        if revision is not None and revision != self.revision:
            self.status.set('追踪期间参数已改变，请重新追踪。')
            return
        self.result = result
        if self.need_regenerate and result['status']=='complete':
            self.appearance_changed()
            return
        self.progress["value"] = 100
        if result["status"] == "complete":
            self.save_button.configure(state="normal")
            next_step = '确认后点击“应用并返回 Aegisub”。' if self.bridge_dir else '然后勾选已检查并保存。'
            self.status.set(f"追踪完成，生成 {len(result['generated'])} 行。请查看参考帧之外的帧，检查绿色框是否贴住目标，{next_step}")
        else:
            error = result["failures"][0]
            self.frame.set(error["frame"])
            self.status.set(f"第 {error['frame']} 帧停止：{error['reason']}。失败结果不会导入字幕。")
            messagebox.showwarning("追踪中断", self.status.get())
            self.show_frame()

    def save(self):
        if self.busy or self.need_regenerate or not self.result or self.result["status"] != "complete":
            return
        if not self.bridge_dir and not self.reviewed.get():
            messagebox.showinfo("检查轨迹", "请拖动帧条查看跟随框，确认后勾选“已检查跟随框”。")
            return
        if self.bridge_dir:
            if self.result.get('job_id') != self.bridge_job_id:
                messagebox.showerror('无法应用', '结果不属于本次 Aegisub 追踪。')
                return
            try:
                save_json(self.bridge_dir / 'result.json', self.result)
            except Exception as exc:
                messagebox.showerror('无法返回结果', str(exc))
                return
            self.close()
            return
        name = self.path.name.replace(".job.json", "") + ".result.json"
        path = filedialog.asksaveasfilename(initialdir=self.path.parent, initialfile=name, defaultextension=".result.json", filetypes=[("追踪结果", "*.result.json")])
        if path:
            save_json(path, self.result)
            save_json(Path(path).with_name(Path(path).name.replace(".result.json", "") + ".configured.job.json"), self.result["job"])
            self.status.set(f"已保存：{path}。返回 Aegisub，运行“ASS 追踪 / 2. 导入结果”。")


def main(initial=None, bridge_dir=None):
    root = tk.Tk()
    App(root, initial, bridge_dir)
    if bridge_dir:
        root.lift()
    root.mainloop()
