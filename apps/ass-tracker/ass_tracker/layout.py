"""Small responsive Tk helpers; coordinates stay independent of display size."""
import os
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk


def work_area(root):
    fallback=(0,0,root.winfo_screenwidth(),root.winfo_screenheight())
    if os.name!='nt': return fallback
    try:
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]
        user=ctypes.windll.user32
        user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
        user.MonitorFromWindow.restype=wintypes.HANDLE
        user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
        info=MonitorInfo();info.size=ctypes.sizeof(info)
        # Do not change process DPI awareness after Tk initialization. Windows
        # returns coordinates in this process's existing DPI coordinate space.
        if user.GetMonitorInfoW(user.MonitorFromWindow(root.winfo_id(),2),ctypes.byref(info)):
            r=info.work
            return r.left,r.top,r.right,r.bottom
    except (OSError,AttributeError): pass
    return fallback


def size_window(root):
    left,top,right,bottom=work_area(root)
    width,height=right-left,bottom-top
    w,h=min(1180,int(width*.9)),min(850,int(height*.9))
    root.minsize(min(640,w),min(480,h))
    root.geometry(f'{w}x{h}{left+(width-w)//2:+d}{top+(height-h)//2:+d}')


class FlowFrame(ttk.Frame):
    """Wrap controls using their measured widths (including current Tk scaling)."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,**kwargs)
        self.items=[]
        self.bind('<Configure>',self.reflow)

    def add(self,widget):
        self.items.append(widget)
        self.reflow()
        return widget

    def reflow(self,event=None):
        width=max(1,self.winfo_width()-8)
        x=y=row_height=0
        for widget in self.items:
            required=widget.winfo_reqwidth()+8
            if x and x+required>width:
                y+=row_height; x=row_height=0
            widget.place(x=x+4,y=y+2,width=max(1,min(widget.winfo_reqwidth(),width-4)))
            x+=required;row_height=max(row_height,widget.winfo_reqheight()+4)
        height=max(1,y+row_height)
        if int(str(self['height']))!=height:self.configure(height=height)


class SettingsPanel(ttk.Frame):
    """Only the settings scroll; preview/navigation/actions never join this canvas."""
    def __init__(self,parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,height=120,highlightthickness=0)
        self.bar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        self.columnconfigure(0,weight=1)
        self.body=ttk.Frame(self.canvas)
        self.item=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.canvas.bind('<Configure>',self.resize)
        self.body.bind('<Configure>',self.resize)
        # Wheel bindings are local, never intercept text entries elsewhere.
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
        self.limit=220

    def bind_wheel(self):
        def wheel(event):
            if self.body.winfo_reqheight()>self.limit:
                self.canvas.yview_scroll(-int(event.delta/120),'units')
                return 'break'
        def attach(widget):
            widget.bind('<MouseWheel>',wheel,add='+')
            for child in widget.winfo_children():attach(child)
        attach(self.body)

    def resize(self,event=None):
        width=max(1,self.canvas.winfo_width())
        self.canvas.itemconfigure(self.item,width=width)
        requested=self.body.winfo_reqheight()
        self.canvas.configure(height=min(requested,self.limit),scrollregion=(0,0,width,requested))
        if requested>self.limit:self.bar.grid(row=0,column=1,sticky='ns')
        else:
            self.bar.grid_remove();self.canvas.yview_moveto(0)


@dataclass(frozen=True)
class Viewport:
    video_width: int
    video_height: int
    width: int
    height: int
    x: int
    y: int

    @classmethod
    def fit(cls,video_width,video_height,canvas_width,canvas_height):
        scale=min(max(1,canvas_width)/video_width,max(1,canvas_height)/video_height)
        w=max(1,min(canvas_width,round(video_width*scale)))
        h=max(1,min(canvas_height,round(video_height*scale)))
        return cls(video_width,video_height,w,h,(canvas_width-w)//2,(canvas_height-h)//2)

    def to_canvas(self,point):
        return self.x+point[0]*self.width/self.video_width,self.y+point[1]*self.height/self.video_height

    def to_video(self,x,y,clamp=False):
        if not clamp and not (self.x<=x<=self.x+self.width and self.y<=y<=self.y+self.height): return None
        return [max(0,min(self.video_width,(x-self.x)*self.video_width/self.width)),
                max(0,min(self.video_height,(y-self.y)*self.video_height/self.height))]
