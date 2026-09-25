"""Responsive Tk UI. Selection and tracking always use original video pixels."""
from pathlib import Path
import copy
import json
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from .core import validate_job, read_reference, run_job, save_json, Cancelled
from .motion import MODES, LABELS, HINTS, toggled
from .subtitles import generate
from .regions import parse_region, upgrade_standalone_job
from .layout import FlowFrame, SettingsPanel, Viewport, size_window
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
        size_window(root)
        self.job = self.result = self.path = self.photo = None
        self.preview_image = self.viewport = None
        self.displayed_frame = None
        self.busy = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.drag = self.draft_points = None
        self.draft_closed = False
        self.advanced_window = None
        self.render_timer = None
        self.poll_timer = None
        self.initial_timer = None
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1, minsize=32)
        self.settings_panel = SettingsPanel(root)
        self.settings_panel.grid(row=0, column=0, sticky='ew', padx=8, pady=(6,0))
        settings = self.settings_panel.body
        toolbar = FlowFrame(settings)
        toolbar.pack(fill='x')
        if not self.bridge_dir:
            toolbar.add(ttk.Button(toolbar, text='打开 Aegisub 任务', command=self.open_job))
            toolbar.add(ttk.Button(toolbar, text='更换视频路径', command=self.replace_video))
        self.title = ttk.Label(toolbar, text='先在 Aegisub 导出选中的字幕行')
        toolbar.add(self.title)
        self.mode = tk.StringVar(value='similarity')
        motion = FlowFrame(settings)
        motion.pack(fill='x')
        motion.add(ttk.Label(motion, text='追踪运动：'))
        self.motion_vars = []
        for i,label in enumerate(LABELS):
            var = tk.BooleanVar(value=i<=2)
            self.motion_vars.append(var)
            motion.add(ttk.Checkbutton(motion, text=label, variable=var,
                command=lambda i=i:self.motion_changed(i), state='disabled' if i==0 else 'normal'))
        self.mode_hint = ttk.Label(settings, padding=(4,2), wraplength=700)
        self.mode_hint.pack(fill='x')
        appearance = FlowFrame(settings)
        appearance.pack(fill='x')
        self.move_clips = tk.BooleanVar(value=True)
        self.scale_appearance = tk.BooleanVar(value=True)
        appearance.add(ttk.Checkbutton(appearance, text='裁切跟随', variable=self.move_clips, command=self.appearance_changed))
        appearance.add(ttk.Checkbutton(appearance, text='描边／阴影／模糊随缩放', variable=self.scale_appearance, command=self.appearance_changed))
        appearance.add(ttk.Button(appearance, text='高级设置…', command=self.open_advanced))
        self.radius = tk.IntVar(value=220)
        self.threshold = tk.DoubleVar(value=.85)
        self.width = tk.IntVar(value=960)
        self.quantum = tk.DoubleVar(value=.5)
        selection = FlowFrame(settings)
        selection.pack(fill='x')
        self.tool = tk.StringVar(value='rectangle')
        for value,label in [('rectangle','矩形'),('polygon','多边形')]:
            selection.add(ttk.Radiobutton(selection, text=label, value=value, variable=self.tool, command=self.tool_changed))
        selection.add(ttk.Button(selection, text='重新框选', command=self.reset_region))
        selection.add(ttk.Button(selection, text='闭合多边形', command=self.finish_polygon))
        self.help = ttk.Label(settings, padding=(4,2), wraplength=700,
            text='在参考帧选同一运动平面。多边形：逐点点击，点首点或 Enter 闭合；Backspace 撤点，Esc 取消；闭合后拖动顶点。')
        self.help.pack(fill='x')
        self.canvas = tk.Canvas(root, width=1, height=1, bg='#202428', highlightthickness=0, takefocus=True)
        self.canvas.grid(row=1, column=0, sticky='nsew', padx=8, pady=4)
        self.canvas.bind('<ButtonPress-1>', self.mouse_down)
        self.canvas.bind('<B1-Motion>', self.mouse_move)
        self.canvas.bind('<ButtonRelease-1>', self.mouse_up)
        self.canvas.bind('<Return>', lambda _:self.finish_polygon())
        self.canvas.bind('<BackSpace>', lambda _:self.undo_point())
        self.canvas.bind('<Escape>', lambda _:self.cancel_edit())
        self.canvas.bind('<Configure>', self.schedule_render)
        self.footer = ttk.Frame(root, padding=(8,0,8,6))
        self.footer.grid(row=2, column=0, sticky='ew')
        self.frame = tk.IntVar(value=0)
        self.slider = ttk.Scale(self.footer, from_=0, to=1, variable=self.frame)
        self.slider.pack(fill='x')
        self.slider.bind('<ButtonRelease-1>', lambda _:self.show_frame())
        nav = FlowFrame(self.footer)
        nav.pack(fill='x')
        self.frame_entry = nav.add(ttk.Entry(nav, textvariable=self.frame, width=9))
        self.frame_entry.bind('<Return>', lambda _:self.show_frame())
        nav.add(ttk.Button(nav, text='查看此帧', command=self.show_frame))
        nav.add(ttk.Button(nav, text='设为参考帧', command=self.set_reference))
        self.status = tk.StringVar(value='所有帧号从 0 开始。选区只决定追踪取样，不改变字幕裁切。')
        self.status_label = tk.Label(self.footer, textvariable=self.status, height=2, anchor='w', justify='left', wraplength=700)
        self.status_label.pack(fill='x')
        self.status_label.bind('<Double-Button-1>', lambda _:messagebox.showinfo('完整状态', self.status.get(), parent=root))
        self.progress = ttk.Progressbar(self.footer)
        self.progress.pack(fill='x', pady=2)
        self.reviewed = tk.BooleanVar(value=False)
        if not self.bridge_dir:
            ttk.Checkbutton(self.footer, text='已检查跟随框', variable=self.reviewed).pack(anchor='w')
        bottom = FlowFrame(self.footer)
        bottom.pack(fill='x')
        self.run_button = bottom.add(ttk.Button(bottom, text='开始追踪', command=self.track))
        bottom.add(ttk.Button(bottom, text='取消计算', command=self.cancel.set))
        self.save_button = bottom.add(ttk.Button(bottom, text='应用并返回 Aegisub' if self.bridge_dir else '保存追踪结果', command=self.save, state='disabled'))
        for var in (self.radius,self.threshold,self.width,self.quantum):
            var.trace_add('write',lambda *_:self.invalidate())
        self.update_motion_ui()
        self.settings_panel.bind_wheel()
        root.bind('<Configure>', self.resized, add='+')
        root.bind('<Destroy>', self.destroyed, add='+')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_timer = root.after(100, self.poll)
        if initial:
            self.initial_timer = root.after(200, lambda:self.open_job(initial))

    def resized(self, event):
        if event.widget != self.root:return
        width=max(80,event.width-40)
        self.mode_hint.configure(wraplength=width)
        self.help.configure(wraplength=width)
        self.status_label.configure(wraplength=width)
        self.settings_panel.limit=max(50,min(int(event.height*.32),event.height-self.footer.winfo_reqheight()-50))
        self.settings_panel.resize()

    def destroyed(self, event):
        if event.widget != self.root:return
        self.cancel.set()
        for timer in (self.render_timer,self.poll_timer,self.initial_timer):
            if timer:
                self.root.after_cancel(timer)

    def close(self):
        self.closing = True
        self.cancel.set()
        self.status.set('正在关闭追踪窗口…')
        if not self.busy:self.root.destroy()

    @property
    def editing(self):
        return self.draft_points is not None or self.drag is not None

    def update_buttons(self):
        ready=not self.busy and not self.editing
        self.run_button.configure(state='normal' if ready else 'disabled')
        self.save_button.configure(state='normal' if ready and not self.need_regenerate and self.result and self.result['status']=='complete' else 'disabled')

    def worker(self, function, done):
        if self.busy:return
        self.busy = True
        self.cancel.clear()
        self.update_buttons()
        def run():
            try:self.events.put(('done',(done,function())))
            except Cancelled:self.events.put(('cancelled',None))
            except Exception as exc:self.events.put(('error',str(exc)))
        threading.Thread(target=run,daemon=True).start()

    def poll(self):
        if self.bridge_dir and (self.bridge_dir/'cancel').exists() and not self.closing:
            self.close()
            if not self.busy:return
        try:
            while True:
                kind,payload=self.events.get_nowait()
                if kind=='progress':
                    label,amount=payload
                    self.progress['value']=amount*100
                    self.status.set(f'{label} {amount:.0%}；随时可以取消。')
                else:
                    self.busy=False
                    if self.closing:
                        self.root.destroy();return
                    if kind=='done':
                        callback,value=payload
                        callback(value)
                    elif kind=='error':
                        self.status.set(payload)
                        messagebox.showerror('未完成',payload,parent=self.root)
                    elif kind=='cancelled':self.status.set('已取消，未更改字幕。')
                    self.update_buttons()
        except queue.Empty:pass
        self.poll_timer=self.root.after(100,self.poll)

    def invalidate(self):
        self.revision+=1
        self.result=None
        self.reviewed.set(False)
        self.update_buttons()

    def update_motion_ui(self):
        level=MODES.index(self.mode.get())
        for i,var in enumerate(self.motion_vars):var.set(i<=level)
        self.mode_hint.configure(text='当前：'+'＋'.join(LABELS[:level+1])+'。'+HINTS[level])

    def motion_changed(self,index):
        self.mode.set(toggled(self.mode.get(),index,self.motion_vars[index].get()))
        self.update_motion_ui()
        self.invalidate()

    def open_advanced(self):
        if self.advanced_window and self.advanced_window.winfo_exists():
            self.advanced_window.lift();return
        dialog=tk.Toplevel(self.root)
        self.advanced_window=dialog
        dialog.title('高级设置');dialog.transient(self.root);dialog.resizable(False,False)
        body=ttk.Frame(dialog,padding=16);body.pack(fill='both',expand=True)
        specs=[('搜索半径（原图像素）',self.radius,None),('分析宽度',self.width,[640,960,1280,1920])]
        if self.mode.get()=='translation':
            specs.extend([('最低匹配分数',self.threshold,None),('位移精度（像素）',self.quantum,[.25,.5,1])])
        drafts=[]
        for row,(label,var,values) in enumerate(specs):
            draft=tk.StringVar(value=str(var.get()));drafts.append((var,draft))
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',padx=4,pady=6)
            control=ttk.Combobox(body,textvariable=draft,values=values,state='readonly',width=10) if values else ttk.Entry(body,textvariable=draft,width=12)
            control.grid(row=row,column=1,padx=4,pady=6)
        def apply():
            try:
                values=[(var,float(draft.get())) for var,draft in drafts]
                for var,value in values:
                    if not math.isfinite(value) or value<=0:raise ValueError('参数必须是正数。')
                    if var is self.radius and (value!=int(value) or value>100000):raise ValueError('搜索半径必须为 1～100000 的整数。')
                    if var is self.threshold and value>1:raise ValueError('匹配分数必须在 0～1 之间。')
                for var,value in values:
                    value=int(value) if isinstance(var,tk.IntVar) else value
                    if var.get()!=value:var.set(value)
            except ValueError as exc:
                messagebox.showerror('参数错误',str(exc),parent=dialog);return
            dialog.destroy()
        buttons=ttk.Frame(body);buttons.grid(row=len(specs),column=0,columnspan=2,pady=(12,0))
        ttk.Button(buttons,text='确定',command=apply).pack(side='left',padx=6)
        ttk.Button(buttons,text='取消',command=dialog.destroy).pack(side='left',padx=6)
        dialog.bind('<Escape>',lambda _:dialog.destroy())
        dialog.grab_set()

    def appearance_changed(self):
        self.need_regenerate=True
        self.update_buttons()
        if self.busy or not self.result or self.result['status']!='complete':return
        self.need_regenerate=False
        result=copy.deepcopy(self.result)
        result['job'].update(move_clips=self.move_clips.get(),scale_appearance=self.scale_appearance.get())
        self.job.update(move_clips=self.move_clips.get(),scale_appearance=self.scale_appearance.get())
        revision=self.revision
        def rebuild():
            result['generated']=generate(result['job'],result['track'])
            return result
        self.worker(rebuild,lambda r:self.tracked(r,revision))

    def open_job(self,path=None):
        if self.busy or (self.bridge_dir and self.job):return
        path=path or filedialog.askopenfilename(filetypes=[('追踪任务','*.job.json'),('JSON','*.json')])
        if not path:return
        try:
            job=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if not self.bridge_dir:job=upgrade_standalone_job(job)
            if not Path(job.get('video','')).is_file():
                video=filedialog.askopenfilename(title='原视频路径已失效，请选择对应视频')
                if not video:return
                job['video']=video
            validate_job(job)
        except Exception as exc:
            messagebox.showerror('任务无效',str(exc),parent=self.root);return
        self.job,self.path=job,Path(path)
        self.drag=self.draft_points=None
        self.preview_image=self.viewport=None
        self.canvas.delete('all')
        if self.bridge_dir:self.bridge_job_id=job['job_id']
        self.mode.set(job.get('mode','translation'))
        self.tool.set('polygon' if 'roi_polygon' in job else 'rectangle')
        self.update_motion_ui();self.invalidate()
        self.title.configure(text=f"{len(job['lines'])} 行 · 帧 {job['start_frame']}—{job['end_frame']-1}")
        self.slider.configure(from_=job['start_frame'],to=job['end_frame']-1)
        self.frame.set(job['reference_frame'])
        options=job.get('options',{})
        self.radius.set(options.get('search_radius',220));self.threshold.set(options.get('threshold',.85))
        self.width.set(options.get('max_width',960));self.quantum.set(options.get('quantum',.5))
        self.move_clips.set(job.get('move_clips',True));self.scale_appearance.set(job.get('scale_appearance',False))
        self.show_frame()

    def replace_video(self):
        if self.busy or not self.job or self.bridge_dir:return
        video=filedialog.askopenfilename(title='选择与任务帧号一致的原视频')
        if video:
            self.job['video']=video;self.invalidate();self.show_frame()

    def show_frame(self):
        if self.busy or not self.job:return
        frame=max(self.job['start_frame'],min(self.job['end_frame']-1,round(self.frame.get())))
        self.frame.set(frame)
        self.cancel_edit(announce=False)
        job=dict(self.job,reference_frame=frame)
        self.status.set(f'正在解码第 {frame} 帧…')
        self.worker(lambda:read_reference(job,min(1920,job['video_width']),self.cancel.is_set),lambda img:self.draw(img,frame))

    def row_for_frame(self):
        if self.result and self.displayed_frame is not None:
            return self.result['track'][self.displayed_frame-self.job['start_frame']]

    def draw(self,img,frame):
        self.preview_image=Image.fromarray(img)
        self.displayed_frame=frame
        self.render_preview()
        row=self.row_for_frame()
        if row and row['ok'] and 'inliers' in row:
            self.status.set(f"帧 {frame} · 平面匹配 {row['inliers']} 点 · 95% 重投影误差 {row['error']:.2f} px。检查轮廓是否贴住同一平面。")
        elif row and row['ok']:
            self.status.set(f"帧 {frame} · 位移 ({row['dx']:.2f}, {row['dy']:.2f}) px · 匹配 {row['score']:.3f}。拖动帧条检查跟随轮廓。")
        elif row:self.status.set(f"帧 {frame} · {row.get('reason','失锁')}。缩短任务、换参考帧或重新框选后再追踪。")
        else:self.status.set(f"帧 {frame} · 参考帧 {self.job['reference_frame']}。仅在参考帧可框选；此帧应与字幕排版时的画面一致。")

    def schedule_render(self,event=None):
        if self.render_timer:self.root.after_cancel(self.render_timer)
        self.render_timer=self.root.after_idle(self.render_preview)

    def render_preview(self):
        self.render_timer=None
        if self.preview_image is None or not self.job:return
        self.viewport=Viewport.fit(self.job['video_width'],self.job['video_height'],self.canvas.winfo_width(),self.canvas.winfo_height())
        v=self.viewport
        self.photo=ImageTk.PhotoImage(self.preview_image.resize((v.width,v.height),Image.Resampling.LANCZOS))
        self.canvas.delete('image')
        self.canvas.create_image(v.x,v.y,image=self.photo,anchor='nw',tags='image')
        self.canvas.tag_lower('image')
        self.render_selection()

    def render_selection(self):
        self.canvas.delete('roi')
        if not self.viewport or not self.job:return
        points=None;closed=True;handles=False
        if self.draft_points is not None:
            points=self.draft_points;closed=self.draft_closed
            handles=self.tool.get()=='polygon'
        elif self.job.get('roi') or self.job.get('roi_polygon'):
            region=parse_region(self.job)
            points=region.points
            row=self.row_for_frame()
            if row and not row['ok']:return
            if row:
                if 'H' in row:
                    from .planar import warp
                    points=warp(points,row['H'])
                else:points=points+[row['dx'],row['dy']]
            handles=region.polygon and self.tool.get()=='polygon' and self.displayed_frame==self.job['reference_frame']
        if points is None or len(points)==0:return
        coords=[self.viewport.to_canvas(p) for p in points]
        flat=[v for p in coords for v in p]
        color='#ffcc66' if self.editing else '#5ef59b'
        if closed and len(points)>=3:self.canvas.create_polygon(*flat,fill='',outline=color,width=2,tags='roi')
        elif len(points)>1:self.canvas.create_line(*flat,fill=color,width=2,tags='roi')
        if handles:
            radius=self.handle_radius()
            for i,(x,y) in enumerate(coords):
                self.canvas.create_oval(x-radius,y-radius,x+radius,y+radius,fill=color if i==0 else '#202428',outline=color,width=2,tags='roi')

    def handle_radius(self):
        return max(4,float(self.root.tk.call('tk','scaling'))*3)

    def set_reference(self):
        if self.busy or not self.job:return
        frame=round(self.frame.get())
        if any(not line['start_frame']<=frame<line['end_frame'] for line in self.job['lines']):
            messagebox.showerror('参考帧无效','所选字幕必须都在参考帧可见。请按不同出现时间分开追踪。',parent=self.root);return
        self.job['reference_frame']=frame
        self.job.pop('roi',None);self.job.pop('roi_polygon',None)
        self.cancel_edit(announce=False);self.invalidate();self.show_frame()

    def tool_changed(self):
        self.cancel_edit(announce=False)
        self.status.set('矩形：在参考帧拖动鼠标。' if self.tool.get()=='rectangle' else '多边形：逐点点击；闭合后拖顶点。重画已有多边形请点“重新框选”。')

    def reset_region(self):
        if self.busy or not self.job:return
        if self.displayed_frame!=self.job['reference_frame']:
            self.frame.set(self.job['reference_frame']);self.show_frame()
        self.drag=None;self.draft_points=[];self.draft_closed=False
        self.canvas.focus_set();self.update_buttons();self.render_selection()
        self.status.set('请重新框选。Esc 取消并保留原选区和追踪结果。')

    def cancel_edit(self,announce=True):
        was_editing=self.editing
        self.drag=self.draft_points=None;self.draft_closed=False
        self.update_buttons();self.render_selection()
        if announce and was_editing:self.status.set('已取消编辑，保留原选区和追踪结果。')

    def undo_point(self):
        if self.busy or self.tool.get()!='polygon' or self.draft_points is None:return
        if self.draft_points:self.draft_points.pop()
        self.drag=None;self.draft_closed=False
        self.render_selection();self.update_buttons()

    def commit_region(self,candidate):
        try:
            region=parse_region(candidate)
            if min(region.bounds[2:])<12:raise ValueError('选区太小：宽和高至少为 12 个原视频像素。')
        except ValueError as exc:
            self.status.set(f'选区无效：{exc} 请调整顶点，或按 Esc 取消。')
            self.update_buttons();return False
        old=(self.job.get('roi'),self.job.get('roi_polygon'))
        region.apply(self.job)
        changed=old!=(self.job.get('roi'),self.job.get('roi_polygon'))
        self.drag=self.draft_points=None;self.draft_closed=False
        if changed:self.invalidate()
        self.update_buttons();self.render_selection()
        x,y,w,h=region.bounds
        label=f'{len(region.points)} 点多边形' if region.polygon else '矩形'
        self.status.set(f'{label} · x={x:.1f}, y={y:.1f}, 宽={w:.1f}, 高={h:.1f}。点击开始追踪。')
        return True

    def finish_polygon(self):
        if self.busy or not self.job or self.tool.get()!='polygon' or self.draft_points is None:return
        candidate=dict(self.job,roi_polygon=self.draft_points)
        self.draft_closed=len(self.draft_points)>=3
        self.commit_region(candidate);self.render_selection()

    def nearest_vertex(self,points,event):
        radius=self.handle_radius()*2
        for i,point in enumerate(points):
            x,y=self.viewport.to_canvas(point)
            if math.hypot(x-event.x,y-event.y)<=radius:return i

    def mouse_down(self,event):
        if self.busy or not self.job or not self.viewport or self.displayed_frame!=self.job['reference_frame']:return
        point=self.viewport.to_video(event.x,event.y)
        if point is None:return
        self.canvas.focus_set()
        if self.tool.get()=='rectangle':
            self.drag=('rectangle',point);self.draft_points=[point]*4;self.draft_closed=True
        else:
            if self.draft_points is None and 'roi_polygon' in self.job:
                index=self.nearest_vertex(self.job['roi_polygon'],event)
                if index is None:
                    self.status.set('拖动已有顶点；要绘制新的多边形请点“重新框选”。');return
                self.draft_points=copy.deepcopy(self.job['roi_polygon']);self.draft_closed=True
                self.drag=('vertex',index)
            elif self.draft_closed:
                index=self.nearest_vertex(self.draft_points,event)
                if index is not None:self.drag=('vertex',index)
            else:
                if self.draft_points is None:self.draft_points=[]
                if len(self.draft_points)>=3 and self.nearest_vertex(self.draft_points[:1],event)==0:
                    self.finish_polygon();return
                if len(self.draft_points)>=64:
                    self.status.set('最多 64 个顶点。请按 Enter 闭合，或 Backspace 撤销。');return
                self.draft_points.append(point)
                self.status.set(f'已添加 {len(self.draft_points)} 点。点首点或 Enter 闭合，Backspace 撤点，Esc 取消。')
        self.update_buttons();self.render_selection()

    def mouse_move(self,event):
        if not self.drag or not self.viewport:return
        point=self.viewport.to_video(event.x,event.y,clamp=True)
        kind,value=self.drag
        if kind=='vertex':self.draft_points[value]=point
        else:
            x1,x2=sorted([value[0],point[0]]);y1,y2=sorted([value[1],point[1]])
            self.draft_points=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
        self.render_selection()

    def mouse_up(self,event):
        if not self.drag:return
        self.mouse_move(event)
        kind,_=self.drag;self.drag=None
        candidate=dict(self.job)
        if kind=='vertex':candidate['roi_polygon']=self.draft_points
        else:
            candidate.pop('roi_polygon',None)
            a,b=self.draft_points[0],self.draft_points[2]
            candidate['roi']=[a[0],a[1],b[0]-a[0],b[1]-a[1]]
        self.commit_region(candidate)

    def track(self):
        if self.busy or not self.job or self.editing:return
        if not self.job.get('roi'):
            messagebox.showinfo('先框选','请在参考帧框选矩形或绘制并闭合多边形。',parent=self.root);return
        try:
            self.job['options']=dict(search_radius=self.radius.get(),threshold=self.threshold.get(),max_width=self.width.get(),quantum=self.quantum.get())
        except tk.TclError:
            messagebox.showerror('参数错误','搜索半径和匹配分数必须是数字。',parent=self.root);return
        self.job.update(move_clips=self.move_clips.get(),scale_appearance=self.scale_appearance.get(),mode=self.mode.get())
        self.invalidate();self.need_regenerate=False
        revision=self.revision;job=copy.deepcopy(self.job)
        self.worker(lambda:run_job(job,lambda *p:self.events.put(('progress',p)),self.cancel.is_set),lambda r:self.tracked(r,revision))

    def tracked(self,result,revision=None):
        if revision is not None and revision!=self.revision:
            self.status.set('追踪期间参数已改变，请重新追踪。');return
        self.result=result
        if self.need_regenerate and result['status']=='complete':
            self.appearance_changed();return
        self.progress['value']=100
        self.update_buttons();self.render_selection()
        if result['status']=='complete':
            next_step='确认后点击“应用并返回 Aegisub”。' if self.bridge_dir else '然后勾选已检查并保存。'
            self.status.set(f"追踪完成，生成 {len(result['generated'])} 行。请查看参考帧之外的帧，检查轮廓是否贴住目标，{next_step}")
        else:
            error=result['failures'][0];self.frame.set(error['frame'])
            self.status.set(f"第 {error['frame']} 帧停止：{error['reason']}。失败结果不会导入字幕。")
            messagebox.showwarning('追踪中断',self.status.get(),parent=self.root);self.show_frame()

    def save(self):
        if self.busy or self.editing or self.need_regenerate or not self.result or self.result['status']!='complete':return
        if not self.bridge_dir and not self.reviewed.get():
            messagebox.showinfo('检查轨迹','请拖动帧条查看跟随轮廓，确认后勾选“已检查跟随框”。',parent=self.root);return
        if self.bridge_dir:
            if self.result.get('job_id')!=self.bridge_job_id:
                messagebox.showerror('无法应用','结果不属于本次 Aegisub 追踪。',parent=self.root);return
            try:save_json(self.bridge_dir/'result.json',self.result)
            except Exception as exc:
                messagebox.showerror('无法返回结果',str(exc),parent=self.root);return
            self.close();return
        name=self.path.name.replace('.job.json','')+'.result.json'
        path=filedialog.asksaveasfilename(initialdir=self.path.parent,initialfile=name,defaultextension='.result.json',filetypes=[('追踪结果','*.result.json')])
        if path:
            save_json(path,self.result)
            save_json(Path(path).with_name(Path(path).name.replace('.result.json','')+'.configured.job.json'),self.result['job'])
            self.status.set(f'已保存：{path}。返回 Aegisub，运行“ASS 追踪 / 2. 导入结果”。')


def main(initial=None,bridge_dir=None):
    root=tk.Tk()
    App(root,initial,bridge_dir)
    if bridge_dir:root.lift()
    root.mainloop()
