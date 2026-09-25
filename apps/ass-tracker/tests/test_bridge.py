import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import numpy as np
import test_macro

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipIf(test_macro.LuaRuntime is None,'requires lupa')
class LinkedMacroTests(unittest.TestCase):
    def setup(self,td,mutate=None,cancel=False):
        lua=test_macro.MacroTests().setup_lua(td)
        lua.globals().session_dir=str(td).replace('\\','/')
        def finish():
            job=json.loads((Path(td)/'job.json').read_text(encoding='utf-8'))
            row=dict(source_index=3,start_time=160,end_time=320,text=r'{\pos(105,120)}测试')
            result=dict(schema='ass-tracker-result-v1',status='complete',job_id=job['job_id'],job=job,generated=[row])
            if mutate:mutate(result)
            (Path(td)/'result.json').write_text(json.dumps(result),encoding='utf-8')
        lua.globals().finish=finish
        lua.globals().cancel_test=cancel
        lua.execute('''
          closed=0; cleaned=0; polls=0; dialogs=0
          aegisub.progress={title=function()end,task=function()end,is_cancelled=function()return cancel_test end}
          aegisub.dialog.open=function()error('unexpected file picker')end
          aegisub.dialog.save=function()error('unexpected file picker')end
          aegisub.dialog.display=function(d,b)
            dialogs=dialogs+1
            if b[1]=='开始追踪' then return b[1],{reference=5,mode='平移'} end
            return b[1],{}
          end
          package.preload.ass_tracker_config=function()return {root='configured root'}end
          package.preload['wenhe.ASSTracker.bridge']=function()return {
            session=function()return session_dir end,
            start=function(root,dir) finish(); return {} end,
            poll=function()polls=polls+1;return 0 end,
            close=function()closed=closed+1 end,
            cleanup=function()cleaned=cleaned+1 end
          }end
        ''')
        return lua

    def test_apply_returns_without_file_dialog_or_second_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td)
            lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(lua.eval('#subs'),5)
            self.assertTrue(lua.eval('subs[3].comment'))
            self.assertEqual(lua.eval('subs[4].text'),r'{\pos(105,120)}测试')
            self.assertEqual(lua.eval('dialogs'),0)
            self.assertEqual(lua.eval('undo_count'),1)
            self.assertEqual(lua.eval('closed'),1)
            self.assertEqual(lua.eval('cleaned'),1)

    def test_cancel_wins_over_completed_result(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td,cancel=True)
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(lua.eval('#subs'),4)
            self.assertFalse(lua.eval('subs[3].comment'))
            self.assertEqual(lua.eval('closed'),1)

    def test_foreign_result_is_rejected(self):
        def change(r):r['job']['job_id']='foreign';r['job_id']='foreign'
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td,change)
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertFalse(lua.eval('subs[3].comment'))
            self.assertEqual(lua.eval('undo_count'),0)


@unittest.skipIf(os.name!='nt' or test_macro.LuaRuntime is None,'requires Windows LuaJIT')
class WindowsBridgeTests(unittest.TestCase):
    def native(self,lua):
        return lua.execute((ROOT.parents[1]/'modules/wenhe/ASSTracker/bridge.lua').read_text(encoding='utf-8'))

    def test_unicode_launch_and_literal_arguments(self):
        lua=test_macro.LuaRuntime(unpack_returned_tuples=True);bridge=self.native(lua)
        with tempfile.TemporaryDirectory(prefix='追踪 联动 & ') as td:
            output=Path(td)/'args.json'
            values=['中文 空格','& | ^ %PATH% ! $()','quote"test','trailing\\']
            args=['-c','import sys,json,pathlib;pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]),encoding="utf-8")',str(output),*values]
            process=bridge.spawn(sys.executable,lua.table_from(args),str(ROOT))
            try:
                deadline=time.monotonic()+15
                code=None
                while code is None and time.monotonic()<deadline:code=bridge.poll(process,100)
                self.assertEqual(code,0)
                self.assertEqual(json.loads(output.read_text(encoding='utf-8')),values)
            finally:bridge.close(process)

    def test_lua_to_real_python_gui_tracking_and_back(self):
        with tempfile.TemporaryDirectory() as td:
            lua=LinkedMacroTests().setup(td)
            native=self.native(lua)
            rng=np.random.default_rng(24);patch=rng.integers(20,235,(60,80),dtype=np.uint8)
            frames=np.full((10,360,640),110,np.uint8)
            for i,frame in enumerate(frames):frame[60:120,50+i*2:130+i*2]=patch
            video=Path(td)/'sample.mkv'
            subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','gray','-s','640x360','-r','25','-i','-','-c:v','ffv1',str(video)],input=frames.tobytes(),check=True)
            lua.globals().real_video=str(video)
            lua.execute('''
              aegisub.project_properties=function()return {video_file=real_video,video_position=5}end
              table.insert(subs,{class='style',name='Screen',fontname='Arial',fontsize=28,scale_x=100,scale_y=100,angle=0,align=7,bold=false,italic=false,spacing=0})
              aegisub.text_extents=function()return 100,28,5 end
            ''')
            helper=Path(td)/'gui_test.py'
            helper.write_text('''import sys,time,tkinter as tk
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from ass_tracker.gui import App,messagebox
root=tk.Tk();root.withdraw()
def fail(*args):raise RuntimeError(str(args))
messagebox.showerror=fail;messagebox.showwarning=fail
session=Path(sys.argv[1]);app=App(root,str(session/'job.json'),bridge_dir=session)
deadline=time.monotonic()+30
while app.photo is None and time.monotonic()<deadline:
 root.update();time.sleep(.01)
assert app.photo is not None
app.job['roi']=[60,60,80,60];app.mode.set('translation');app.track()
while app.busy and time.monotonic()<deadline:
 root.update();time.sleep(.01)
assert app.result and app.result['status']=='complete'
app.save()
''',encoding='utf-8')
            lua.globals().native=native;lua.globals().executable=sys.executable
            lua.globals().helper=str(helper);lua.globals().tool_root=str(ROOT)
            lua.execute('''
              package.preload['wenhe.ASSTracker.bridge']=function()return {
                session=function()return session_dir end,
                start=function(root,dir)return native.spawn(executable,{helper,dir,tool_root},tool_root)end,
                poll=native.poll,close=native.close,cleanup=function()end
              }end
              macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})
            ''')
            self.assertTrue(lua.eval('subs[3].comment'))
            self.assertEqual(lua.eval('undo_count'),1)
            self.assertEqual(lua.eval('dialogs'),0)
            self.assertEqual(lua.eval('#subs'),9)
            self.assertIn(r'\pos(98,120)',lua.eval('subs[4].text'))
            self.assertIn(r'\pos(104,120)',lua.eval('subs[7].text'))


if __name__=='__main__':unittest.main()
