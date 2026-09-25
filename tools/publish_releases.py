"""Publish immutable assets first, then expose their DependencyControl feed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
REPO='WenHe233/WenHe-Aegisub-Scripts'


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


class GitHub:
    def __init__(self):
        self.token=os.environ['GH_TOKEN']
        self.opener=urllib.request.build_opener(SafeRedirect())

    def request(self,path,method='GET',data=None,raw=False):
        url=path if path.startswith('https://') else f'https://api.github.com/repos/{REPO}/{path}'
        host=urllib.parse.urlsplit(url).netloc
        headers={'Accept':'application/octet-stream' if raw else 'application/vnd.github+json',
                 'User-Agent':'WenHe-ASSTracker-release','X-GitHub-Api-Version':'2022-11-28'}
        if host in ('api.github.com','uploads.github.com'): headers['Authorization']='Bearer '+self.token
        if isinstance(data,dict):
            data=json.dumps(data,ensure_ascii=False).encode();headers['Content-Type']='application/json'
        elif data is not None: headers['Content-Type']='application/octet-stream'
        response=self.opener.open(urllib.request.Request(url,data=data,headers=headers,method=method),timeout=300)
        if raw: return response
        with response: return json.load(response)

    def release(self,tag):
        try:return self.request('releases/tags/'+tag)
        except urllib.error.HTTPError as e:
            if e.code!=404:raise
            return None

    def download(self,asset,path):
        with self.request(asset['url'],raw=True) as response,path.open('wb') as out:
            import shutil
            shutil.copyfileobj(response,out)


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,encoding='utf-8').strip()


def digest(path,algorithm='sha256'):
    with path.open('rb') as f:return hashlib.file_digest(f,algorithm).hexdigest()


def validate_bundle(path,version,commit):
    with zipfile.ZipFile(path) as z:
        manifest=json.loads(z.read('runtime.json'))
        if (manifest.get('version'),manifest.get('source_commit'),manifest.get('platform')) != (version,commit,'Windows-x64'):
            raise RuntimeError('Existing runtime belongs to another version/platform/commit.')
        required={'ASSTracker.exe','ffmpeg/ffmpeg.exe','ffmpeg/ffprobe.exe','_internal/ass_tracker/VERSION'}
        if not required <= manifest.get('files',{}).keys():raise RuntimeError('Incomplete runtime manifest.')
        for name,expected in manifest['files'].items():
            with z.open(name) as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=expected:raise RuntimeError('Corrupt runtime file: '+name)
    return manifest


def ensure_asset(api,release,path,temporary):
    existing=next((a for a in release['assets'] if a['name']==path.name),None)
    expected=digest(path)
    if existing is None:
        url=release['upload_url'].split('{',1)[0]+'?'+urllib.parse.urlencode({'name':path.name})
        existing=api.request(url,'POST',path.read_bytes())
        release['assets'].append(existing)
    server_digest=existing.get('digest')
    if server_digest:
        if server_digest!='sha256:'+expected:raise RuntimeError('Published asset differs; refusing overwrite: '+path.name)
    else:
        downloaded=temporary/('verify-'+path.name)
        api.download(existing,downloaded)
        if digest(downloaded)!=expected:raise RuntimeError('Published asset differs; refusing overwrite: '+path.name)


def runtime_assets(api,release,item,commit,output,temporary):
    name=f"wenhe.ASSTracker-{item['version']}-Windows-x64.zip"
    bundle=output/name
    old=next((a for a in release['assets'] if a['name']==name),None)
    if old:
        # PyInstaller embeds build-time details. Resume from the already-uploaded
        # verified bundle for this source commit instead of overwriting it.
        bundle=temporary/name
        api.download(old,bundle)
    validate_bundle(bundle,item['version'],commit)
    manifest=temporary/'release-manifest.json'
    manifest.write_text(json.dumps(dict(schema=1,version=item['version'],platform='Windows-x64',source_commit=commit,
        asset=name,size=bundle.stat().st_size,sha256=digest(bundle)),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    source=output/'ffmpeg-source.tar.gz'
    checks=temporary/'SHA256SUMS.txt'
    checks.write_text(''.join(f'{digest(p)}  {p.name}\n' for p in [bundle,manifest,source]),encoding='utf-8')
    for path in [bundle,source,checks,manifest]:ensure_asset(api,release,path,temporary)


def verify_raw(files):
    for item in files:
        for attempt in range(8):
            try:
                with urllib.request.urlopen(item['url'],timeout=30) as response:
                    actual=hashlib.file_digest(response,'sha1').hexdigest()
                if actual==item['sha1']:break
            except urllib.error.URLError:pass
            if attempt==7:raise RuntimeError('Published source verification failed: '+item['url'])
            time.sleep(2)


def publish(plan,commit,output):
    if git('rev-parse','HEAD')!=commit:raise RuntimeError('Checkout differs from release commit.')
    api=GitHub()
    with tempfile.TemporaryDirectory(prefix='wenhe-release-') as td:
        temporary=Path(td)
        for item in plan['releases']:
            tag=item['tag']
            existing=git('ls-remote','--tags','origin','refs/tags/'+tag)
            if existing:
                git('fetch','origin','refs/tags/'+tag)
                if git('rev-parse','FETCH_HEAD^{commit}')!=commit:raise RuntimeError('Tag points to another commit: '+tag)
            else:
                git('tag','-a',tag,commit,'-m',tag);git('push','origin','refs/tags/'+tag)
            release=api.release(tag)
            if release is None:
                release=api.request('releases','POST',dict(tag_name=tag,target_commitish=commit,draft=True,
                    name=f"{item['name']} v{item['version']}",body='\n'.join('- '+s for s in item['release_notes'])))
            if item.get('runtime'):runtime_assets(api,release,item,commit,output,temporary)
            verify_raw(item.get('files',[dict(url=item['raw_url'],sha1=item['sha1'])]))
            if release['draft']:api.request(f"releases/{release['id']}",'PATCH',dict(draft=False))
            if item.get('runtime'):
                url=f'https://github.com/{REPO}/releases/download/{tag}/release-manifest.json'
                with urllib.request.urlopen(url,timeout=30) as response:manifest=json.load(response)
                if manifest['source_commit']!=commit or manifest['version']!=item['version']:
                    raise RuntimeError('Public runtime manifest mismatch.')
            print('Verified release: '+tag)
    # No feed write/push occurs above. A failed asset leaves the previous feed live.
    git('fetch','origin','main')
    remote=git('rev-parse','origin/main')
    feed=(ROOT/'DependencyControl.json').read_bytes()
    if remote!=commit:
        previous=subprocess.check_output(['git','show',remote+':DependencyControl.json'],cwd=ROOT)
        if git('rev-parse',remote+'^')==commit and previous==feed and git('show','-s','--format=%s',remote)=='chore(release): update DependencyControl feed [skip ci]':
            print('Feed already published.');return
        raise RuntimeError('main advanced during release; refusing to overwrite it.')
    git('add','DependencyControl.json')
    if git('diff','--cached','--name-only'):
        git('commit','-m','chore(release): update DependencyControl feed [skip ci]')
        git('push','origin','HEAD:main')
    print('DependencyControl feed published after verification.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--assets',type=Path,default=ROOT/'dist')
    args=parser.parse_args()
    publish(json.loads(args.plan.read_text(encoding='utf-8')),args.commit,args.assets)
