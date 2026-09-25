"""Nested motion models. Constraints are enforced during estimation."""
import math
import cv2
import numpy as np

MODES = ('translation', 'translation_scale', 'similarity', 'affine', 'perspective')
LABELS = ('平移', '缩放', '旋转', '斜切', '透视')
HINTS = ('仅水平、垂直移动，适合滚动消息。', '平移与等比缩放，适合无旋转的推拉。',
         '平移、等比缩放和旋转，适合常见屏字。', '包含非等比缩放与斜切，适合平行四边形形变。',
         '完整平面透视，适合手机屏幕转动和梯形变化。')


def toggled(mode, index, enabled):
    """Return the highest selected degree; translation is mandatory."""
    level = MODES.index(mode)
    return MODES[max(level, index) if enabled else min(level, max(0, index - 1))]


def _scale_fit(p, q):
    pc, qc = p.mean(axis=0), q.mean(axis=0)
    u, v = p - pc, q - qc
    den = np.sum(u * u)
    if den < 1e-8:
        return None
    scale = np.sum(u * v) / den
    if not np.isfinite(scale) or scale <= 0:
        return None
    H = np.diag([scale, scale, 1.])
    H[:2, 2] = qc - scale * pc
    return H


def estimate(p, q, mode, threshold=3.):
    p, q = np.asarray(p, float), np.asarray(q, float)
    if mode == 'translation_scale':
        # Minimal two-point hypotheses, followed by constrained least squares.
        rng = np.random.default_rng(0)
        best, best_score = None, (-1, -math.inf)
        limit, iteration = 3000, 0
        while iteration < limit:
            iteration += 1
            ids = rng.choice(len(p), 2, replace=False)
            H = _scale_fit(p[ids], q[ids])
            if H is None:
                continue
            error = np.linalg.norm(p * H[0, 0] + H[:2, 2] - q, axis=1)
            mask = error <= threshold
            score = (int(mask.sum()), -float(np.minimum(error, threshold).sum()))
            if score > best_score:
                best, best_score = mask, score
                ratio = mask.mean()
                if ratio == 1:
                    limit = iteration
                elif ratio > 0:
                    limit = min(limit, max(iteration, math.ceil(math.log(.001) / math.log(1-ratio**2))))
        if best is None or best.sum() < 2:
            return None, None
        H = _scale_fit(p[best], q[best])
        if H is None:
            return None, None
        mask = np.linalg.norm(p * H[0, 0] + H[:2, 2] - q, axis=1) <= threshold
        return H, mask
    if mode == 'perspective':
        return cv2.findHomography(p, q, cv2.RANSAC, threshold, maxIters=3000, confidence=.999)
    solver = {'similarity': cv2.estimateAffinePartial2D, 'affine': cv2.estimateAffine2D}.get(mode)
    if solver is None:
        raise ValueError('不支持的运动模型。')
    A, mask = solver(p, q, method=cv2.RANSAC, ransacReprojThreshold=threshold,
                     maxIters=3000, confidence=.999, refineIters=20)
    return (np.vstack([A, [0., 0., 1.]]) if A is not None else None), mask
