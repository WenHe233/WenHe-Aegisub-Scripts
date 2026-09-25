"""Build the Windows portable application and integrity manifests."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'apps/ass-tracker'
FFMPEG_URL='https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip'
FFMPEG_SHA='04861d3339c5ebe38b56c19a15cf2c0cc97f5de4fa8910e4d47e5e6404e4a2d4'
SOURCE_URL='https://github.com/FFmpeg/FFmpeg/archive/db69d06eeeab4f46da15030a80d539efb4503ca8.tar.gz'
SOURCE_SHA='173ba614d8636491106cba08d79db469b5dee4b479388bc4fbcead8ca1fb79b1'


def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def download(url,path,expected):
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.download')
        with urllib.request.urlopen(url,timeout=120) as response, temporary.open('wb') as out:
            shutil.copyfileobj(response,out)
        if digest(temporary)!=expected: raise RuntimeError('Upstream download checksum mismatch: '+url)
        temporary.replace(path)
    if digest(path)!=expected: raise RuntimeError('Cached download checksum mismatch: '+str(path))


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def version():
    import re
    value=(APP/'ass_tracker/VERSION').read_text().strip()
    macro=(ROOT/'macros/wenhe.ASSTracker.lua').read_text(encoding='utf-8')
    if not re.fullmatch(r'\d+\.\d+\.\d+',value) or f'script_version = "{value}"' not in macro:
        raise ValueError('VERSION and macro metadata do not match.')
    return value


def prepare_ffmpeg(target):
    archive_path=ROOT/'.cache/ffmpeg-7.1.1.zip'
    download(FFMPEG_URL,archive_path,FFMPEG_SHA)
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            relative='/'.join(name.split('/')[1:])
            if name.endswith('/') or not relative: continue
            if relative in ('bin/ffmpeg.exe','bin/ffprobe.exe'):
                destination=target/Path(relative).name
            elif not relative.startswith('bin/'):
                destination=target/'upstream'/relative
            else: continue
            if not destination.resolve().is_relative_to(target.resolve()): raise RuntimeError('Unsafe archive path.')
            destination.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(name) as src,destination.open('wb') as dst: shutil.copyfileobj(src,dst)


def build(skip_freeze=False):
    value=version()
    work=ROOT/'build/tracker'
    work.mkdir(parents=True,exist_ok=True)
    bundle=work/'dist/ASSTracker'
    if not skip_freeze:
        run=work/'runs'/uuid.uuid4().hex
        run.mkdir(parents=True)
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
                        '--name','ASSTracker','--distpath',str(run/'dist'),'--workpath',str(run/'work'),
                        '--specpath',str(run),'--paths',str(APP),'--hidden-import','ass_tracker.gui',
                        '--hidden-import','ass_tracker.selfcheck','--collect-submodules','scipy._external.array_api_compat.numpy',
                        '--add-data',str(APP/'ass_tracker/VERSION')+';ass_tracker',
                        str(APP/'launcher.py')],cwd=ROOT,check=True)
        # Atomic directory renames stay inside the checked build workspace.
        # Keeping previous builds also avoids OneDrive read-only directory deletion.
        previous=work/('previous-'+uuid.uuid4().hex)
        fresh=run/'dist/ASSTracker'
        for path in (bundle,previous,fresh):
            if not path.resolve().is_relative_to(work.resolve()):raise RuntimeError('Unsafe build output path.')
        bundle.parent.mkdir(parents=True,exist_ok=True)
        if bundle.exists():bundle.rename(previous)
        fresh.rename(bundle)
    if not (bundle/'ASSTracker.exe').is_file(): raise RuntimeError('Windows application was not built.')
    cache=ROOT/'.cache'
    source=cache/'ffmpeg-source.tar.gz'
    prepare_ffmpeg(bundle/'ffmpeg')
    download(SOURCE_URL,source,SOURCE_SHA)
    include=bundle/'automation/include/wenhe'
    include.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'modules/wenhe/ASSTracker.lua',include)
    shutil.copytree(ROOT/'modules/wenhe/ASSTracker',include/'ASSTracker',dirs_exist_ok=True)
    shutil.copy2(APP/'ass_tracker/VERSION',include/'ASSTracker/VERSION')
    (bundle/'automation/autoload').mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'macros/wenhe.ASSTracker.lua',bundle/'automation/autoload')
    for src in [APP/'install.ps1',APP/'README.md',APP/'VALIDATION.md',ROOT/'LICENSE',APP/'THIRD_PARTY.md']:
        shutil.copy2(src,bundle/src.name)
    licenses=bundle/'licenses';licenses.mkdir(exist_ok=True)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file():raise RuntimeError('Missing Python distribution license.')
    shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
    for distribution in ['numpy','opencv-python-headless','scipy','Pillow','pyinstaller']:
        package=importlib.metadata.distribution(distribution)
        for file in package.files or []:
            if any(s in str(file).lower() for s in ('license','copying','notice')) and '.dist-info/' in str(file):
                src=Path(package.locate_file(file))
                if src.is_file():
                    target=licenses/distribution/Path(str(file).split('.dist-info/',1)[-1])
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
    source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    runtime=dict(schema=1,version=value,platform='Windows-x64',source_commit=source_commit,
        components={'Python':sys.version.split()[0],'FFmpeg':'7.1.1',**{
            name:importlib.metadata.version(name) for name in ['numpy','scipy','opencv-python-headless','Pillow','pyinstaller']}},files={
        p.relative_to(bundle).as_posix():digest(p) for p in sorted(bundle.rglob('*'))
        if p.is_file() and p.name!='runtime.json'})
    write_json(bundle/'runtime.json',runtime)
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    target=output/f'wenhe.ASSTracker-{value}-Windows-x64.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                entry=zipfile.ZipInfo(path.relative_to(bundle).as_posix(),date_time=(2020,1,1,0,0,0))
                entry.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(entry,path.read_bytes())
    write_json(output/'release-manifest.json',dict(schema=1,version=value,platform='Windows-x64',
               source_commit=source_commit,asset=target.name,size=target.stat().st_size,sha256=digest(target)))
    shutil.copy2(source,output/'ffmpeg-source.tar.gz')
    checks=[f'{digest(p)}  {p.name}' for p in [target,output/'release-manifest.json',output/'ffmpeg-source.tar.gz']]
    (output/'SHA256SUMS.txt').write_text('\n'.join(checks)+'\n',encoding='utf-8')
    print(target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-freeze',action='store_true',help='Reuse an existing local PyInstaller build')
    parser.add_argument('--prepare-ffmpeg',action='store_true',help='Prepare checked render-test dependency only')
    args=parser.parse_args()
    if args.prepare_ffmpeg:prepare_ffmpeg(ROOT/'build/test-ffmpeg')
    else:build(args.skip_freeze)
