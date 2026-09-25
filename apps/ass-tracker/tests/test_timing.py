"""Regression for Aegisub START semantics, not generic floor(time * FPS).

Automation calls FrameAtTime(ms, START) = EXACT(ms-1)+1. Its inverse
TimeAtFrame(f, START) is the midpoint of the previous/current integer PTS.
Source: libaegisub/common/vfr.cpp and src/auto4_lua.cpp (TypesettingTools).
"""
import bisect
import copy
import json
from pathlib import Path
import tempfile
import unittest
import test_macro
from ass_tracker.subtitles import generate

TEXTS=[
 r'{\an1\fnArial\c&H19261B&\fax0.000504\fscx92.89\fscy95.50\frz-13.8255\frx-3.3501\fry-2.2549\org(1702.22,361.33)\pos(1669.2,385.21)}Timing A',
 r'{\an1\fnArial\c&H19261B&\fax0.000367\fscx90.80\fscy94.55\frz-13.0999\frx-2.6692\fry-2.8897\org(1609.32,493.25)\pos(1528.65,516.89)}Timing B',
 r'{\an1\fnArial\c&H19261B&\fax0.000359\fscx90.80\fscy94.55\frz-13.1000\frx-2.6705\fry-2.8897\org(1580.51,626.05)\pos(1499.85,649.69)}Timing C']


@unittest.skipIf(test_macro.LuaRuntime is None,'requires lupa')
class TimingTests(unittest.TestCase):
    def export(self, directory, pts, start, end, texts=TEXTS):
        lua=test_macro.MacroTests().setup_lua(directory)
        lua.globals().frame_at=lambda ms:bisect.bisect_left(pts,ms)
        lua.globals().time_at=lambda f:0 if f==0 else (pts[f-1]+pts[f]+1)//2
        lua.globals().start_ms=start;lua.globals().end_ms=end
        lua.globals().reference=bisect.bisect_left(pts,start)
        lua.globals().texts=lua.table_from(texts)
        lua.execute('''
          aegisub.frame_from_ms=frame_at; aegisub.ms_from_frame=time_at
          aegisub.dialog.display=function(d,b)
            if b[1]=='导出' then return '导出',{reference=reference} end
            return b[1],{}
          end
          selected={}
          for i,text in ipairs(texts) do
            subs[i+2]={class='dialogue',text=text,style='Screen',start_time=start_ms,end_time=end_ms,
                       layer=i==1 and 0 or 1,actor='',effect='',margin_l=0,margin_r=0,margin_t=0,comment=false}
            selected[i]=i+2
          end
          macros['ASS 追踪/1. 导出选中行'](subs,selected)
        ''')
        job=json.loads((Path(directory)/'selection.job.json').read_text(encoding='utf-8'))
        return lua,job

    def assert_visible_mapping(self, job, pts):
        track=[dict(ok=True,dx=i,dy=0) for i in range(job['end_frame']-job['start_frame'])]
        events=generate(job,track)
        for line in job['lines']:
            rendered=[e for e in events if e['source_index']==line['index']]
            self.assertEqual(len(rendered),line['end_frame']-line['start_frame'])
            for frame in range(line['start_frame']-1,line['end_frame']+1):
                if frame<0:continue
                visible=[i for i,e in enumerate(rendered) if e['start_time']<=pts[frame]<e['end_time']]
                expected=[frame-line['start_frame']] if line['start_frame']<=frame<line['end_frame'] else []
                self.assertEqual(visible,expected,f'frame {frame}, pts {pts[frame]}')

    def test_three_reported_lines_at_23976(self):
        pts=[(f*1001+12)//24 for f in range(2560)]
        with tempfile.TemporaryDirectory() as td:
            lua,job=self.export(td,pts,101370,106340)
            self.assertEqual((job['start_frame'],job['end_frame']),(2431,2550))
            self.assertEqual(len(job['boundaries_ms']),120)
            self.assert_visible_mapping(job,pts)
            # Old extra-frame results must be rejected before mutating subtitles.
            old=copy.deepcopy(job);old['end_frame']+=1
            for line in old['lines']:line['end_frame']+=1
            result=dict(schema='ass-tracker-result-v1',job_id=old['job_id'],status='complete',job=old,generated=[])
            (Path(td)/'output.result.json').write_text(json.dumps(result),encoding='utf-8')
            with self.assertRaises(Exception):lua.execute("macros['ASS 追踪/2. 导入结果'](subs)")
            self.assertFalse(lua.eval('subs[3].comment'))

    def test_exact_pts_and_single_frame(self):
        pts=list(range(0,600,40))
        for start,end in [(160,320),(170,230),(0,40),(160,200)]:
            with self.subTest(start=start,end=end),tempfile.TemporaryDirectory() as td:
                _,job=self.export(td,pts,start,end,[r'{\pos(50,50)}Frame'])
                self.assertEqual(job['end_frame'],bisect.bisect_left(pts,end))
                self.assert_visible_mapping(job,pts)

    def test_vfr_short_interval_centisecond_rounding(self):
        pts=[0,17,63,105,148,210,251,300]
        with tempfile.TemporaryDirectory() as td:
            _,job=self.export(td,pts,0,250,[r'{\pos(50,50)}VFR'])
            self.assertEqual(job['boundaries_ms'][1],10)
            self.assert_visible_mapping(job,pts)


if __name__=='__main__':unittest.main()
