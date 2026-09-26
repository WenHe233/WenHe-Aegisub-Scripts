import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
import cv2
import numpy as np
from ass_tracker.planar import track_planar, warp, corners
from ass_tracker.perspective import transform, validate_line, state

ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'tests/test_tracker.py').read_text(encoding='utf-8'))
HEADER=next(n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='header' for t in n.targets))


def render(value,folder,name):
    (Path(folder)/(name+'.ass')).write_text(HEADER+'Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,'+value+'\n',encoding='utf-8-sig')
    p=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=320x240','-vf',f'ass={name}.ass','-frames:v','1','-pix_fmt','gray','-f','rawvideo','-'],cwd=folder,capture_output=True,check=True)
    return np.frombuffer(p.stdout,np.uint8).reshape(240,320)


class PlanarTests(unittest.TestCase):
    def test_synthetic_plane_and_occlusion(self):
        rng=np.random.default_rng(35)
        ref=cv2.GaussianBlur(rng.integers(0,256,(240,320),dtype=np.uint8),(3,3),0)
        H=np.array([[.98,.035,10],[-.02,1.02,7],[.00015,-.00012,1]])
        moved=cv2.warpPerspective(ref,H,(320,240))
        frames=np.stack([ref,ref,moved,np.full_like(ref,100),moved])
        job=dict(video_width=320,video_height=240,start_frame=0,reference_frame=1,roi=[35,35,230,165],options=dict(search_radius=80))
        rows=track_planar(frames,job,lambda *a:None,lambda:False)
        self.assertTrue(all(r['ok'] for r in rows[:3]))
        np.testing.assert_allclose(warp(corners(job['roi']),rows[2]['H']),warp(corners(job['roi']),H),atol=.8)
        self.assertFalse(rows[3]['ok']);self.assertEqual(rows[4]['reason'],'未追踪')

    def test_rendered_text_and_screen_polygon(self):
        H=np.array([[1.015,.045,4],[-.02,.985,8],[.0002,-.00015,1]])
        cases=[r'{\an7\pos(70,80)\org(90,90)\frz15\frx8\fry-5}Track',
               r'{\an1\pos(70,110)\org(90,90)\fax0.04\frz-12\frx-3\fry-2}Track',
               r'{\an7\pos(0,0)\p1\1a&H20&}m 50 50 l 200 50 200 90 50 90 c{\p0}',
               r'{\an7\pos(60,70)\org(90,90)\fscx115\fscy90\fax.06\fay.1\frz8}Track',
               r'{\a6\pos(160,60)\fr10\frx6}Track',
               r'{\an7\pos(70,80)\frz40\r\frz15\frx8}Track',
               r'{\an7\pos(50,90)\frz12\c&H00FFFF&}Tr{\r\frz12}ack']
        with tempfile.TemporaryDirectory() as td:
            for i,body in enumerate(cases):
                with self.subTest(case=i):
                    line=dict(text=body,metrics=dict(width=100,height=28),style_data=dict(align=7))
                    actual=render(transform(line,H),td,f'new{i}')
                    expected=cv2.warpPerspective(render(body,td,f'old{i}'),H,(320,240))
                    a=actual>90;b=expected>90
                    iou=np.sum(a&b)/np.sum(a|b)
                    self.assertGreater(iou,.86,f'case {i}: rendered overlap {iou:.3f}')

    def test_fay_text_under_strong_shear(self):
        # A large change of fax exposes the fay term (overlap ~0.3 when ignored).
        H=np.array([[1,.35,-20],[0,1,4],[0,0,1.]])
        body=r'{\an7\pos(40,60)\org(90,90)\fs60\fay.25}Track'
        with tempfile.TemporaryDirectory() as td:
            line=dict(text=body,metrics=dict(width=150,height=60),style_data=dict(align=7))
            a=render(transform(line,H),td,'new')>90
            b=cv2.warpPerspective(render(body,td,'old'),H,(320,240))>90
            self.assertGreater(np.sum(a&b)/np.sum(a|b),.9)

    def test_tag_state_follows_renderer(self):
        style=dict(align=2,angle=5,scale_x=90,scale_y=110)
        def line(text,**extra):
            return dict(text=text,metrics=dict(width=100,height=28),style_data=style,**extra)
        v,_,_,fay=state(line(r'{\pos(1,2)\fscx120\fsc\frz10\fr20\fax.1\fax\fay.2}x'))
        np.testing.assert_allclose(v[2:],[90,110,20,0,0,0]);self.assertEqual(fay,.2)
        self.assertEqual(state(line(r'{\pos(1,2)\frz30\frz}x'))[0][4],5)
        for text,expected in [(r'{\an7\an3\pos(1,2)}x',7),(r'{\a10\pos(1,2)}x',5),(r'{\a4\pos(1,2)}x',7),
                              (r'{\a6\an1\pos(1,2)}x',8),(r'{\pos(1,2)}x',2)]:
            self.assertEqual(state(line(text))[2],expected,text)
        # \r restores the (named) style; restoring the same values keeps the line uniform.
        alt=dict(style,angle=30)
        validate_line(line(r'{\pos(1,2)\frz30}A{\r\frz30}B'))
        validate_line(line(r'{\pos(1,2)\frz5}A{\rMissing}B'))
        validate_line(line(r'{\pos(1,2)\frz30}A{\rAlt}B',reset_styles=dict(Alt=alt)))
        for text in [r'{\pos(1,2)\frz30}A{\r}B',r'{\pos(1,2)}A{\rAlt}B',r'{\pos(1,2)\fay.1}A{\c&HFF&}B',
                     r'{\pos(1,2)}A{\an7}B',r'{\pos(1,2)\fs+2}x']:
            with self.assertRaises(ValueError,msg=text):validate_line(line(text,reset_styles=dict(Alt=alt)))
        # Fitted geometry is restored after a later \r.
        value=transform(line(r'{\an7\pos(20,30)\frz12}A{\r\frz12\c&HFF&}B'),[[1,.05,3],[0,1,5],[0,0,1]])
        self.assertRegex(value,r'^\{\\an7\\q2\\pos\([^)]*\)\\org\([^)]*\)\\fscx[^}]*\}A\{\\r\\fscx[^}]*\\c&HFF&\}B$')
        self.assertEqual(value.count(r'\frz'),2)

    def test_clip_and_rejection(self):
        line=dict(text=r'{\an7\pos(20,30)\clip(0,0,100,100)}Track',metrics=dict(width=100,height=28))
        H=np.array([[1,.05,3],[0,1,5],[0,0,1]])
        self.assertIn(r'\clip(m 3 5 l 103 5 l 108 105 l 8 105 c)',transform(line,H))
        self.assertIn(r'\clip(0,0,100,100)',transform(line,H,False))
        with self.assertRaises(ValueError):validate_line(dict(text=r'{\pos(1,2)}No metrics'))
        with self.assertRaises(ValueError):validate_line(dict(text=r'{\an7\pos(0,0)\p1}m 0 0 b 1 2 3 4'))


if __name__=='__main__':unittest.main()
