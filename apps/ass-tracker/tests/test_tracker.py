import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import cv2
import numpy as np
from ass_tracker.core import track_frames, run_job, decoded_frames, validate_job, Cancelled
from ass_tracker.subtitles import translate, generate, validate_text, retime

header='[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'


def fixture():
    rng = np.random.default_rng(127)
    target = rng.integers(20, 235, (38, 52), dtype=np.uint8)
    target = cv2.GaussianBlur(target, (3, 3), 0)
    positions = [(35, 42), (35, 42), (62, 50), (83, 68), (83, 68), (52, 60)]
    frames = np.full((len(positions), 150, 220), 105, np.uint8)
    for frame, (x, y) in zip(frames, positions):
        frame[y:y+38, x:x+52] = target
    job = dict(schema="ass-tracker-job-v1", job_id="fixture", video="unused", video_width=220, video_height=150,
               script_width=440, script_height=300, start_frame=10, end_frame=16, reference_frame=12,
               boundaries_ms=[1000,1040,1080,1130,1170,1220,1270], roi=[62,50,52,38],
               options=dict(search_radius=60,threshold=.85,quantum=.5),
               lines=[dict(index=5,start_frame=10,end_frame=16,start_time=1000,end_time=1270,
                           text=r"{\pos(100,120)\org(120,130)\clip(1,2,40,50)}test")])
    return frames, positions, job


class TrackingTests(unittest.TestCase):
    def test_line_edges_preserve_full_original_duration(self):
        frames, _, job = fixture()
        job['lines'][0]['start_time'] = 995
        job['lines'][0]['end_time'] = 1280
        result = generate(job, track_frames(frames, job))
        self.assertEqual(result[0]['start_time'], 995)
        self.assertEqual(result[-1]['end_time'], 1280)
        self.assertTrue(all(a['end_time'] == b['start_time'] for a,b in zip(result,result[1:])))

    def test_bidirectional_jumps_and_holds(self):
        frames, positions, job = fixture()
        rows = track_frames(frames, job)
        self.assertTrue(all(r['ok'] for r in rows))
        for row, (x,y) in zip(rows, positions):
            self.assertAlmostEqual(row['dx'], x-62, delta=.5)
            self.assertAlmostEqual(row['dy'], y-50, delta=.5)
        self.assertEqual(rows[0]['dx'], rows[1]['dx'])
        self.assertEqual(rows[3]['dy'], rows[4]['dy'])
        events = generate(job, rows)
        self.assertEqual(len(events), 4)
        self.assertIn(r'\pos(46,104)', events[0]['text'])
        self.assertIn(r'\org(66,114)', events[0]['text'])
        self.assertEqual(events[0]['start_time'],1000)
        self.assertEqual(events[-1]['end_time'],1270)

    def test_occlusion_stops_no_fake_result(self):
        frames, _, job = fixture()
        frames[3] = 105
        rows = track_frames(frames, job)
        self.assertFalse(rows[3]['ok'])
        self.assertEqual(rows[4]['reason'], '未追踪')
        with self.assertRaises(ValueError): generate(job, rows)

    def test_ambiguous_repeated_target(self):
        frames, _, job = fixture()
        frames[3,68:106,145:197] = frames[2,50:88,62:114]
        job['options']['search_radius'] = 100
        rows = track_frames(frames, job)
        self.assertFalse(rows[3]['ok'])
        self.assertIn('相似',rows[3]['reason'])

    def test_flat_roi_and_cancel(self):
        frames, _, job = fixture()
        job['roi'] = [0,0,20,20]
        with self.assertRaises(ValueError): track_frames(frames,job)
        job['roi'] = [62,50,52,38]
        with self.assertRaises(Cancelled): track_frames(frames,job,cancelled=lambda:True)

    def test_no_search_beyond_configured_radius(self):
        frames, _, job = fixture()
        job['options']['search_radius'] = 6
        rows = track_frames(frames,job)
        self.assertFalse(rows[3]['ok'])


