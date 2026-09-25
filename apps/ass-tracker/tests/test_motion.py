import unittest
import numpy as np
import cv2
from ass_tracker.motion import MODES, toggled, estimate
from ass_tracker.planar import warp, track_planar, corners
from ass_tracker.perspective import transform
from ass_tracker.paths import transform_path, transform_clip
from ass_tracker.appearance import local_scale
from test_planar import render
import tempfile


class MotionTests(unittest.TestCase):
    def test_nested_selection(self):
        self.assertEqual(toggled('similarity',4,True),'perspective')
        self.assertEqual(toggled('perspective',2,False),'translation_scale')
        self.assertEqual(toggled('translation',0,False),'translation')

    def test_constrained_models_with_outliers(self):
        rng=np.random.default_rng(5)
        p=rng.uniform(0,500,(100,2))
        matrices=[np.array([[1.2,0,10],[0,1.2,-4],[0,0,1.]]),
                  np.array([[1.1,-.2,20],[.2,1.1,-8],[0,0,1.]]),
                  np.array([[1.1,.2,20],[-.1,.9,10],[0,0,1.]]),
                  np.array([[1.1,.2,20],[-.1,.9,10],[.0003,-.0001,1.]])]
        for mode,H in zip(MODES[1:],matrices):
            q=warp(p,H);q[:20]=rng.uniform(0,500,(20,2))
            fitted,mask=estimate(p,q,mode)
            np.testing.assert_allclose(warp(p,fitted),warp(p,H),atol=.01)
            self.assertGreaterEqual(np.asarray(mask).sum(),80)
        # A constrained fit must never introduce rotation, even with noisy data.
        H,_=estimate(p,warp(p,matrices[1]),'translation_scale',20)
        self.assertEqual(H[0,1],0);self.assertEqual(H[1,0],0)
        self.assertEqual(H[0,0],H[1,1])

    def test_image_tracking_all_feature_models(self):
        rng=np.random.default_rng(35)
        ref=cv2.GaussianBlur(rng.integers(0,256,(240,320),dtype=np.uint8),(3,3),0)
        matrices=[[[1.04,0,5],[0,1.04,3],[0,0,1]],
                  [[.99,-.08,15],[.08,.99,-5],[0,0,1]],
                  [[1.02,.08,2],[-.03,.96,8],[0,0,1]],
                  [[1.01,.06,4],[-.02,.98,7],[.0002,-.0001,1]]]
        for mode,H in zip(MODES[1:],matrices):
            H=np.array(H,float)
            frames=np.array([ref,ref,cv2.warpPerspective(ref,H,(320,240)),np.zeros_like(ref),ref])
            job=dict(mode=mode,video_width=320,video_height=240,start_frame=0,reference_frame=1,
                     roi=[30,30,240,170],options=dict(search_radius=80))
            rows=track_planar(frames,job,lambda *a:None,lambda:False)
            self.assertTrue(rows[2]['ok'],rows[2])
            np.testing.assert_allclose(warp(corners(job['roi']),rows[2]['H']),warp(corners(job['roi']),H),atol=.8)
            self.assertFalse(rows[3]['ok']);self.assertFalse(rows[4]['ok'])

    def test_fractional_rectangle_and_appearance(self):
        self.assertIn('m 0.5',transform_clip('clip','0,0,10,20',[[1,0,.5],[0,1,0],[0,0,1]]))
        self.assertEqual(transform_clip('clip','0,0,10,20',[[-1,0,30],[0,-1,40],[0,0,1]]),r'\clip(20,20,30,40)')
        with self.assertRaises(ValueError):
            transform_path('m 0 0 l 100 0 100 100',[[1,0,0],[0,1,0],[-.02,0,1]])
        H=np.diag([2.,2.,1.])
        line=dict(text=r'{\an7\pos(30,40)\bord1\blur.5\be2}T{\xbord3\xshad-4}est',
                  metrics=dict(width=100,height=28),style_data=dict(outline=2,shadow=3))
        value=transform(line,H,scale_appearance=True)
        for tag in [r'\bord4',r'\shad6',r'\bord2',r'\blur1',r'\be2',r'\xbord6',r'\xshad-8']:
            self.assertIn(tag,value)
        self.assertEqual(local_scale(np.eye(3),[0,0]),1)

    def test_render_curves_clips_and_drawing_anchors(self):
        H=np.array([[1.015,.045,4],[-.02,.985,8],[.0002,-.00015,1]])
        paths=['m 40 40 b 40 5 160 5 160 40 l 160 120 b 160 150 40 150 40 120',
               'm 40 40 s 160 40 160 120 40 120 c',
               'm 40 40 l 160 40 160 120 40 120 m 70 60 l 70 100 130 100 130 60']
        cases=[]
        for path in paths:
            cases.append(r'{\an7\pos(0,0)\p1}'+path)
            cases.append(r'{\an7\pos(0,0)\p1\clip('+path+r')}m 0 0 l 220 0 220 180 0 180')
            cases.append(r'{\an7\pos(0,0)\p1\iclip('+path+r')}m 0 0 l 220 0 220 180 0 180')
        cases += [r'{\an5\pos(130,100)\p2\pbo12\fscx80\fscy110\frz12}m 0 0 l 200 0 200 80 0 80',
                  r'{\an1\pos(60,140)\org(80,90)\p3\pbo-10\frx8\fry-4\fax.1}m 40 40 b 40 5 160 5 160 40 l 160 120 40 120']
        with tempfile.TemporaryDirectory() as td:
            for i,body in enumerate(cases):
                line=dict(text=body,style_data=dict(align=7))
                actual=render(transform(line,H),td,f'new{i}')
                expected=cv2.warpPerspective(render(body,td,f'old{i}'),H,(320,240))
                a,b=actual>90,expected>90
                overlap=np.sum(a&b)/max(1,np.sum(a|b))
                self.assertGreater(overlap,.94,f'case {i}: {overlap:.3f}')


if __name__=='__main__':unittest.main()
