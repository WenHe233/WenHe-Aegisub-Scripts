"""Exercise Tk state transitions; these are API tests, not manual UI evidence."""
import copy
import json
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import numpy as np
import unittest
from unittest.mock import patch
from ass_tracker.gui import App
from ass_tracker.subtitles import generate
from ass_tracker.layout import Viewport


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.addCleanup(lambda:(self.root.update(),self.root.destroy()))
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
            a.viewport=Viewport.fit(320,240,320,240)
            a.mouse_down(SimpleNamespace(x=10,y=10))
            self.assertIsNotNone(a.result)  # Drafts are transactional.
            a.mouse_up(SimpleNamespace(x=100,y=100))
            self.assertIsNone(a.result)
            with patch.object(a,'show_frame'):a.set_reference()
            self.assertNotIn('roi',a.job)

    def prepare_selection(self):
        a=self.app
        a.job=dict(video_width=320,video_height=240,reference_frame=0,start_frame=0,end_frame=2,roi=[20,20,200,160])
        a.displayed_frame=0
        a.viewport=Viewport.fit(320,240,400,400)
        a.result=dict(status='complete',track=[dict(ok=True,H=np.eye(3).tolist())]*2)
        a.update_buttons()
        return a

    def click_video(self,x,y):
        a=self.app
        x,y=a.viewport.to_canvas((x,y))
        return SimpleNamespace(x=x,y=y)

    def test_polygon_gestures_transaction_and_invalid_edit(self):
        a=self.prepare_selection();a.tool.set('polygon')
        old=copy.deepcopy(a.job);result=a.result;revision=a.revision
        a.mouse_down(SimpleNamespace(x=3,y=3))  # Letterbox.
        self.assertFalse(a.editing)
        for p in [(20,20),(220,20),(220,180),(100,90),(20,180)]:a.mouse_down(self.click_video(*p))
        self.assertTrue(a.editing);self.assertEqual(str(a.run_button['state']),'disabled')
        with patch.object(a,'worker') as worker:a.track();worker.assert_not_called()
        a.undo_point();self.assertEqual(len(a.draft_points),4)
        a.cancel_edit();self.assertEqual(a.job,old);self.assertIs(a.result,result);self.assertEqual(a.revision,revision)
        for p in [(20,20),(220,20),(220,180),(100,90),(20,180)]:a.mouse_down(self.click_video(*p))
        a.mouse_down(self.click_video(20,20))  # Close by first point.
        self.assertFalse(a.editing);self.assertEqual(len(a.job['roi_polygon']),5)
        self.assertIsNone(a.result);self.assertEqual(a.revision,revision+1)
        saved=copy.deepcopy(a.job['roi_polygon'])
        a.mouse_down(self.click_video(20,20));a.mouse_up(self.click_video(210,170))
        self.assertTrue(a.editing);self.assertIn('选区无效',a.status.get())
        self.assertEqual(a.job['roi_polygon'],saved)
        a.cancel_edit()
        a.mouse_down(self.click_video(20,20));a.mouse_up(self.click_video(30,30))
        self.assertFalse(a.editing);self.assertEqual(a.job['roi_polygon'][0],[30.,30.])
        a.reset_region()
        for p in [(30,40),(200,40),(100,170)]:a.mouse_down(self.click_video(*p))
        a.finish_polygon()  # Same command as Enter.
        self.assertEqual(len(a.job['roi_polygon']),3)

    def test_polygon_limit_and_rectangle_replacement(self):
        a=self.prepare_selection();a.tool.set('polygon')
        for angle in np.linspace(0,2*np.pi,64,endpoint=False):
            a.mouse_down(self.click_video(160+100*np.cos(angle),120+80*np.sin(angle)))
        # Directly extending the draft avoids the intentional first-point snap.
        a.draft_points=[[160+100*np.cos(t),120+80*np.sin(t)] for t in np.linspace(0,2*np.pi,64,endpoint=False)]
        a.draft_closed=False
        a.mouse_down(self.click_video(160,120));self.assertEqual(len(a.draft_points),64)
        a.finish_polygon();self.assertFalse(a.editing)
        a.tool.set('rectangle');a.tool_changed()
        a.mouse_down(self.click_video(30,40));a.mouse_up(self.click_video(200,170))
        self.assertNotIn('roi_polygon',a.job);self.assertEqual(a.job['roi'],[30,40,170,130])

    def test_responsive_layout_cached_image_and_long_status(self):
        a=self.prepare_selection();root=self.root
        root.deiconify()
        a.preview_image=__import__('PIL.Image',fromlist=['Image']).fromarray(np.zeros((240,320),np.uint8))
        old=copy.deepcopy(a.job);result=a.result;revision=a.revision
        for scaling in [96/72,120/72,144/72,192/72]:
            root.tk.call('tk','scaling',scaling)
            for width,height in [(640,480),(800,600),(1180,850),(1500,700)]:
                root.geometry(f'{width}x{height}');root.update()
                a.status.set('很长的错误提示和路径 / '*100);root.update()
                with patch('ass_tracker.gui.read_reference',side_effect=AssertionError('resize must not decode')):
                    a.render_preview()
                for widget in [a.run_button,a.save_button,a.slider,a.frame_entry]:
                    x=widget.winfo_rootx()-root.winfo_rootx();y=widget.winfo_rooty()-root.winfo_rooty()
                    self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
                    self.assertLessEqual(x+widget.winfo_width(),root.winfo_width(),(scaling,width,height))
                    self.assertLessEqual(y+widget.winfo_height(),root.winfo_height(),(scaling,width,height))
                v=a.viewport
                self.assertGreaterEqual(v.x,0);self.assertGreaterEqual(v.y,0)
                self.assertLessEqual(v.x+v.width,a.canvas.winfo_width())
                self.assertLessEqual(v.y+v.height,a.canvas.winfo_height())
                np.testing.assert_allclose(v.to_video(*v.to_canvas((123.4,87.6))),[123.4,87.6])
        self.assertEqual(a.job,old);self.assertIs(a.result,result);self.assertEqual(a.revision,revision)
        a.open_advanced();root.update()
        self.assertTrue(a.advanced_window.winfo_exists())
        self.assertLess(a.save_button.winfo_rooty()-root.winfo_rooty(),root.winfo_height())
        a.advanced_window.destroy()

    def test_preview_draws_transformed_concave_outline(self):
        a=self.prepare_selection()
        points=[[20,20],[220,20],[220,180],[100,90],[20,180]]
        a.job['roi_polygon']=points
        H=np.array([[1.02,.04,3],[0,.98,5],[.0002,0,1]])
        a.result['track'][1]=dict(ok=True,H=H.tolist())
        a.displayed_frame=1;a.render_selection()
        from ass_tracker.planar import warp
        expected=np.array([a.viewport.to_canvas(p) for p in warp(points,H)]).ravel()
        np.testing.assert_allclose(a.canvas.coords(a.canvas.find_withtag('roi')[0]),expected)

    def test_layout_scaling_before_widget_creation(self):
        for factor in [1,1.25,1.5,2]:
            root=tk.Tk();root.tk.call('tk','scaling',factor*96/72)
            try:
                a=App(root,bridge_dir=Path(tempfile.gettempdir())/'unused-tracker-ui-test')
                root.geometry('640x480');root.update()
                for widget in [a.run_button,a.save_button,a.frame_entry,a.slider]:
                    self.assertLessEqual(widget.winfo_rooty()-root.winfo_rooty()+widget.winfo_height(),480,factor)
                    self.assertLessEqual(widget.winfo_rootx()-root.winfo_rootx()+widget.winfo_width(),640,factor)
                self.assertGreaterEqual(a.canvas.winfo_height(),32)
                self.assertTrue(a.settings_panel.bar.winfo_ismapped())
            finally:root.update();root.destroy()

    def test_polygon_save_and_reopen_configured_job(self):
        from test_tracker import fixture
        from ass_tracker.core import track_frames
        from ass_tracker import __version__
        a=self.app
        frames,_,job=fixture()
        job.update(tool_version=__version__,video=str(Path(__file__)),
                   roi_polygon=[[62,50],[113,50],[113,87],[88,69],[62,87]])
        rows=track_frames(frames,job)
        a.job=copy.deepcopy(job)
        a.result=dict(status='complete',job=job,track=rows,generated=generate(job,rows))
        a.reviewed.set(True)
        with tempfile.TemporaryDirectory() as td:
            a.path=Path(td)/'demo.job.json';result=Path(td)/'demo.result.json'
            with patch('ass_tracker.gui.filedialog.asksaveasfilename',return_value=str(result)):a.save()
            configured=Path(td)/'demo.configured.job.json'
            self.assertTrue(result.is_file());self.assertTrue(configured.is_file())
            with patch.object(a,'show_frame'):a.open_job(configured)
            self.assertEqual(a.job['roi_polygon'],job['roi_polygon'])
            self.assertEqual(a.tool.get(),'polygon');self.assertIsNone(a.result)


if __name__=='__main__':unittest.main()
