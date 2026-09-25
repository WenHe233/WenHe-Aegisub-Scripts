"""Fit a tracked plane to ASS's projection; keep editable text and polygon cards."""
import re
import numpy as np
from scipy.optimize import least_squares
from .subtitles import NUMBER, BLOCK, PAIR, CLIP, fmt, validate_text
from .planar import warp
from .paths import parse, transform_path, transform_clip

NAMES = ['fscx','fscy','frz','frx','fry','fax']


def number(tags, name, default):
    found=re.findall(r'\\'+name+'('+NUMBER+r')(?=\\|$)',tags)
    return float(found[-1]) if found else float(default)


def state(line):
    text=line['text'];tags=''.join(m[1] for m in BLOCK.finditer(text))
    style=line.get('style_data', {})
    xy={m[1]:[float(m[2]),float(m[3])] for m in PAIR.finditer(tags)}
    defaults=[style.get('scale_x',100),style.get('scale_y',100),style.get('angle',0),0,0,0]
    values=np.array(xy['pos']+[number(tags,k,d) for k,d in zip(NAMES,defaults)])
    return tags,values,np.array(xy.get('org',xy['pos'])),int(number(tags,'an',style.get('align',2)))


def validate_line(line):
    validate_text(line['text'])
    tags,v,org,alignment=state(line)
    if not BLOCK.match(line['text']):
        raise ValueError('透视模式请将排版标签放在行首。')
    if not np.isfinite(v).all() or min(v[2:4])<=0 or alignment not in range(1,10):
        raise ValueError('透视模式不支持负缩放、零缩放或无效对齐。')
    if number(tags,'fay',0)!=0 or re.search(r'\\a\d',tags):
        raise ValueError('透视模式暂不支持 fay 或旧式 a 对齐。请改为 an 对齐。')
    if re.search(r'\\fr'+NUMBER+r'(?=\\|$)',tags):
        raise ValueError('请将 fr 标签改写为 frz 后再导出。')
    # Geometry must be uniform throughout a line; colors may still change.
    remaining=line['text'][BLOCK.match(line['text']).end():] if BLOCK.match(line['text']) else line['text']
    if any(re.search(r'\\(?:fn|(?:fs|fsp|fscx|fscy|frx|fry|frz|fax|fay|an|b|i|pbo)(?=[+\-.\d\\]|$)|pos\(|org\()',m[1]) for m in BLOCK.finditer(remaining)):
        raise ValueError('透视模式不支持一行内部切换字体或几何参数，请拆成独立行。')
    drawing=number(tags,'p',0)>0 or re.search(r'\\p[1-9]',tags)
    if drawing:
        drawing_data(line)
    else:
        if re.search(r'\\b(?:[2-9]|[1-9]\d)',tags):
            raise ValueError('透视模式暂不支持数字字重，请用字体名称选择字重，并使用 b0/b1。')
        if re.search(r'\\[Nn]',line['text']):
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


def project(v,org,points,anchor):
    px,py,sx,sy,rz,rx,ry,fax=v
    q=np.asarray(points)@np.array([[1,0],[fax,1]])-anchor
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
        tags,v,org,an = state(line)
        size = points.max(axis=0)-points.min(axis=0)
        pbo=number(tags,'pbo',0)/2**(level-1)
        offset=np.array([0.,max(pbo-size[1],0.)])
        size[1]=max(size[1]-pbo,0.)+max(pbo,0.)
        anchor = size * [((an-1)%3)/2, (2-(an-1)//3)/2]
        # Recover the planar projection as a homography from four samples.
        sample=np.array([[0.,0.],[100.,0.],[100.,100.],[0.,100.]])
        import cv2
        original=cv2.getPerspectiveTransform(sample.astype(np.float32),project(v,org,sample+offset,anchor).astype(np.float32))
        combined=H@original
        chunks[i]=transform_path(path,combined,2**(level-1),pixel_scale)
        # Path is now in screen coordinates; clear only baked geometry.
        for j,chunk in enumerate(chunks):
            if chunk.startswith('{'):
                head=PAIR.sub('',chunk[1:-1])
                head=re.sub(r'\\(?:fscx|fscy|frz|frx|fry|fax|fay|an|pbo)'+NUMBER,'',head)
                chunks[j]='{'+head+'}'
        chunks[1]='{\\an7\\pos(0,0)\\fscx100\\fscy100\\frz0\\frx0\\fry0\\fax0\\fay0\\pbo0'+chunks[1][1:]
        text=''.join(chunks)
    else:
        tags,v,org,an=state(line)
        width,height=line['metrics']['width'],line['metrics']['height']
        anchor=np.array([((an-1)%3)*width/2, (2-(an-1)//3)*height/2])
        points=np.array([[x,y] for x in np.linspace(0,width,5) for y in np.linspace(0,height,3)])
        target=warp(project(v,org,points,anchor),H)
        neworg=warp(org[None],H)[0]
        guess=v.copy();guess[:2]=warp(v[:2][None],H)[0]
        fit=least_squares(lambda a:(project(a,neworg,points,anchor)-target).ravel(),guess,
                          x_scale='jac',max_nfev=600,ftol=1e-10,xtol=1e-10,gtol=1e-10)
        error=float(np.max(np.linalg.norm(project(fit.x,neworg,points,anchor)-target,axis=1)))
        if not fit.success or not np.isfinite(fit.x).all() or error>.08 or min(fit.x[2:4])<=0:
            raise ValueError(f'ASS 透视参数拟合失败（误差 {error:.3f}），请缩短镜头或换参考帧。')
        # All geometric tags are replaced in the initial override block.
        first=BLOCK.match(text)
        if not first: raise ValueError('请将排版标签放在行首。')
        head=PAIR.sub('',first[1])
        head=re.sub(r'\\(?:fscx|fscy|frz|frx|fry|fax)'+NUMBER,'',head)
        geometry='\\pos('+','.join(map(fmt,fit.x[:2]))+')\\org('+','.join(map(fmt,neworg))+')'
        geometry+=''.join('\\'+name+fmt(value) for name,value in zip(NAMES,fit.x[2:]))
        # Avoid reflow when the fitted horizontal scale changes.
        head=re.sub(r'\\q[0-3]','',head)
        text='{'+head+'\\q2'+geometry+'}'+text[first.end():]
    if move_clips:
        text=BLOCK.sub(lambda m:'{'+CLIP.sub(lambda c:transform_clip(c[1],c[2],H,pixel_scale),m[1])+'}',text)
    if scale_appearance:
        from .appearance import transform as appearance, local_scale
        _,v,_,_=state(line)
        text=appearance(text,line.get('style_data',{}),local_scale(H,v[:2]))
    return text
