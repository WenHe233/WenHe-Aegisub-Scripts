"""Validated tracking regions, expressed in original video coordinates."""
from dataclasses import dataclass
import cv2
import numpy as np


def _cross(a, b, c):
    u, v = b-a, c-a
    return float(u[0]*v[1]-u[1]*v[0])


def _intersects(a, b, c, d):
    def on(p, q, r):
        return abs(_cross(p,q,r)) < 1e-8 and np.all(r >= np.minimum(p,q)-1e-8) and np.all(r <= np.maximum(p,q)+1e-8)
    x,y,z,w = _cross(a,b,c),_cross(a,b,d),_cross(c,d,a),_cross(c,d,b)
    return (x*y < 0 and z*w < 0) or on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b)


@dataclass
class Region:
    points: np.ndarray
    bounds: list
    polygon: bool

    def mask(self, shape, video_size):
        height, width = shape
        points = self.points * [width/video_size[0], height/video_size[1]]
        mask = np.zeros((height,width),np.uint8)
        cv2.fillPoly(mask,[np.round(points).astype(np.int32)],255)
        return mask

    def apply(self, job):
        job['roi'] = list(self.bounds)
        if self.polygon: job['roi_polygon'] = self.points.tolist()
        else: job.pop('roi_polygon',None)


def parse_region(job):
    width, height = job['video_width'], job['video_height']
    polygon = 'roi_polygon' in job
    try:
        if polygon:
            points = np.asarray(job['roi_polygon'],dtype=float)
            if points.ndim != 2 or points.shape[1] != 2 or not 3 <= len(points) <= 64:
                raise ValueError('多边形需要 3～64 个顶点。')
        else:
            roi = np.asarray(job.get('roi'),dtype=float)
            if roi.shape != (4,): raise ValueError('请先框选追踪区域。')
            x,y,w,h = roi
            if min(w,h) <= 0: raise ValueError('追踪区域宽高必须大于零。')
            points = np.array([[x,y],[x+w,y],[x+w,y+h],[x,y+h]])
    except (TypeError,OverflowError) as exc:
        raise ValueError('无法解析追踪区域坐标。') from exc
    if not np.isfinite(points).all(): raise ValueError('追踪区域坐标必须为有限数值。')
    if np.any(points < 0) or np.any(points > [width,height]): raise ValueError('追踪区域超出视频画面。')
    if polygon:
        count = len(points)
        for i in range(count):
            if np.linalg.norm(points[i]-points[(i+1)%count]) < 1e-6:
                raise ValueError('多边形不能有重复的相邻顶点。')
            # Reject overlapping adjacent edges as well as non-adjacent crossings.
            a,b,c = points[i-1],points[i],points[(i+1)%count]
            if abs(_cross(a,b,c)) < 1e-8 and np.dot(a-b,c-b) > 0:
                raise ValueError('多边形边不能重叠。')
            for j in range(i+1,count):
                if j == i+1 or (i == 0 and j == count-1): continue
                if _intersects(points[i],points[(i+1)%count],points[j],points[(j+1)%count]):
                    raise ValueError('多边形不能自交或重复经过同一顶点。')
    area = abs(cv2.contourArea(points.astype(np.float32)))
    lo, hi = points.min(axis=0), points.max(axis=0)
    if area < 1e-6: raise ValueError('追踪区域面积为零。')
    if polygon and (min(hi-lo) < 12 or area < 144): raise ValueError('多边形区域太小，请扩大选区。')
    return Region(points,[*lo.tolist(),*(hi-lo).tolist()],polygon)


def upgrade_standalone_job(job):
    """Only known old rectangle jobs are migrated; bridge handshakes stay strict."""
    import copy
    from . import __version__
    value=copy.deepcopy(job)
    if value.get('schema')=='ass-tracker-job-v1' and value.get('tool_version') in ('0.4.0','0.4.1'):
        if 'roi_polygon' in value: raise ValueError('旧版任务不应包含多边形区域。')
        value['tool_version']=__version__
    return value
