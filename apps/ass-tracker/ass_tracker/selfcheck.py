"""Portable runtime smoke test, including real Tk and FFmpeg execution."""
import traceback
from . import __version__


def check():
    try:
        import tkinter as tk
        import tempfile
        from pathlib import Path
        import subprocess
        import numpy as np
        from .core import executable, decoded_frames, CREATE_FLAGS
        from .perspective import transform
        from .motion import estimate
        root=tk.Tk();root.withdraw();root.update();root.destroy()
        with tempfile.TemporaryDirectory(prefix='ASS tracker 中文 ') as td:
            video=Path(td)/'sample.mkv'
            frames=np.arange(3*64*48,dtype=np.uint8).reshape(3,48,64)
            subprocess.run([executable('ffmpeg'),'-v','error','-y','-f','rawvideo','-pix_fmt','gray',
                            '-s','64x48','-r','25','-i','-','-c:v','ffv1',str(video)],
                           input=frames.tobytes(),check=True,creationflags=CREATE_FLAGS,capture_output=True)
            job=dict(video=str(video),video_width=64,video_height=48,start_frame=0,end_frame=3)
            with decoded_frames(job,64) as decoded: np.testing.assert_array_equal(decoded,frames)
            p=np.array([[0.,0.],[50.,0.],[50.,50.],[0.,50.]])
            H,_=estimate(p,p*1.2+3,'similarity')
            transform(dict(text=r'{\pos(10,10)\an7}Test',metrics=dict(width=80,height=28)),H)
        return dict(status='ok',version=__version__,checks=['tk','ffmpeg','ffprobe','decode','opencv','scipy','ASS'])
    except Exception:
        return dict(status='error',version=__version__,error=traceback.format_exc())
