"""Fit a tracked plane to ASS's projection; keep editable text and polygon cards."""
import re
import numpy as np
from scipy.optimize import least_squares
from .subtitles import NUMBER, BLOCK, PAIR, CLIP, RESET, fmt, validate_text
from .planar import warp
from .paths import parse, transform_path, transform_clip

NAMES = ['fscx','fscy','frz','frx','fry','fax']
# Geometry and measured font must stay the same along a line.
UNIFORM = NAMES+['fay','fn','fs','fsp','b','i']
CHUNKS = re.compile(r'(\{[^}]*\})')
TAG = re.compile(r'\\([^\\]*)')
VALUE = re.compile(r'(fscx|fscy|frx|fry|frz|fr|fax|fay|fsp|fs|b|i)\s*('+NUMBER+r')?\s*')
ALIGN = re.compile(r'\\(an|a)\s*('+NUMBER+r')?\s*(?=\\|$)')
# Fitted geometry, including the \fr alias, \fsc and argument-less resets.
FITTED = r'\\(?:fscx|fscy|fsc|frx|fry|frz|fr|fax)(?:\s*'+NUMBER+r')?\s*(?=\\|$)'
DRAWN = r'\\(?:fscx|fscy|fsc|frx|fry|frz|fr|fax|fay|an|a|pbo)(?:\s*'+NUMBER+r')?\s*(?=\\|$)'
IDENTITY = '\\fscx100\\fscy100\\frz0\\frx0\\fry0\\fax0\\fay0'


def number(tags, name, default):
    found=re.findall(r'\\'+name+'('+NUMBER+r')(?=\\|$)',tags)
    return float(found[-1]) if found else float(default)


def reset(style):
    """Tag state after \\r to this style (libass ass_reset_render_context)."""
    return dict(fscx=float(style.get('scale_x',100)),fscy=float(style.get('scale_y',100)),
                frz=float(style.get('angle',0)),frx=0.,fry=0.,fax=0.,fay=0.,
                fn=style.get('fontname'),fs=style.get('fontsize'),fsp=float(style.get('spacing',0)),
                b=bool(style.get('bold',False)),i=bool(style.get('italic',False)))


def apply(current,raw,base,styles):
    raw=raw.strip();value=VALUE.fullmatch(raw)
    if raw.startswith('r'):
        # An unknown style name falls back to the line style, like libass.
        name=raw[1:].strip()
        current.update(reset(styles[name]) if name in styles else base)
    elif raw.startswith('fn'):
        name=raw[2:].strip()
        current['fn']=base['fn'] if name in ('','0') else name
    elif raw=='fsc' or re.fullmatch('fsc'+NUMBER,raw):
        current.update(fscx=base['fscx'],fscy=base['fscy'])
    elif value:
        name='frz' if value[1]=='fr' else value[1]
        if value[2] is None:
            current[name]=base[name]  # Argument-less tags restore the line style.
        elif name=='fs' and value[2][0] in '+-':
            raise ValueError('透视模式不支持相对字号 \\fs+/-，请改为绝对字号。')
        elif name in ('b','i'):
            n=float(value[2]);current[name]=bool(n) if n in (0,1) else n
        else:
            current[name]=float(value[2])


def runs(line):
    """(text, tag state) for each text segment in order, following \\r resets."""
    style=line.get('style_data') or {}
    styles=line.get('reset_styles') or {}
    base=reset(style);current=dict(base);result=[]
    for k,chunk in enumerate(CHUNKS.split(line['text'])):
        if k%2:
            for raw in TAG.findall(chunk[1:-1]):apply(current,raw,base,styles)
        elif chunk:
            result.append((chunk,dict(current)))
    return result or [('',current)]


def alignment(text,default):
    """The first \\an or \\a wins; legacy \\a values have their own numbering."""
    for block in BLOCK.finditer(text):
        m=ALIGN.search(block[1])
        if m:
            value=int(float(m[2])) if m[2] else 0
            if m[1]=='an':
                return value if 1<=value<=9 else int(default)
            if not 1<=value<=11:
                return int(default)
            value=5 if value&3==0 else value  # VSFilter: \a4 and \a8 act like \a5
            return (value&3)+{0:0,4:6,8:3}[value&12]
    return int(default)


