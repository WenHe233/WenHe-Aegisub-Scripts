import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
import cv2
import numpy as np
from ass_tracker.planar import track_planar, warp, corners
from ass_tracker.perspective import transform, validate_line

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
               r'{\an7\pos(0,0)\p1\1a&H20&}m 50 50 l 200 50 200 90 50 90 c{\p0}']
        with tempfile.TemporaryDirectory() as td:
            for i,body in enumerate(cases):
                with self.subTest(case=i):
                    line=dict(text=body,metrics=dict(width=100,height=28),style_data=dict(align=7))
                    actual=render(transform(line,H),td,f'new{i}')
                    expected=cv2.warpPerspective(render(body,td,f'old{i}'),H,(320,240))
                    a=actual>90;b=expected>90
                    iou=np.sum(a&b)/np.sum(a|b)
                    self.assertGreater(iou,.86,f'case {i}: rendered overlap {iou:.3f}')

    def test_clip_and_rejection(self):
        line=dict(text=r'{\an7\pos(20,30)\clip(0,0,100,100)}Track',metrics=dict(width=100,height=28))
        H=np.array([[1,.05,3],[0,1,5],[0,0,1]])
        self.assertIn(r'\clip(m 3 5 l 103 5 l 108 105 l 8 105 c)',transform(line,H))
        self.assertIn(r'\clip(0,0,100,100)',transform(line,H,False))
        with self.assertRaises(ValueError):validate_line(dict(text=r'{\pos(1,2)}No metrics'))
        with self.assertRaises(ValueError):validate_line(dict(text=r'{\an7\pos(0,0)\p1}m 0 0 b 1 2 3 4'))


if __name__=='__main__':unittest.main()
