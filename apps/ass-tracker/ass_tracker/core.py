"""Fixed-reference template tracking with conservative failure handling."""
from pathlib import Path
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import sys
import cv2
import numpy as np
from .subtitles import validate_text, generate
from .motion import MODES

SCHEMA = "ass-tracker-job-v1"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Cancelled(Exception):
    pass


def executable(name):
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).parent / 'ffmpeg' / (name + '.exe')
        if bundled.is_file():
            return str(bundled)
    found = shutil.which(name)
    if not found:
        raise ValueError(f"找不到 {name}。请安装 FFmpeg，并把 bin 目录加入 PATH。")
    return found


def probe(video):
    p = subprocess.run([executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,nb_frames,avg_frame_rate", "-of", "json", str(video)],
                       capture_output=True, creationflags=CREATE_FLAGS, check=True)
    streams = json.loads(p.stdout).get("streams", [])
    if not streams:
        raise ValueError("文件中没有可读取的视频轨道。")
    return streams[0]


def validate_job(job):
    from . import __version__
    if job.get('tool_version',__version__) != __version__:
        raise ValueError('宏与追踪程序版本不一致，请安装配套版本。')
    if job.get('mode', 'translation') not in MODES:
        raise ValueError('不支持的追踪模式。')
    if job.get("schema") != SCHEMA:
        raise ValueError("不是 ASS Tracker v1 任务文件。")
    for field in ["start_frame", "end_frame", "reference_frame", "video_width", "video_height", "script_width", "script_height"]:
        if type(job.get(field)) is not int:
            raise ValueError(f"任务字段 {field} 必须为整数。")
    start, end, ref = (job[k] for k in ["start_frame", "end_frame", "reference_frame"])
    if not 0 <= start <= ref < end or end - start > 2400:
        raise ValueError("帧范围无效，或超过第一版的 2400 帧上限。请按镜头分段。")
    if min(job[k] for k in ["video_width", "video_height", "script_width", "script_height"]) <= 0:
        raise ValueError("视频或脚本分辨率无效。")
    if 'roi' in job or 'roi_polygon' in job:
        from .regions import parse_region
        parse_region(job).apply(job)
    if job.get('mode', 'translation') != 'translation':
        aspect = job['script_width'] * job['video_height'] / (job['script_height'] * job['video_width'])
        if abs(aspect-1) > .001:
            raise ValueError('非平移模式需要脚本与视频宽高比一致，请先在 Aegisub 重采样脚本分辨率。')
    boundaries = job.get("boundaries_ms", [])
    if len(boundaries) != end - start + 1 or any(type(x) is not int for x in boundaries):
        raise ValueError("缺少来自 Aegisub 的逐帧时间边界。")
    if any(a >= b for a, b in zip(boundaries, boundaries[1:])):
        raise ValueError("时间边界必须严格递增；过高帧率无法用 ASS 厘秒精度表示。")
    if not job.get("lines") or not isinstance(job.get("job_id"), str):
        raise ValueError("任务没有字幕行或标识。")
    indices = set()
    for line in job["lines"]:
        if type(line.get("index")) is not int or line["index"] in indices:
            raise ValueError("字幕行号无效或重复。")
        indices.add(line["index"])
        if not start <= line["start_frame"] < line["end_frame"] <= end:
            raise ValueError("字幕行帧范围不在任务范围内。")
        if not line["start_frame"] <= ref < line["end_frame"]:
            raise ValueError("所有行必须在参考帧可见。不同出现时间的消息请分开导出。")
        if line["start_time"] >= line["end_time"]:
            raise ValueError("字幕行持续时间无效。")
        try:
            validate_text(line["text"])
            styles = line.get('reset_styles')
            if styles is not None and not (isinstance(styles, dict) and all(isinstance(s, dict) for s in styles.values())):
                raise ValueError('reset_styles 必须是以样式名为键的对象。')
            if job.get('mode', 'translation') != 'translation':
                from .perspective import validate_line
                validate_line(line)
            if job.get('move_clips', True):
                from .paths import transform_clip
                from .subtitles import CLIP, BLOCK
                for block in BLOCK.finditer(line['text']):
                    for match in CLIP.finditer(block[1]):
                        transform_clip(match[1], match[2], np.eye(3))
        except ValueError as exc:
            raise ValueError(f"第 {line['index']} 行：{exc}") from exc
    if not Path(job.get("video", "")).is_file():
        raise ValueError("任务中的视频路径不存在。请在追踪窗口重新选择原视频。")
    return job


def read_job(path):
    return validate_job(json.loads(Path(path).read_text(encoding="utf-8-sig")))


@contextmanager
def decoded_frames(job, max_width=960, progress=lambda *_: None, cancelled=lambda: False):
    info = probe(job["video"])
    if (info["width"], info["height"]) != (job["video_width"], job["video_height"]):
        raise ValueError("视频分辨率与 Aegisub 导出的任务不同。请确认使用了同一个视频。")
    width = min(job["video_width"], int(max_width))
    height = max(1, round(job["video_height"] * width / job["video_width"]))
    count = job["end_frame"] - job["start_frame"]
    size = width * height
    if size * count > 768 * 1024 ** 2:
        raise ValueError("这一段缓存超过 768 MB。请缩短字幕范围或降低分析宽度。")
    with tempfile.TemporaryDirectory(prefix="ass-tracker-") as tmp:
        stderr = open(Path(tmp) / "ffmpeg.log", "w+b")
        # Decode by frame index, not guessed FPS or input -ss. VFR and duplicate
        # animation frames retain exactly the indices exported by Aegisub.
        vf = f"trim=start_frame={job['start_frame']}:end_frame={job['end_frame']},scale={width}:{height},format=gray"
        cmd = [executable("ffmpeg"), "-v", "error", "-nostdin", "-noautorotate", "-i", job["video"],
               "-map", "0:v:0", "-an", "-sn", "-vf", vf, "-vsync", "0", "-frames:v", str(count),
               "-pix_fmt", "gray", "-f", "rawvideo", "-"]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr, creationflags=CREATE_FLAGS)
        # Cancel also interrupts a blocked decode/read (e.g. a long input prefix).
        import threading
        stop = threading.Event()
        def watchdog():
            while not stop.wait(.1):
                if cancelled():
                    process.terminate()
                    return
        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        array = None
        try:
            cache = Path(tmp) / "frames.gray"
            with cache.open("wb") as out:
                for i in range(count):
                    if cancelled():
                        raise Cancelled()
                    parts = bytearray()
                    while len(parts) < size:
                        block = process.stdout.read(size - len(parts))
                        if not block:
                            if cancelled():
                                raise Cancelled()
                            raise ValueError(f"解码提前结束：需要 {count} 帧，只读到 {i} 帧。")
                        parts.extend(block)
                    out.write(parts)
                    progress("解码", (i + 1) / count)
            if process.wait() != 0:
                stderr.seek(0)
                raise ValueError(stderr.read().decode("utf-8", "replace")[-1200:])
            array = np.memmap(cache, dtype=np.uint8, mode="r", shape=(count, height, width))
            yield array
        finally:
            stop.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            watcher.join(timeout=1)
            process.stdout.close()
            stderr.close()
            if array is not None:
                array._mmap.close()


