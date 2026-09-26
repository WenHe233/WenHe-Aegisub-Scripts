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
    def setup(self,td,mutate=None,cancel=False,installed=True,exit_code=0):
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
        lua.globals().runtime_installed=installed
        lua.globals().exit_code=exit_code
        lua.globals().mkdir=lambda path:os.makedirs(path,exist_ok=True)
        lua.execute('''
          closed=0; cleaned=0; polls=0; dialogs=0; sessions=0; started=nil
          checked={}; shown={}; answers={}; tasks={}; progress_values={}
          aegisub.progress={title=function()end,task=function(t)table.insert(tasks,t)end,
                            set=function(v)table.insert(progress_values,v)end,is_cancelled=function()return cancel_test end}
          aegisub.dialog.open=function()error('unexpected file picker')end
          aegisub.dialog.save=function()error('unexpected file picker')end
          aegisub.dialog.display=function(d,b)
            dialogs=dialogs+1
            -- Record the named controls and label text; answer from the queue.
            local view={labels=''}
            for _,c in ipairs(d) do
              if c.name then view[c.name]=c.text or c.value end
              if c.class=='label' then view.labels=view.labels..c.label..'\\n' end
            end
            table.insert(shown,view)
            if #answers>0 then local a=table.remove(answers,1);return a[1],a[2] end
            if b[1]=='开始追踪' then return b[1],{reference=5,mode='平移'} end
            return b[1],{}
          end
          package.preload.ass_tracker_config=function()return {root='configured root'}end
          package.preload['wenhe.ASSTracker.bridge']=function()return {
            local_app_data=function()return 'C:\\\\Users\\\\测试\\\\AppData\\\\Local' end,
            runtime_present=function(root,version)table.insert(checked,root..'|'..version);return runtime_installed end,
            make_directory=function(path)mkdir(path)end,
            session=function()sessions=sessions+1;return session_dir end,
            start=function(root,dir,version,setup) started=setup; finish(); return {} end,
            poll=function()polls=polls+1;return exit_code end,
            close=function()closed=closed+1 end,
            cleanup=function()cleaned=cleaned+1 end
          }end
        ''')
        return lua

    def answer(self,lua,*answers):
        """Queue dialog replies as (button, controls) pairs; False means Esc."""
        lua.globals().answers=lua.table_from([lua.table_from([button,lua.table_from(values)]) for button,values in answers])

    def settings(self,td):
        return json.loads((Path(td)/'config/wenhe.ASSTracker.runtime.json').read_text(encoding='utf-8'))

    def test_missing_runtime_downloads_from_selected_mirror_and_remembers_choice(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td,installed=False)
            self.answer(lua,('开始下载',dict(parent='D:\\追踪 运行包',source='ghfast.top 代理',custom='')))
            lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(lua.eval('checked[1]'),'C:\\Users\\测试\\AppData\\Local\\WenHe\\AegisubScripts\\ASSTracker\\versions|0.6.0')
            self.assertEqual(lua.eval('shown[1].parent'),'C:\\Users\\测试\\AppData\\Local\\WenHe\\AegisubScripts')
            self.assertEqual(lua.eval('shown[1].source'),'GitHub 官方')
            self.assertEqual(lua.eval('started.cache_root'),'D:\\追踪 运行包\\ASSTracker\\versions')
            self.assertEqual(lua.eval('started.mirror'),'https://ghfast.top/')
            self.assertTrue(lua.eval('started.download'))
            self.assertTrue(lua.eval('subs[3].comment'))
            self.assertEqual(self.settings(td),dict(parent='D:\\追踪 运行包',source='ghfast.top 代理',custom_prefix=''))
            # A later version needs another download: the saved choice is prefilled.
            lua=self.setup(td,installed=False)
            self.answer(lua,('取消',{}))
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(lua.eval('checked[1]'),'D:\\追踪 运行包\\ASSTracker\\versions|0.6.0')
            self.assertEqual(lua.eval('shown[1].parent'),'D:\\追踪 运行包')
            self.assertEqual(lua.eval('shown[1].source'),'ghfast.top 代理')

    def test_installed_runtime_in_saved_location_skips_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(Path(td)/'config')
            (Path(td)/'config/wenhe.ASSTracker.runtime.json').write_text(json.dumps(dict(parent='E:\\Tools')),encoding='utf-8')
            lua=self.setup(td)
            lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(lua.eval('dialogs'),0)
            self.assertEqual(lua.eval('started.cache_root'),'E:\\Tools\\ASSTracker\\versions')
            self.assertFalse(lua.eval('started.download'))
            self.assertIsNone(lua.eval('started.mirror'))

    def test_cancel_download_prompt_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td,installed=False)
            self.answer(lua,(False,dict(parent='D:\\x',source='GitHub 官方',custom='')))
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(lua.eval('sessions'),0)
            self.assertIsNone(lua.eval('started'))
            self.assertFalse(lua.eval('subs[3].comment'))
            self.assertEqual(lua.eval('undo_count'),0)
            self.assertFalse((Path(td)/'config').exists())

    def test_invalid_input_returns_to_prompt_and_folder_picker(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td,installed=False)
            lua.execute(r"aegisub.dialog.save=function(title,dir,name)picked_from=dir;return 'F:\\Picked\\'..name end")
            custom='自定义代理前缀'
            self.answer(lua,
                ('开始下载',dict(parent='relative\\dir',source=custom,custom='https://ok.example/')),
                ('开始下载',dict(parent='E:\\Tools',source=custom,custom='http://insecure.example/')),
                ('开始下载',dict(parent='E:\\Tools',source=custom,custom='https://ok.example/p?x=1')),
                ('选择文件夹…',dict(parent='E:\\Tools',source=custom,custom='https://ok.example/')),
                ('开始下载',dict(parent='F:\\Picked',source=custom,custom=' https://mirror.example:8443/gh ')))
            lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertIn('完整路径',lua.eval('shown[2].labels'))
            self.assertIn('自定义前缀必须以 https:// 开头',lua.eval('shown[3].labels'))
            self.assertIn('自定义前缀必须以 https:// 开头',lua.eval('shown[4].labels'))
            self.assertNotIn('自定义前缀必须',lua.eval('shown[5].labels'))
            self.assertEqual(lua.eval('picked_from'),'E:\\Tools')
            self.assertEqual(lua.eval('shown[5].parent'),'F:\\Picked')
            self.assertEqual(lua.eval('shown[5].custom'),'https://ok.example/')
            self.assertEqual(lua.eval('started.mirror'),'https://mirror.example:8443/gh/')
            self.assertEqual(lua.eval('started.cache_root'),'F:\\Picked\\ASSTracker\\versions')

    def test_download_progress_and_download_failure_message(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup(td,installed=False,exit_code=3)
            (Path(td)/'error.log').write_text('\nDownload checksum mismatch.\nAt line 1\n',encoding='utf-8')
            lua.execute('''
              local function stage(text)local f=io.open(session_dir..'/progress','wb');f:write(text);f:close()end
              local fake=package.preload['wenhe.ASSTracker.bridge']
              package.preload['wenhe.ASSTracker.bridge']=function()
                local bridge=fake()
                bridge.poll=function()
                  polls=polls+1
                  if polls==1 then stage('download 524288 1048576 mirror') elseif polls==6 then stage('verify') end
                  -- The macro reads progress on every fifth tick: 1, 6, 11.
                  if polls>=11 then return exit_code end
                end
                return bridge
              end
            ''')
            self.answer(lua,('开始下载',dict(parent='D:\\x',source='GitHub 官方',custom='')),('关闭',{}))
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/开始追踪（自动回填）'](subs,{3})")
            self.assertEqual(list(lua.eval('progress_values').values()),[50,100])
            tasks='\n'.join(lua.eval('tasks').values())
            self.assertIn('已下载 0.5 / 1.0 MB，下载源：GitHub 官方',tasks)
            self.assertIn('校验清单来自代理',tasks)
            self.assertIn('校验并解压',tasks)
            message=lua.eval('shown[2].labels')
            self.assertIn('Download checksum mismatch.',message)
            self.assertIn('换一个下载源',message)
            self.assertFalse(lua.eval('subs[3].comment'))

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

    def test_runtime_detection_and_directories_with_unicode_paths(self):
        lua=test_macro.LuaRuntime(unpack_returned_tuples=True);bridge=self.native(lua)
        self.assertEqual(bridge.local_app_data(),os.environ['LOCALAPPDATA'])
        with tempfile.TemporaryDirectory(prefix='运行包 检测 ') as td:
            root=Path(td)/'versions';runtime=root/'0.6.0'
            self.assertFalse(bridge.runtime_present(str(root),'0.6.0'))
            runtime.mkdir(parents=True);(runtime/'runtime.json').write_text('{}')
            self.assertFalse(bridge.runtime_present(str(root),'0.6.0'))
            (runtime/'ASSTracker.exe').write_bytes(b'')
            self.assertTrue(bridge.runtime_present(str(root),'0.6.0'))
            self.assertFalse(bridge.is_file(str(runtime)))
            config=Path(td)/'配置'
            bridge.make_directory(str(config));bridge.make_directory(str(config))
            self.assertTrue(config.is_dir())
            with self.assertRaises(Exception):bridge.make_directory(str(runtime/'runtime.json'))

    def test_start_passes_cache_root_and_mirror_only_when_set(self):
        lua=test_macro.LuaRuntime(unpack_returned_tuples=True);bridge=self.native(lua)
        calls=[]
        bridge.spawn=lambda executable,args,cwd:calls.append(list(args.values()))
        bridge.start('root','session','0.6.0',lua.table_from(dict(cache_root='D:\\运行包',mirror='https://ghfast.top/')))
        bridge.start('root','session','0.6.0',lua.table_from(dict(cache_root='D:\\运行包',mirror='')))
        self.assertEqual(calls[0][-4:],['-CacheRoot','D:\\运行包','-Mirror','https://ghfast.top/'])
        self.assertEqual(calls[1][-2:],['-CacheRoot','D:\\运行包'])

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
                local_app_data=native.local_app_data,runtime_present=function()return true end,
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
