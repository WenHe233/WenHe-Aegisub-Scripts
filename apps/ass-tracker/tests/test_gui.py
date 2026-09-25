"""Exercise Tk state transitions; these are API tests, not manual UI evidence."""
import copy
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from ass_tracker.gui import App
from ass_tracker.subtitles import generate


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.app=App(self.root)

    def test_defaults_nested_checkboxes_and_stale_worker(self):
        a=self.app
        self.assertEqual(a.mode.get(),'similarity')
        self.assertEqual([v.get() for v in a.motion_vars],[True,True,True,False,False])
        self.assertTrue(a.move_clips.get());self.assertTrue(a.scale_appearance.get())
        a.motion_vars[4].set(True);a.motion_changed(4)
        self.assertEqual(a.mode.get(),'perspective')
        revision=a.revision
        a.motion_vars[2].set(False);a.motion_changed(2)
        self.assertEqual(a.mode.get(),'translation_scale')
        a.tracked(dict(status='complete',generated=[]),revision)
        self.assertIsNone(a.result)
        self.assertEqual(str(a.save_button['state']),'disabled')

    def test_legacy_restore_and_appearance_reuses_matrices(self):
        a=self.app
        with tempfile.TemporaryDirectory() as td:
            video=Path(td)/'fixture';video.touch()
            line=dict(index=3,text=r'{\an7\pos(30,40)\bord2\clip(0,0,100,100)}Test',
                      metrics=dict(width=100,height=28),style_data=dict(align=7,outline=1,shadow=0),
                      start_frame=0,end_frame=2,start_time=0,end_time=80)
            job=dict(schema='ass-tracker-job-v1',job_id='test',video=str(video),
                     start_frame=0,end_frame=2,reference_frame=0,video_width=320,video_height=240,
                     script_width=320,script_height=240,boundaries_ms=[0,40,80],lines=[line])
            p=Path(td)/'old.job.json';p.write_text(json.dumps(job))
            with patch.object(a,'show_frame'):a.open_job(p)
            self.assertEqual(a.mode.get(),'translation')
            self.assertFalse(a.scale_appearance.get())
            job['mode']='similarity'
            track=[dict(ok=True,H=[[1,0,0],[0,1,0],[0,0,1]]),dict(ok=True,H=[[2,0,0],[0,2,0],[0,0,1]])]
            a.job=copy.deepcopy(job)
            a.result=dict(status='complete',job=copy.deepcopy(job),track=copy.deepcopy(track),generated=generate(job,track))
            before=a.revision
            a.scale_appearance.set(True);a.move_clips.set(False)
            with patch.object(a,'worker',side_effect=lambda f,done:done(f())),patch('ass_tracker.gui.run_job',side_effect=AssertionError('must not retrack')):
                a.appearance_changed()
            self.assertEqual(a.revision,before);self.assertEqual(a.result['track'],track)
            self.assertIn(r'\bord4',a.result['generated'][1]['text'])
            self.assertIn(r'\clip(0,0,100,100)',a.result['generated'][1]['text'])
            a.displayed_frame=0
            a.mouse_down(type('Event',(),dict(x=10,y=10))())
            self.assertIsNone(a.result)
            with patch.object(a,'show_frame'):a.set_reference()
            self.assertNotIn('roi',a.job)


if __name__=='__main__':unittest.main()