class SubtitleTests(unittest.TestCase):
    def test_shapes_and_vector_clip_scale(self):
        value = r'{\pos(0,0)\clip(2,m 20 40 l 60 80)\p1}m 0 0 l 10 10{\p0}'
        result = translate(value,5,-3)
        self.assertIn(r'\pos(5,-3)',result)
        self.assertIn(r'\clip(2,m 30 34 l 70 74)',result)
        self.assertTrue(result.endswith(r'm 0 0 l 10 10{\p0}'))
        self.assertIn(r'\clip(2,m 20 40 l 60 80)',translate(value,5,-3,False))

    def test_time_dependent_tags_rejected(self):
        for tag in [r'\move(0,0,1,1)',r'\t(0,10,\fs40)',r'\k20',r'\fad(1,2,3)',r'\fad(10.5,10)',r'\fad(10,10)\fade(1,2)']:
            with self.assertRaises(ValueError):validate_text(r'{\pos(1,2)'+tag+'}x')
        with self.assertRaises(ValueError):validate_text('unpositioned')
        for tag in [r'\fad(10,10)',r'\fade(255,0,255,0,10,20,30)',r'\rScreen',r'\r']:
            validate_text(r'{\pos(1,2)'+tag+'}x')

    def test_fade_keeps_source_clock(self):
        frames, _, job = fixture()
        job['lines'][0]['text'] = r'{\pos(100,120)\fad(100,150)}test'
        events = generate(job, track_frames(frames, job))
        self.assertEqual(len(events), 4)  # Held frames still merge.
        for event in events:
            s = event['start_time']-1000
            self.assertIn(rf'\fade(255,0,255,{-s},{100-s},{120-s},{270-s})', event['text'])
        still = [dict(ok=True, dx=0., dy=0.) for _ in range(6)]
        self.assertEqual(generate(job, still)[0]['text'], job['lines'][0]['text'])
        # libass reads a 7-argument fade with t1 = t4 = -1 as the two-argument form.
        self.assertEqual(retime(r'{\pos(1,2)\fade(10,200,30,-1,40,50,-1)}x',100,300),
                         r'{\pos(1,2)\fade(10,200,30,-100,-60,150,200)}x')
        self.assertEqual(retime(r'{\fade(0,255,0,99,99,99,99)}x',100,300),r'{\fade(0,255,0,-1,-1,-1,-2)}x')

    def test_reset_is_kept_in_translation(self):
        value = r'{\pos(1,2)\frz10}A{\rAlt\c&H0000FF&}B'
        self.assertEqual(translate(value,3,4),r'{\pos(4,6)\frz10}A{\rAlt\c&H0000FF&}B')

    def test_zero_motion_preserves_all_text(self):
        value = r'{\pos(2.500,3)\frz-12\blur0.35}中文\Ntest'
        self.assertEqual(translate(value,0,0),value)

    def test_rect_clip_and_anisotropic_playres(self):
        self.assertEqual(translate(r'{\pos(1,2)\iclip(0,0,10,20)}x',2,3),r'{\pos(3,5)\iclip(2,3,12,23)}x')
        _,_,job=fixture();job['script_height']=150
        rows=[dict(ok=True,dx=3.,dy=5.) for _ in range(6)]
        output=generate(job,rows)
        self.assertEqual(len(output),1)
        self.assertIn(r'\pos(106,125)',output[0]['text'])


class VideoIntegrationTests(unittest.TestCase):
    def test_lossless_decode_preserves_indices_and_duplicate_frames(self):
        frames,positions,job=fixture()
        full=np.concatenate([np.full((5,150,220),22,np.uint8),frames,np.full((3,150,220),199,np.uint8)])
        with tempfile.TemporaryDirectory(prefix='追踪测试 空格 ') as td:
            video=Path(td)/'样片.mkv'
            subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','gray','-s','220x150','-r','24000/1001','-i','-','-c:v','ffv1',str(video)],input=full.tobytes(),check=True)
            job.update(video=str(video),start_frame=5,end_frame=11,reference_frame=7)
            job['lines'][0].update(start_frame=5,end_frame=11)
            with decoded_frames(job,220) as decoded:
                np.testing.assert_array_equal(decoded,frames)
            result=run_job(job)
            self.assertEqual(result['status'],'complete')
            self.assertEqual(len(result['track']),6)
            self.assertEqual(result['generated'][0]['start_time'],1000)

    def test_split_fade_renders_like_source_line(self):
        def frame(start,end,text,at):
            with tempfile.TemporaryDirectory() as td:
                (Path(td)/'f.ass').write_text(header+f'Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n',encoding='utf-8')
                p=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=320x240:r=100','-vf','ass=f.ass','-ss',str(at),
                                  '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],cwd=td,capture_output=True,check=True)
                return np.frombuffer(p.stdout,np.uint8).reshape(240,320,3)
        text=r'{\an7\pos(40,40)\fad(400,600)}Track'
        opaque=frame('0:00:00.00','0:00:02.00',text,.7)
        for start,end,at in [(100,500,.25),(1500,2000,1.6)]:
            source=frame('0:00:00.00','0:00:02.00',text,at)
            self.assertFalse(np.array_equal(source,opaque))  # Mid-fade, not a trivial comparison.
            split=frame(f'0:00:{start/1000:05.2f}',f'0:00:{end/1000:05.2f}',retime(text,start,2000),at)
            np.testing.assert_array_equal(split,source)

    def test_projective_text_and_drawing_translate_pixel_exactly(self):
        # Moving both pos and org is required even for a translation-only track.
        text=r'{\an7\pos(70,70)\org(90,90)\frz15\frx8\fry-5}Track'
        with tempfile.TemporaryDirectory() as td:
            images=[]
            for i,value in enumerate([text,translate(text,8,6)]):
                ass=Path(td)/f'{i}.ass';ass.write_text(header+'Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,'+value+'\n')
                p=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=320x240','-vf',f'ass={i}.ass','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],cwd=td,capture_output=True,check=True)
                images.append(np.frombuffer(p.stdout,np.uint8).reshape(240,320,3))
            expected=cv2.warpAffine(images[0],np.float32([[1,0,8],[0,1,6]]),(320,240))
            np.testing.assert_array_equal(expected,images[1])


if __name__=='__main__': unittest.main()