def state(line):
    text=line['text'];tags=''.join(m[1] for m in BLOCK.finditer(text))
    style=line.get('style_data', {})
    xy={m[1]:[float(m[2]),float(m[3])] for m in PAIR.finditer(tags)}
    segments=runs(line)
    first=next((s for t,s in segments if t.strip()),segments[0][1])
    values=np.array(xy['pos']+[first[k] for k in NAMES])
    return values,np.array(xy.get('org',xy['pos'])),alignment(text,style.get('align',2)),first['fay']


def validate_line(line):
    validate_text(line['text'])
    v,org,an,fay=state(line)
    text=line['text'];tags=''.join(m[1] for m in BLOCK.finditer(text))
    if not BLOCK.match(text):
        raise ValueError('透视模式请将排版标签放在行首。')
    if not np.isfinite(v).all() or min(v[2:4])<=0 or an not in range(1,10):
        raise ValueError('透视模式不支持负缩放、零缩放或无效对齐。')
    # Position, origin, alignment and baseline belong to the whole line.
    seen=False
    for k,chunk in enumerate(CHUNKS.split(text)):
        if k%2==0:
            seen=seen or bool(chunk.strip())
        elif seen and re.search(r'\\(?:(?:an|a|pbo)\s*(?:'+NUMBER+r')?\s*(?=\\|$)|pos\(|org\()',chunk[1:-1]):
            raise ValueError('透视模式不支持在行内切换位置、原点、对齐或基线偏移，请拆成独立行。')
    # Colors may change; geometry and font must not, including through \r.
    visible=[s for t,s in runs(line) if t.strip()]
    for k,s in enumerate(visible[1:],2):
        changed=[key for key in UNIFORM if s[key]!=visible[0][key]]
        if changed:
            raise ValueError(f'透视模式要求整行几何和字体一致：第 {k} 段文字的 {"、".join(changed)} 与第一段不同。'
                             '\\r 会恢复样式值，请在 \\r 后补回相同标签，或拆成独立行。')
    if fay and len(visible)>1:
        raise ValueError('fay 不能与行内标签分段同时使用（libass 会逐段重新斜切），请拆成独立行。')
    drawing=number(tags,'p',0)>0 or re.search(r'\\p[1-9]',tags)
    if drawing:
        drawing_data(line)
    else:
        if re.search(r'\\b(?:[2-9]|[1-9]\d)',tags):
            raise ValueError('透视模式暂不支持数字字重，请用字体名称选择字重，并使用 b0/b1。')
        if re.search(r'\\[Nn]',text):
            raise ValueError('透视模式请将每个换行拆成独立字幕行。')
        metrics=line.get('metrics')
        if not metrics or min(metrics.get('width',0),metrics.get('height',0))<=0:
            raise ValueError('缺少字体尺寸，请使用新版 Aegisub 宏重新导出。')
    return bool(drawing)


def validate_polygon(path):
    parse(path)


def polygon(path,H,scale=1):
    return transform_path(path,H,scale)


def clip(kind,value,H):
    return transform_clip(kind,value,H)


def rotation(angle,axis):
    c,s=np.cos(np.deg2rad(angle)),np.sin(np.deg2rad(angle))
    mat=np.eye(3);indices=[i for i in range(3) if i!=axis]
    mat[np.ix_(indices,indices)]=[[c,-s],[s,c]]
    return mat


def project(v,org,points,anchor,fay=0.):
    px,py,sx,sy,rz,rx,ry,fax=v
    # Both shears act on unscaled layout coordinates (libass divides by scale).
    q=np.asarray(points)@np.array([[1,fay],[fax,1]])-anchor
    q*=np.array([sx,sy])/100
    q+=np.array([px,py])-org
    q=np.c_[q,np.zeros(len(q))]@rotation(-rz,2).T@rotation(-rx,0).T@rotation(ry,1).T
    return q[:,:2]*312.5/(312.5+q[:,2:])+org


def drawing_data(line):
    chunks = re.split(r'(\{[^}]*\})',line['text'])
    level = 0
    bodies = []
    for i,chunk in enumerate(chunks):
        if chunk.startswith('{'):
            level = int(number(chunk[1:-1],'p',level))
        elif chunk.strip():
            if not 1 <= level <= 10:
                raise ValueError('仅支持单段纯绘图 p1～p10，请拆开混合文字／绘图。')
            bodies.append((i,chunk,level))
    if len(bodies) != 1:
        raise ValueError('仅支持单段纯绘图，请拆开多个绘图片段。')
    i,path,level = bodies[0]
    commands = parse(path)
    points = np.vstack([p for _,p in commands if len(p)]) / 2**(level-1)
    return chunks,i,path,level,points


