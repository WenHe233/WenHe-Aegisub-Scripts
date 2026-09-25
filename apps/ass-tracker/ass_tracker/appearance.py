"""Scale length-valued overrides, preserving inheritance and tag order."""
import re
import numpy as np
from .subtitles import NUMBER, BLOCK, fmt

LENGTH = re.compile(r'\\(xbord|ybord|bord|xshad|yshad|shad|blur)('+NUMBER+r')?(?=\\|$)')


def local_scale(H, point):
    H = np.asarray(H, float)
    q = H @ np.r_[point, 1.]
    if abs(q[2]) < 1e-8:
        raise ValueError('外观缩放位置接近透视奇点。')
    J = (H[:2,:2]*q[2] - np.outer(q[:2], H[2,:2]))/q[2]**2
    scale = np.sqrt(abs(np.linalg.det(J)))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError('外观缩放比例无效。')
    return scale


def transform(text, style, scale):
    if abs(scale-1) < 1e-9:
        return text
    defaults = dict(bord=float(style.get('outline', 0)), shad=float(style.get('shadow', 0)), blur=0.)
    defaults.update(xbord=defaults['bord'], ybord=defaults['bord'], xshad=defaults['shad'], yshad=defaults['shad'])
    def block(m):
        def tag(t):
            value = float(t[2]) if t[2] is not None else defaults[t[1]]
            if t[1] not in ('xshad','yshad') and value < 0:
                raise ValueError('描边、模糊和 shad 不能为负数。')
            return '\\'+t[1]+fmt(value*scale)
        return '{'+LENGTH.sub(tag,m[1])+'}'
    text = BLOCK.sub(block,text)
    prefix = ''.join('\\'+k+fmt(defaults[k]*scale) for k in ('bord','shad') if defaults[k])
    return '{'+prefix+text[1:] if text.startswith('{') else '{'+prefix+'}'+text
