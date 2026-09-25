"""ASS drawing grammar and bounded projective curve approximation.

Spline semantics follow ASS/libass: m closes the preceding contour, n does
not; c extends a B-spline with its first three controls (not a generic close).
"""
import re
import numpy as np
from .planar import warp

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
TOKEN = re.compile(r'[mnlbspc]|' + NUMBER)
LIMIT = 8192


def fmt(n):
    return f'{n:.4f}'.rstrip('0').rstrip('.') if abs(n) >= .00005 else '0'


def parse(path):
    if TOKEN.sub('', path).strip():
        raise ValueError('绘图包含无法解析的字符。')
    tokens = TOKEN.findall(path)
    commands = []
    i = 0
    while i < len(tokens):
        command = tokens[i]; i += 1
        if command not in 'mnlbspc' or len(command) != 1:
            raise ValueError('绘图缺少命令。')
        numbers = []
        while i < len(tokens) and re.fullmatch(NUMBER, tokens[i]):
            numbers.append(float(tokens[i])); i += 1
        if len(numbers) % 2:
            raise ValueError('绘图坐标必须成对。')
        points = np.array(numbers).reshape(-1, 2)
        count = len(points)
        if (command == 'c' and count) or (command != 'c' and not count):
            raise ValueError('绘图命令参数数量错误。')
        if command == 'b' and count % 3 or command == 's' and count < 3:
            raise ValueError('曲线控制点数量错误。')
        commands.append((command, points))
    if not commands or commands[0][0] != 'm':
        raise ValueError('绘图必须以 m 开始。')
    if sum(len(p) for _, p in commands) > LIMIT:
        raise ValueError('绘图超过 8192 个控制点，请简化轮廓。')
    # Also validate spline continuation semantics before any transformation.
    list(segments(commands))
    return commands


def segments(commands):
    """Canonical m/n/l/b segments; retain the non-closing move semantics."""
    pen = np.zeros(2)
    endpoint = None
    spline = None
    started = False
    def spline_curves(points):
        for j in range(len(points)-3):
            a,b,c,d = np.asarray(points[j:j+4])
            yield np.array([(a+4*b+c)/6, (2*b+c)/3, (b+2*c)/3, (b+4*c+d)/6])
    i = 0
    while i < len(commands):
        cmd, pts = commands[i]; i += 1
        if cmd in ('m', 'n'):
            for p in pts:
                pen = p
                yield cmd, np.array([p])
                if cmd == 'm': started = False
            spline = None
        elif cmd == 'l':
            yield 'l', pts
            pen = pts[-1]; endpoint=pen; started = True; spline = None
        elif cmd == 'b':
            for j in range(0, len(pts), 3):
                yield 'b', np.vstack([endpoint if started else pen, pts[j:j+3]])
                pen = pts[j+2]; endpoint=pen; started = True
            spline = None
        elif cmd == 's':
            spline = [pen, *pts]
            while i < len(commands) and commands[i][0] == 'p':
                spline.extend(commands[i][1]); i += 1
            if i < len(commands) and commands[i][0] == 'c':
                spline.extend(spline[:3]); i += 1
            curves = list(spline_curves(spline))
            if not started:
                yield 'n', curves[0][:1]
            for curve in curves:
                if started: curve[0]=endpoint
                yield 'b', curve
                endpoint=curve[-1]; started=True
            pen = np.asarray(spline[-1])
        elif cmd == 'p':
            raise ValueError('p 必须用于延续 s 样条。')
        # c outside a spline is ignored by ASS renderers.