def transform(line,H,move_clips=True,scale_appearance=False,pixel_scale=(1.,1.)):
    drawing=validate_line(line)
    text=line['text'];H=np.asarray(H,float)
    if np.max(np.abs(H-np.eye(3)))<1e-9: return text
    if drawing:
        chunks,i,path,level,points = drawing_data(line)
        v,org,an,fay = state(line)
        tags=''.join(m[1] for m in BLOCK.finditer(text))
        size = points.max(axis=0)-points.min(axis=0)
        pbo=number(tags,'pbo',0)/2**(level-1)
        offset=np.array([0.,max(pbo-size[1],0.)])
        size[1]=max(size[1]-pbo,0.)+max(pbo,0.)
        anchor = size * [((an-1)%3)/2, (2-(an-1)//3)/2]
        # Recover the planar projection as a homography from four samples.
        sample=np.array([[0.,0.],[100.,0.],[100.,100.],[0.,100.]])
        import cv2
        original=cv2.getPerspectiveTransform(sample.astype(np.float32),project(v,org,sample+offset,anchor,fay).astype(np.float32))
        combined=H@original
        chunks[i]=transform_path(path,combined,2**(level-1),pixel_scale)
        # Path is now in screen coordinates; clear only baked geometry, and
        # keep it cleared after \r, which restores the style's values.
        for j,chunk in enumerate(chunks):
            if chunk.startswith('{'):
                head=re.sub(DRAWN,'',PAIR.sub('',chunk[1:-1]))
                chunks[j]='{'+RESET.sub(lambda r:r[0]+IDENTITY,head)+'}'
        chunks[1]='{\\an7\\pos(0,0)'+IDENTITY+'\\pbo0'+chunks[1][1:]
        text=''.join(chunks)
    else:
        v,org,an,fay=state(line)
        width,height=line['metrics']['width'],line['metrics']['height']
        anchor=np.array([((an-1)%3)*width/2, (2-(an-1)//3)*height/2])
        points=np.array([[x,y] for x in np.linspace(0,width,5) for y in np.linspace(0,height,3)])
        target=warp(project(v,org,points,anchor,fay),H)
        neworg=warp(org[None],H)[0]
        guess=v.copy();guess[:2]=warp(v[:2][None],H)[0]
        # fay stays as written; the eight other parameters span the homography.
        fit=least_squares(lambda a:(project(a,neworg,points,anchor,fay)-target).ravel(),guess,
                          x_scale='jac',max_nfev=600,ftol=1e-10,xtol=1e-10,gtol=1e-10)
        error=float(np.max(np.linalg.norm(project(fit.x,neworg,points,anchor,fay)-target,axis=1)))
        if not fit.success or not np.isfinite(fit.x).all() or error>.08 or min(fit.x[2:4])<=0:
            raise ValueError(f'ASS 透视参数拟合失败（误差 {error:.3f}），请缩短镜头或换参考帧。')
        # Fitted geometry replaces the old values in every block and follows
        # each later \r, which would otherwise restore the style's values.
        geometry=''.join('\\'+name+fmt(value) for name,value in zip(NAMES,fit.x[2:]))
        chunks=CHUNKS.split(text)
        for j in range(1,len(chunks),2):
            head=re.sub(FITTED,'',re.sub(r'\\q[0-3]','',PAIR.sub('',chunks[j][1:-1])))
            chunks[j]='{'+(RESET.sub(lambda r:r[0]+geometry,head) if j>1 else head)+'}'
        # Avoid reflow when the fitted horizontal scale changes.
        position='\\pos('+','.join(map(fmt,fit.x[:2]))+')\\org('+','.join(map(fmt,neworg))+')'
        chunks[1]=chunks[1][:-1]+'\\q2'+position+geometry+'}'
        text=''.join(chunks)
    if move_clips:
        text=BLOCK.sub(lambda m:'{'+CLIP.sub(lambda c:transform_clip(c[1],c[2],H,pixel_scale),m[1])+'}',text)
    if scale_appearance:
        from .appearance import transform as appearance, local_scale
        v=state(line)[0]
        text=appearance(text,line.get('style_data',{}),local_scale(H,v[:2]),line.get('reset_styles') or {})
    return text
