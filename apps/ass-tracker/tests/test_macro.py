"""Exercise real Lua macro code against an in-memory Aegisub API fixture."""
from pathlib import Path
import json
import tempfile
import unittest
try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime=None

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None,'Install lupa for the Lua bridge integration tests')
class MacroTests(unittest.TestCase):
    def test_module_paths_with_dependency_control_chunk_names(self):
        from ass_tracker import __version__
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'ASSTracker.lua'
            folder=Path(td)/'ASSTracker';folder.mkdir()
            (folder/'VERSION').write_text(__version__)
            code=(ROOT.parents[1]/'modules/wenhe/ASSTracker.lua').read_text(encoding='utf-8')
            for name in (str(source),'@'+str(source)):
                lua=LuaRuntime(unpack_returned_tuples=True)
                lua.globals().code=code;lua.globals().source=name
                lua.execute("package.preload['wenhe.ASSTracker.json']=function()return {}end")
                module=lua.execute('return assert(loadstring(code,source))()')
                self.assertEqual(module.version_string,__version__)

    def setup_lua(self, directory):
        (Path(directory)/'ass_tracker_json.lua').write_bytes((ROOT.parents[1]/'modules/wenhe/ASSTracker/json.lua').read_bytes())
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.globals().module_dir=str(Path(directory)).replace('\\','/')
        lua.globals().job_path=str(Path(directory)/'selection.job.json')
        lua.globals().result_path=str(Path(directory)/'output.result.json')
        from ass_tracker import __version__
        lua.globals().fixture_version=__version__
        lua.execute(r'''
        package.path=module_dir..'/?.lua;'..package.path
        package.preload['wenhe.ASSTracker']=function()return {version_string=fixture_version,directory=module_dir,json=require('ass_tracker_json')}end
        macros={};messages={};undo_count=0
        aegisub={
          register_macro=function(n,d,fn) macros[n]=fn end,
          video_size=function() return 640,360 end,
          project_properties=function() return {video_file='C:/素材/video.mp4',video_position=5} end,
          frame_from_ms=function(ms) return math.ceil(ms/40) end,
          ms_from_frame=function(f) return f*40-20 end,
          cancel=function() error('CANCELLED') end,
          set_undo_point=function()undo_count=undo_count+1 end,
          decode_path=function(p) return (p:gsub('^%?user',module_dir)) end,
          dialog={
            open=function()return result_path end,
            save=function()return job_path end,
            display=function(d,b) table.insert(messages,d[1].label); if b[1]=='导出' then return '导出',{reference=5} else return b[1],{} end end
          }
        }
        subs={
          {class='info',key='PlayResX',value='640'},
          {class='info',key='PlayResY',value='360'},
          {class='dialogue',text=[[{\pos(100,120)}测试]],style='Screen',start_time=160,end_time=320,layer=2,actor='',effect='',margin_l=0,margin_r=0,margin_t=0,comment=false},
          {class='dialogue',text='untouched',style='Other',start_time=0,end_time=100,comment=false}
        }
        subs.insert=function(index,value)table.insert(subs,index,value)end
        ''')
        lua.execute((ROOT.parents[1]/'macros/wenhe.ASSTracker.lua').read_text(encoding='utf-8'))
        return lua

    def test_export_import_and_stale_input(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup_lua(td)
            lua.execute("macros['ASS 追踪/1. 导出选中行'](subs,{3})")
            job=json.loads((Path(td)/'selection.job.json').read_text(encoding='utf-8'))
            self.assertEqual(job['start_frame'],4)
            self.assertEqual(job['boundaries_ms'],[140,180,220,260,300])
            result=dict(schema='ass-tracker-result-v1',job_id=job['job_id'],status='complete',job=job,
                        generated=[dict(source_index=3,start_time=160,end_time=240,text=r'{\pos(100,120)}测试'),
                                   dict(source_index=3,start_time=240,end_time=320,text=r'{\pos(110,120)}测试')])
            (Path(td)/'output.result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
            lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertEqual(lua.eval('#subs'),6)
            self.assertTrue(lua.eval('subs[3].comment'))
            self.assertEqual(lua.eval('subs[5].text'),r'{\pos(110,120)}测试')
            self.assertEqual(lua.eval('subs[6].text'),'untouched')
            self.assertEqual(lua.eval('undo_count'),1)
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertEqual(lua.eval('#subs'),6)

    def test_bad_coverage_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup_lua(td)
            lua.execute("macros['ASS 追踪/1. 导出选中行'](subs,{3})")
            job=json.loads((Path(td)/'selection.job.json').read_text(encoding='utf-8'))
            result=dict(schema='ass-tracker-result-v1',job_id=job['job_id'],status='complete',job=job,
                        generated=[dict(source_index=3,start_time=200,end_time=320,text=r'{\pos(1,2)}x')])
            (Path(td)/'output.result.json').write_text(json.dumps(result),encoding='utf-8')
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertEqual(lua.eval('#subs'),4)
            self.assertFalse(lua.eval('subs[3].comment'))

    def test_perspective_exports_metrics_and_guards_style_changes(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup_lua(td)
            lua.execute(r'''
            table.insert(subs,{class='style',name='Screen',fontname='Arial',fontsize=28,scale_x=100,scale_y=100,angle=0,align=7,bold=false,italic=false,spacing=0})
            aegisub.text_extents=function(style,text)
              assert(style.scale_x==100 and style.scale_y==100)
              return 60,28,5
            end
            aegisub.dialog.display=function(d,b)
              if b[1]=='导出' then return '导出',{reference=5,mode='平面透视'} end
              return b[1],{}
            end
            macros['ASS 追踪/1. 导出选中行'](subs,{3})
            ''')
            job=json.loads((Path(td)/'selection.job.json').read_text(encoding='utf-8'))
            self.assertEqual(job['mode'],'similarity')
            self.assertEqual(job['lines'][0]['metrics']['height'],28)
            result=dict(schema='ass-tracker-result-v1',job_id=job['job_id'],status='complete',job=job,
                        generated=[dict(source_index=3,start_time=160,end_time=320,text=r'{\pos(100,120)}测试')])
            (Path(td)/'output.result.json').write_text(json.dumps(result),encoding='utf-8')
            lua.execute('subs[5].fontsize=40')
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertEqual(lua.eval('#subs'),5)
            self.assertFalse(lua.eval('subs[3].comment'))

    def test_reset_styles_fades_and_measured_font(self):
        with tempfile.TemporaryDirectory() as td:
            lua=self.setup_lua(td)
            lua.execute(r'''
            table.insert(subs,{class='style',name='Screen',fontname='Arial',fontsize=28,scale_x=100,scale_y=100,angle=0,align=7,bold=false,italic=false,spacing=0})
            table.insert(subs,{class='style',name='Alt',fontname='Georgia',fontsize=40,scale_x=100,scale_y=100,angle=0,align=7,bold=true,italic=false,spacing=0})
            subs[3].text=[[{\pos(100,120)\fad(100,100)\fs60}{\rAlt\i1}测{\r\rMissing}试]]
            measured={}
            aegisub.text_extents=function(style,text) table.insert(measured,style); return 60,28,5 end
            macros['ASS 追踪/1. 导出选中行'](subs,{3})
            ''')
            # The first run uses Alt after \r, then \i1; \fs60 before \r no longer applies.
            self.assertEqual([lua.eval('measured[1].'+k) for k in ('fontname','fontsize','bold','italic')],['Georgia',40,True,True])
            job=json.loads((Path(td)/'selection.job.json').read_text(encoding='utf-8'))
            self.assertEqual(list(job['lines'][0]['reset_styles']),['Alt'])
            self.assertEqual(job['lines'][0]['reset_styles']['Alt']['fontname'],'Georgia')
            tail=r'\fs60}{\rAlt\i1}测{\r\rMissing}试'
            result=dict(schema='ass-tracker-result-v1',job_id=job['job_id'],status='complete',job=job,
                        generated=[dict(source_index=3,start_time=160,end_time=240,text=r'{\pos(100,120)\fade(255,0,255,0,100,60,160)'+tail),
                                   dict(source_index=3,start_time=240,end_time=320,text=r'{\pos(110,120)\fade(255,0,255,-80,20,-20,80)'+tail)])
            (Path(td)/'output.result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
            # A style named by \r changing after export invalidates the result.
            lua.execute('subs[6].fontsize=41')
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertEqual(lua.eval('#subs'),6)
            lua.execute('subs[6].fontsize=40')
            lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertEqual(lua.eval('#subs'),8)
            self.assertTrue(lua.eval('subs[3].comment'))
            self.assertIn(r'\fade(255,0,255,-80,20,-20,80)',lua.eval('subs[5].text'))


if __name__=='__main__':unittest.main()
