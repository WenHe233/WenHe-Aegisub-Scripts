"""Region contracts and masked tracking against independently moving backgrounds."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from ass_tracker import __version__
from ass_tracker.regions import parse_region, upgrade_standalone_job
from ass_tracker.core import track_frames, match_frame, validate_job
from ass_tracker.planar import track_planar, warp
from ass_tracker.motion import MODES
from test_tracker import fixture


POLYGONS=[[[40,35],[265,55],[245,200],[55,180]],
          [[40,35],[265,35],[265,190],[160,100],[40,190]]]


class RegionTests(unittest.TestCase):
    def test_concave_mask_bounds_serialization_and_versions(self):
        for points in POLYGONS:
            job=dict(video_width=320,video_height=240,roi_polygon=points,roi=[0,0,1,1])
            region=parse_region(job);region.apply(job)
            np.testing.assert_array_equal(job['roi'],[40,35,225,max(p[1] for p in points)-35])
            restored=parse_region(json.loads(json.dumps(job)))
            np.testing.assert_array_equal(restored.points,points)
            mask=region.mask((240,320),(320,240))
            self.assertEqual(mask[50,50],255);self.assertEqual(mask[220,160],0)
        self.assertEqual(mask[170,160],0)  # The concave notch is outside.
        _,_,job=fixture()
        job['video']=str(Path(__file__))  # Validation only; no decoder in this test.
        for version in ['0.4.0','0.4.1']:
            job['tool_version']=version
            updated=upgrade_standalone_job(job)
            self.assertEqual(updated['tool_version'],__version__)
            self.assertEqual(job['tool_version'],version)
            validate_job(updated)
            if version!=__version__:
                with self.assertRaisesRegex(ValueError,'版本'):validate_job(job)
        job['tool_version']='9.9.9'
        with self.assertRaisesRegex(ValueError,'版本'):validate_job(upgrade_standalone_job(job))

    def test_invalid_regions(self):
        invalid=[[],[[0,0],[100,100]],[[0,0],[100,100],[0,100],[100,0]],
                 [[20,20],[200,20],[100,20]],[[0,0],[100,0],[100,100],[100,0],[0,100]],
                 [[0,0],[100,0],[50,0],[100,100]],[[0,0],[10,0],[10,10]],
                 [[-1,0],[100,0],[100,100]],[[0,0],[321,0],[100,100]],
                 [[0,0],[100,0],[float('nan'),100]],[[20,20]]*65]
        for points in invalid:
            with self.subTest(points=points),self.assertRaises(ValueError):
                parse_region(dict(video_width=320,video_height=240,roi_polygon=points))
        points=[[160+100*np.cos(t),120+80*np.sin(t)] for t in np.linspace(0,2*np.pi,64,endpoint=False)]
        self.assertEqual(len(parse_region(dict(video_width=320,video_height=240,roi_polygon=points)).points),64)

    def test_all_models_exclude_independent_background(self):
        rng=np.random.default_rng(452)
        texture=cv2.GaussianBlur(rng.integers(10,246,(240,320),dtype=np.uint8),(3,3),0)
        matrices=[[[1,0,24],[0,1,13],[0,0,1]],[[1.04,0,5],[0,1.04,3],[0,0,1]],
                  [[.99,-.08,15],[.08,.99,-5],[0,0,1]],[[1.02,.08,2],[-.03,.96,8],[0,0,1]],
                  [[1.01,.06,4],[-.02,.98,7],[.0002,-.0001,1]]]
        for points in POLYGONS:
            for mode,H in zip(MODES,matrices):
                with self.subTest(mode=mode,points=points):
                    job=dict(mode=mode,video_width=320,video_height=240,start_frame=0,reference_frame=1,
                             roi_polygon=points,options=dict(search_radius=80,threshold=.85,quantum=.25))
                    region=parse_region(job);region.apply(job);mask=region.mask((240,320),(320,240))>0
                    # The notch and remaining bbox contain strong, unrelated motion.
                    a=rng.integers(0,256,(240,320),dtype=np.uint8);a[mask]=texture[mask]
                    H=np.array(H,float)
                    target_mask=cv2.warpPerspective(mask.astype(np.uint8),H,(320,240),flags=cv2.INTER_NEAREST)>0
                    b=rng.integers(0,256,(240,320),dtype=np.uint8)
                    target=cv2.warpPerspective(texture,H,(320,240));b[target_mask]=target[target_mask]
                    frames=np.array([a,a,b,np.zeros_like(a),b])
                    rows=track_frames(frames,job) if mode=='translation' else track_planar(frames,job,lambda *a:None,lambda:False)
                    self.assertTrue(rows[2]['ok'],rows[2])
                    np.testing.assert_allclose(warp(points,rows[2]['H']),warp(points,H),atol=1)
                    self.assertFalse(rows[3]['ok']);self.assertEqual(rows[4]['reason'],'未追踪')

    def test_translation_flat_repeated_large_jump_and_invalid_scores(self):
        frames,positions,job=fixture()
        job['roi_polygon']=[[62,50],[113,50],[113,87],[88,69],[62,87]]
        rows=track_frames(frames,job)
        self.assertTrue(all(r['ok'] for r in rows),rows)
        for row,(x,y) in zip(rows,positions):
            self.assertAlmostEqual(row['dx'],x-62,delta=.5);self.assertAlmostEqual(row['dy'],y-50,delta=.5)
        mask=parse_region(job).mask(frames.shape[1:],(220,150))>0
        flat=frames.copy();flat[2][mask]=105
        with self.assertRaisesRegex(ValueError,'细节'):track_frames(flat,job)
        frames[3,68:106,145:197]=frames[2,50:88,62:114]
        job['options']['search_radius']=100
        self.assertIn('相似',track_frames(frames,job)[3]['reason'])
        image=np.zeros((50,50),np.uint8);template=np.zeros((12,12),np.uint8);mask=np.ones_like(template)*255
        with patch('ass_tracker.core.cv2.matchTemplate',return_value=np.array([[np.nan,np.inf],[-np.inf,np.nan]],np.float32)):
            result=match_frame(image,template,(10,10),20,.8,.02,mask)
            self.assertFalse(result['ok']);self.assertIn('有效',result['reason'])
        scores=np.array([[np.nan,.1,np.nan],[np.inf,.99,-np.inf],[np.nan,.1,np.nan]],np.float32)
        with patch('ass_tracker.core.cv2.matchTemplate',return_value=scores):
            result=match_frame(image,template,(10,10),20,.8,.02,mask)
            self.assertTrue(result['ok'],result)
            self.assertTrue(np.isfinite([result['x'],result['y'],result['gap']]).all())


if __name__=='__main__':unittest.main()
