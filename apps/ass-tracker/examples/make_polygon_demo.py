"""Generate a redistributable synthetic clip; no private video or fonts required."""
from pathlib import Path
import json
import subprocess
import sys
import cv2
import numpy as np

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from ass_tracker import __version__
from ass_tracker.core import executable, CREATE_FLAGS
from ass_tracker.regions import parse_region


def make(output):
    output.mkdir(parents=True,exist_ok=True)
    width,height,count=640,360,25
    points=[[140,70],[460,70],[460,280],[330,280],[300,180],[140,260]]
    job=dict(schema='ass-tracker-job-v1',tool_version=__version__,job_id='synthetic-concave-demo',
        mode='similarity',video=str((output/'concave.mkv').resolve()),video_width=width,video_height=height,
        script_width=width,script_height=height,start_frame=0,end_frame=count,reference_frame=0,
        boundaries_ms=[i*40 for i in range(count+1)],roi_polygon=points,move_clips=True,scale_appearance=True,
        options=dict(search_radius=120,max_width=640),lines=[dict(index=1,start_frame=0,end_frame=count,
        start_time=0,end_time=count*40,text=r'{\an7\pos(190,115)\bord1}Polygon TEST',
        style_data=dict(align=7,outline=1,shadow=0,fontsize=28,scale_x=100,scale_y=100),
        metrics=dict(width=185,height=32,descent=6))])
    region=parse_region(job);region.apply(job)
    mask=region.mask((height,width),(width,height))
    rng=np.random.default_rng(501)
    texture=cv2.GaussianBlur(rng.integers(30,225,(height,width),dtype=np.uint8),(3,3),0)
    cv2.putText(texture,'SAME PLANE',(165,130),cv2.FONT_HERSHEY_SIMPLEX,1,245,2)
    frames=[]
    for i in range(count):
        H=np.vstack([cv2.getRotationMatrix2D((300,170),i*.15,1+i*.002),[0,0,1]])
        H[0,2]+=i;H[1,2]+=i*.3
        active=cv2.warpPerspective(mask,H,(width,height),flags=cv2.INTER_NEAREST)>0
        frame=rng.integers(10,130,(height,width),dtype=np.uint8)
        plane=cv2.warpPerspective(texture,H,(width,height));frame[active]=plane[active]
        frames.append(frame)
    subprocess.run([executable('ffmpeg'),'-v','error','-y','-f','rawvideo','-pix_fmt','gray','-s',f'{width}x{height}',
        '-r','25','-i','-','-c:v','ffv1',job['video']],input=np.array(frames).tobytes(),check=True,creationflags=CREATE_FLAGS)
    target=output/'concave.job.json'
    target.write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target)


if __name__=='__main__':make(APP.parents[1]/'build/polygon-demo')
