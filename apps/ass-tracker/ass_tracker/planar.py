"""Fixed-reference SIFT/RANSAC planar tracking. Homographies use video pixels."""
import cv2
import numpy as np
from .motion import estimate, MODES, LABELS


def warp(points, matrix):
    points = np.asarray(points, dtype=float)
    q = np.c_[points, np.ones(len(points))] @ np.asarray(matrix).T
    if not np.isfinite(q).all() or np.any(np.abs(q[:, 2]) < 1e-7):
        raise ValueError('透视变换接近无穷远，不能应用。')
    return q[:, :2] / q[:, 2:]


def corners(roi):
    x, y, w, h = roi
    return np.array([[x,y],[x+w,y],[x+w,y+h],[x,y+h]], float)


def track_planar(frames, job, progress, cancelled):
    from .core import Cancelled
    roi = job.get('roi', [])
    if len(roi) != 4 or not np.isfinite(roi).all():
        raise ValueError('请框选同一平面的追踪区域。')
    x,y,w,h = roi
    if min(x,y)<0 or min(w,h)<30 or x+w>job['video_width'] or y+h>job['video_height']:
        raise ValueError('透视追踪区域无效或太小。')
    scale = np.diag([frames.shape[2]/job['video_width'], frames.shape[1]/job['video_height'], 1.])
    inverse = np.linalg.inv(scale)
    quad = warp(corners(roi), scale)
    mask = np.zeros(frames.shape[1:], np.uint8)
    cv2.fillConvexPoly(mask, np.round(quad).astype(np.int32), 255)
    ref = job['reference_frame']-job['start_frame']
    sift = cv2.SIFT_create(nfeatures=6000, contrastThreshold=.012, edgeThreshold=12)
    keys, descriptors = sift.detectAndCompute(frames[ref], mask)
    if descriptors is None or len(keys)<16:
        raise ValueError('平面区域特征不足。扩大框选，包含多处文字和边缘。')
    options = job.get('options', {})
    radius = float(options.get('search_radius',220))*scale[0,0]
    if not 4 <= radius <= 2000:
        raise ValueError('搜索半径超出范围。')
    matcher = cv2.BFMatcher()
    rows = [dict(frame=job['start_frame']+i,ok=False,reason='未追踪') for i in range(len(frames))]
    rows[ref] = dict(frame=job['reference_frame'],ok=True,H=np.eye(3).tolist(),inliers=len(keys),error=0.)
    done=1
    for sequence in [range(ref+1,len(frames)),range(ref-1,-1,-1)]:
        previous=np.eye(3)
        for i in sequence:
            if cancelled(): raise Cancelled()
            row=dict(frame=job['start_frame']+i,ok=False)
            rows[i]=row
            try:
                old=warp(quad,previous)
                lo=np.maximum(0,np.floor(old.min(axis=0)-radius)).astype(int)
                hi=np.minimum([frames.shape[2],frames.shape[1]],np.ceil(old.max(axis=0)+radius)).astype(int)
                search=np.zeros_like(mask);search[lo[1]:hi[1],lo[0]:hi[0]]=255
                target_keys,target_desc=sift.detectAndCompute(frames[i],search)
                if target_desc is None or len(target_desc)<16: raise ValueError('目标区域特征不足或被遮挡')
                pairs=matcher.knnMatch(descriptors,target_desc,k=2)
                good=[pair[0] for pair in pairs if len(pair)==2 and pair[0].distance<.7*pair[1].distance]
                # Repeated characters must not all vote for the same target keypoint.
                unique={}
                for m in sorted(good,key=lambda m:m.distance): unique.setdefault(m.trainIdx,m)
                good=list(unique.values())
                if len(good)<12: raise ValueError('可靠匹配点不足，可能发生遮挡或形变')
                p=np.float32([keys[m.queryIdx].pt for m in good]);q=np.float32([target_keys[m.trainIdx].pt for m in good])
                vp, vq = warp(p, inverse), warp(q, inverse)
                video_H,valid=estimate(vp,vq,job.get('mode','perspective'),1.5/scale[0,0])
                H = scale @ video_H @ inverse if video_H is not None else None
                if H is None: raise ValueError('无法求得平面变换')
                valid=valid.ravel().astype(bool);count=int(valid.sum())
                if count<12 or count/len(good)<.45: raise ValueError('匹配点不符合单一平面运动')
                coverage=cv2.contourArea(cv2.convexHull(p[valid]))/max(1,cv2.contourArea(quad.astype(np.float32)))
                if coverage<.025: raise ValueError('匹配点过于集中，透视估计不稳定')
                error=float(np.quantile(np.linalg.norm(warp(p[valid],H)-q[valid],axis=1),.95)/scale[0,0])
                if error>3: raise ValueError('平面重投影误差过大')
                projected=warp(quad,H)
                den=np.c_[quad,np.ones(4)]@H[2]
                area=cv2.contourArea(projected.astype(np.float32),oriented=True)/cv2.contourArea(quad.astype(np.float32),oriented=True)
                if np.min(den)*np.max(den)<=0 or not cv2.isContourConvex(projected.astype(np.float32)) or not .15<area<6:
                    raise ValueError('平面变换折叠或缩放异常')
                if np.max(np.linalg.norm(projected-old,axis=1))>radius*1.6: raise ValueError('平面跳动超过搜索范围')
                video_H=inverse@H@scale;video_H/=video_H[2,2]
                row.update(ok=True,H=video_H.tolist(),inliers=count,matches=len(good),error=error,coverage=float(coverage))
                previous=H
            except ValueError as exc:
                row['reason']=str(exc)
                break
            done+=1;progress(LABELS[MODES.index(job.get('mode','perspective'))]+'追踪',done/len(frames))
    return rows