def read_reference(job, max_width=1100, cancelled=lambda: False):
    minimal = dict(job, start_frame=job["reference_frame"], end_frame=job["reference_frame"] + 1)
    with decoded_frames(minimal, max_width, cancelled=cancelled) as frames:
        return frames[0].copy()


def match_frame(gray, template, previous, radius, threshold=.85, gap_threshold=.025, mask=None):
    h, w = template.shape
    px, py = previous
    left, top = max(0, math.floor(px - radius)), max(0, math.floor(py - radius))
    right = min(gray.shape[1], math.ceil(px + w + radius))
    bottom = min(gray.shape[0], math.ceil(py + h + radius))
    if right - left < w or bottom - top < h:
        return dict(ok=False, reason="目标离开画面或搜索范围不足")
    scores = cv2.matchTemplate(gray[top:bottom, left:right], template, cv2.TM_CCOEFF_NORMED, mask=mask)
    if not np.isfinite(scores).any():
        return dict(ok=False, reason="搜索范围内没有有效的匹配分数（区域可能为纯色或被遮挡）")
    scores = np.where(np.isfinite(scores),np.clip(scores,-1,1),-np.inf)
    _, score, _, loc = cv2.minMaxLoc(scores)
    if not np.isfinite(score):
        return dict(ok=False, reason="匹配分数异常")
    x, y = loc
    other = scores.copy()
    exclude = max(3, min(w, h) // 8)
    other[max(0, y-exclude):y+exclude+1, max(0, x-exclude):x+exclude+1] = -1
    finite_other=other[np.isfinite(other)]
    second = float(finite_other.max()) if finite_other.size else -1
    gap = score - second
    if score < threshold:
        return dict(ok=False, reason="匹配质量下降（遮挡、形变或位移过大）", score=float(score), gap=gap)
    if gap < gap_threshold:
        return dict(ok=False, reason="存在相似图案，无法可靠区分目标", score=float(score), gap=gap)
    if ((x == 0 and left > 0) or (y == 0 and top > 0) or
        (x == scores.shape[1]-1 and right < gray.shape[1]) or
        (y == scores.shape[0]-1 and bottom < gray.shape[0])):
        return dict(ok=False, reason="匹配落在搜索边界，请扩大搜索半径", score=float(score), gap=gap)
    def refine(a, b, c):
        if not np.isfinite([a,b,c]).all(): return 0.
        den = a - 2*b + c
        return float(np.clip(.5*(a-c)/den, -.5, .5)) if abs(den) > 1e-6 else 0.
    fx = refine(*scores[y, x-1:x+2]) if 0 < x < scores.shape[1]-1 else 0.
    fy = refine(*scores[y-1:y+2, x]) if 0 < y < scores.shape[0]-1 else 0.
    return dict(ok=True, x=left+x+fx, y=top+y+fy, score=float(score), gap=float(gap))


def track_frames(frames, job, progress=lambda *_: None, cancelled=lambda: False):
    options = job.get("options", {})
    scale_x = frames.shape[2] / job["video_width"]
    scale_y = frames.shape[1] / job["video_height"]
    from .regions import parse_region
    region = parse_region(job)
    x, y, w, h = region.bounds
    mask = None
    if region.polygon:
        full_mask = region.mask(frames.shape[1:],(job['video_width'],job['video_height']))
        yy,xx = np.nonzero(full_mask)
        if not xx.size: raise ValueError('多边形在分析图中为空，请扩大区域或提高分析宽度。')
        x,y,w,h = int(xx.min()),int(yy.min()),int(xx.max()-xx.min()+1),int(yy.max()-yy.min()+1)
        mask=full_mask[y:y+h,x:x+w].copy()
        if np.count_nonzero(mask)<144: raise ValueError('多边形在分析图中有效面积太小，请扩大区域或提高分析宽度。')
    else:
        x, y, w, h = round(x*scale_x), round(y*scale_y), round(w*scale_x), round(h*scale_y)
    if min(w, h) < 12:
        raise ValueError("追踪区域太小；分析图中宽高至少各 12 像素。")
    ref = job["reference_frame"] - job["start_frame"]
    template = frames[ref, y:y+h, x:x+w].copy()
    pixels = template[mask>0] if mask is not None else template
    edges = cv2.Laplacian(template, cv2.CV_32F)
    if mask is not None:
        interior=cv2.erode(mask,np.ones((3,3),np.uint8),borderType=cv2.BORDER_CONSTANT,borderValue=0)>0
        edges=edges[interior]
    if not edges.size or pixels.std() < 8 or edges.var() < 3:
        raise ValueError("所选区域细节太少。请包含文字、角点或清楚的边缘。")
    threshold = float(options.get("threshold", .85))
    gap = float(options.get("gap", .025))
    radius = float(options.get("search_radius", 220)) * scale_x
    quantum = float(options.get("quantum", .5))
    if not .5 <= threshold <= 1 or not 0 <= gap <= .5 or not 4 <= radius <= 2000 or not .05 <= quantum <= 4:
        raise ValueError("追踪参数超出允许范围。")
    rows = [dict(frame=job["start_frame"]+i, ok=False, reason="未追踪") for i in range(len(frames))]
    rows[ref] = dict(frame=job["reference_frame"], ok=True, dx=0., dy=0., score=1., gap=1.)
    done = 1
    for sequence in [range(ref+1, len(frames)), range(ref-1, -1, -1)]:
        previous = (x, y)
        previous_shift = (0., 0.)
        for i in sequence:
            if cancelled():
                raise Cancelled()
            result = match_frame(frames[i], template, previous, radius, threshold, gap, mask)
            result["frame"] = job["start_frame"] + i
            rows[i] = result
            if not result["ok"]:
                break  # Never extrapolate a lost target.
            previous = (result.pop("x"), result.pop("y"))
            dx, dy = (previous[0]-x)/scale_x, (previous[1]-y)/scale_y
            # Quantize tiny codec/subpixel noise; do not smooth across jumps.
            dx, dy = round(dx/quantum)*quantum, round(dy/quantum)*quantum
            if abs(dx-previous_shift[0]) < quantum and abs(dy-previous_shift[1]) < quantum:
                dx, dy = previous_shift
            result.update(dx=dx, dy=dy)
            previous_shift = (dx, dy)
            done += 1
            progress("追踪", done / len(frames))
    for row in rows:
        if row['ok']:
            row['H'] = [[1., 0., row['dx']], [0., 1., row['dy']], [0., 0., 1.]]
    return rows


def run_job(job, progress=lambda *_: None, cancelled=lambda: False):
    job = copy.deepcopy(validate_job(job))
    with decoded_frames(job, job.get("options", {}).get("max_width", 960), progress, cancelled) as frames:
        if job.get('mode', 'translation') != 'translation':
            from .planar import track_planar
            rows = track_planar(frames, job, progress, cancelled)
        else:
            rows = track_frames(frames, job, progress, cancelled)
    complete = all(r["ok"] for r in rows)
    return dict(schema="ass-tracker-result-v1", job_id=job["job_id"], status="complete" if complete else "failed",
                job=job, track=rows, generated=generate(job, rows) if complete else [],
                failures=[r for r in rows if not r["ok"] and r.get("reason") != "未追踪"])


def save_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)