def _flatten(controls, H, tolerance, pixel_scale, depth=0):
    """Positive rational Bezier convex hull gives a screen-space error bound."""
    homogeneous = np.c_[controls, np.ones(4)] @ H.T
    weights = homogeneous[:, 2]
    if min(weights) * max(weights) <= 0 or min(abs(weights)) < 1e-8:
        raise ValueError('曲线跨越透视奇点，不能应用。')
    points = homogeneous[:, :2] / weights[:, None]
    q = points * pixel_scale
    chord = q[-1] - q[0]
    den = np.dot(chord, chord)
    t = np.clip((q-q[0]) @ chord / den, 0, 1) if den > 1e-12 else np.zeros(4)
    error = np.max(np.linalg.norm(q - (q[0] + t[:, None]*chord), axis=1))
    if error <= tolerance:
        return [points[-1]]
    if depth >= 20:
        raise ValueError('曲线细分无法达到 0.25 像素精度。')
    a = (controls[:-1]+controls[1:])/2
    b = (a[:-1]+a[1:])/2
    c = (b[0]+b[1])/2
    left = _flatten(np.array([controls[0], a[0], b[0], c]), H, tolerance, pixel_scale, depth+1)
    right = _flatten(np.array([c, b[1], a[2], controls[-1]]), H, tolerance, pixel_scale, depth+1)
    if len(left)+len(right) > LIMIT:
        raise ValueError('透视曲线超过 8192 段，请简化轮廓。')
    return left+right


def transform_path(path, H, scale=1, pixel_scale=(1., 1.)):
    commands = parse(path)
    H = np.asarray(H, float)
    # A contour crossing the camera plane cannot be represented by a finite
    # ASS path. A shared sign for all controls also covers straight/closing edges.
    controls=np.concatenate([p for _,p in commands if len(p)])/scale
    denominators=np.c_[controls,np.ones(len(controls))] @ H[2]
    if min(denominators)*max(denominators)<=0 or min(abs(denominators))<1e-8:
        raise ValueError('轮廓跨越透视奇点，不能应用。')
    if np.allclose(H[2, :2], 0, atol=1e-12):
        # Affine maps preserve all polynomial curves, including B-splines.
        return ' '.join(cmd + (' ' + ' '.join(fmt(n) for n in (warp(p/scale,H)*scale).ravel()) if len(p) else '')
                        for cmd,p in commands)
    output = []
    count = 0
    for cmd, pts in segments(commands):
        if cmd == 'b':
            points = np.asarray(_flatten(pts/scale, H, .24, np.asarray(pixel_scale)))
            cmd = 'l'
        else:
            points = warp(pts/scale, H)
        count += len(points)
        if count > LIMIT:
            raise ValueError('透视轮廓超过 8192 段，请简化轮廓。')
        output.append(cmd + ' ' + ' '.join(fmt(n) for n in (points*scale).ravel()))
    return ' '.join(output)


def transform_clip(kind, value, H, pixel_scale=(1., 1.)):
    parts = [p.strip() for p in value.split(',')]
    if len(parts) == 4 and all(re.fullmatch(NUMBER, p) for p in parts):
        x,y,u,v = map(float,parts)
        if u < x or v < y:
            raise ValueError('矩形裁切的右下角必须不小于左上角。')
        original=np.array([[x,y],[u,y],[u,v],[x,v]])
        denominators=np.c_[original,np.ones(4)] @ np.asarray(H)[2]
        if min(denominators)*max(denominators)<=0 or min(abs(denominators))<1e-8:
            raise ValueError('裁切跨越透视奇点，不能应用。')
        points = warp(original, H)
        # ASS rectangular clips have integer coordinates.
        if (np.allclose(points, np.round(points), atol=1e-7) and
            abs(points[0,1]-points[1,1]) < 1e-8 and abs(points[1,0]-points[2,0]) < 1e-8 and
            abs(points[2,1]-points[3,1]) < 1e-8 and abs(points[3,0]-points[0,0]) < 1e-8):
            body = ','.join(str(int(round(n))) for n in [*points.min(axis=0), *points.max(axis=0)])
        else:
            body = 'm ' + ' l '.join(' '.join(fmt(n) for n in p) for p in points) + ' c'
        return '\\'+kind+'('+body+')'
    level = 1
    if len(parts) == 2 and parts[0].isdigit():
        level = int(parts[0])
    elif len(parts) != 1:
        raise ValueError('裁切格式无效。')
    if not 1 <= level <= 10:
        raise ValueError('矢量裁切坐标倍率需在 1～10。')
    body = transform_path(parts[-1], H, 2**(level-1), pixel_scale)
    return '\\'+kind+'('+ (str(level)+',' if len(parts)==2 else '')+body+')'
