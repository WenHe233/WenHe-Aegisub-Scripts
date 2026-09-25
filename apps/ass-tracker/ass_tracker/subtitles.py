"""Strict, translation-only ASS adaptation. No implicit tag deletion."""
import re

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
PAIR = re.compile(r"\\(pos|org)\(\s*(" + NUMBER + r")\s*,\s*(" + NUMBER + r")\s*\)")
CLIP = re.compile(r"\\(i?clip)\(([^()]*)\)")
BLOCK = re.compile(r"\{([^}]*)\}")


def fmt(n):
    return f"{n:.4f}".rstrip("0").rstrip(".") if abs(n) >= .00005 else "0"


def validate_text(text):
    blocks = "".join(m[1] for m in BLOCK.finditer(text))
    # Time-dependent effects would restart when splitting events.
    if re.search(r"\\(?:move|t|fad|fade)\s*\(|\\(?:k|K|kf|ko|kt)\d|\\r", blocks):
        raise ValueError("第一版不支持 move、t、淡入淡出、卡拉 OK 或样式重置。请先展开/移除这些效果。")
    if len(re.findall(r"\\pos\(", blocks)) != 1:
        raise ValueError("每行必须有且只有一个显式 \\pos(x,y)。请先在参考帧做好位置。")
    if len([m for m in PAIR.finditer(blocks) if m[1] == "pos"]) != 1:
        raise ValueError("无法解析 pos 坐标。")
    if blocks.count("\\org(") > 1:
        raise ValueError("一行不能有多个 org 标签。")
    if blocks.count("\\clip(") + blocks.count("\\iclip(") > 1:
        raise ValueError("第一版每行最多支持一个 clip/iclip。")


def shift_clip(kind, value, dx, dy):
    from .paths import transform_clip
    return transform_clip(kind, value, [[1.,0.,dx],[0.,1.,dy],[0.,0.,1.]])


def translate(text, dx, dy, move_clips=True):
    validate_text(text)
    if dx == 0 and dy == 0:
        return text
    def block(m):
        value = PAIR.sub(lambda p: "\\" + p[1] + "(" + fmt(float(p[2]) + dx) + "," + fmt(float(p[3]) + dy) + ")", m[1])
        if move_clips:
            value = CLIP.sub(lambda p: shift_clip(p[1], p[2], dx, dy), value)
        return "{" + value + "}"
    return BLOCK.sub(block, text)


def generate(job, track):
    if any(not row["ok"] for row in track):
        raise ValueError("存在失锁帧，禁止生成可导入的字幕结果。")
    sx = job["script_width"] / job["video_width"]
    sy = job["script_height"] / job["video_height"]
    output = []
    for line in job["lines"]:
        current = None
        for frame in range(line["start_frame"], line["end_frame"]):
            i = frame - job["start_frame"]
            row = track[i]
            start = max(line["start_time"], job["boundaries_ms"][i])
            end = min(line["end_time"], job["boundaries_ms"][i + 1])
            if frame == line["start_frame"]:
                start = line["start_time"]
            if frame == line["end_frame"] - 1:
                end = line["end_time"]
            if start >= end:
                continue
            if 'H' in row and job.get('mode', 'translation') != 'translation':
                import numpy as np
                from .perspective import transform
                scale = np.diag([sx, sy, 1.])
                value = transform(line, scale @ np.asarray(row['H']) @ np.linalg.inv(scale),
                                  job.get('move_clips', True), job.get('scale_appearance', False), (1/sx,1/sy))
            else:
                value = translate(line["text"], row["dx"] * sx, row["dy"] * sy, job.get("move_clips", True))
            if current and current["text"] == value and current["end_time"] == start:
                current["end_time"] = end
            else:
                current = dict(source_index=line["index"], start_time=start, end_time=end, text=value)
                output.append(current)
    return output
