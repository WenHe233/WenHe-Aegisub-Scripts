import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
import test_sync_dependency_control as existing

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('publisher',ROOT/'tools/publish_releases.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)


class TrackerFeedTests(unittest.TestCase):
    def setUp(self):
        self.repo=existing.RepositoryCase();self.repo.setUp();self.addCleanup(self.repo.tearDown)
        self.root=self.repo.root
        files={'apps/ass-tracker/ass_tracker/VERSION':'0.4.0\n',
               'apps/ass-tracker/ass_tracker/core.py':'# engine\n',
               'modules/wenhe/ASSTracker.lua':'return {}\n',
               'modules/wenhe/ASSTracker/json.lua':'return {}\n'}
        for name,value in files.items():
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
        self.repo.write_script('wenhe.ASSTracker','0.4.0')

    def test_multifile_hashes_dependency_and_version_gate(self):
        head=self.repo.commit('add tracker')
        feed,plan=existing.sync.synchronize(self.repo.initial,head,'2026-09-25')
        release=feed['modules']['wenhe.ASSTracker']['channels']['stable']
        self.assertEqual({f['name'] for f in release['files']},{'.lua','/VERSION','/json.lua'})
        self.assertEqual(feed['macros']['wenhe.ASSTracker']['channels']['stable']['requiredModules'][0]['version'],'0.4.0')
        self.assertTrue(plan[0]['runtime'])
        self.repo.write_feed(feed);base=self.repo.commit('published')
        (self.root/'apps/ass-tracker/ass_tracker/core.py').write_text('# changed engine\n')
        head=self.repo.commit('engine without version')
        with self.assertRaises(existing.sync.SyncError):existing.sync.synchronize(base,head,'2026-09-26')

    def test_documentation_does_not_release_and_pending_version_recovers(self):
        head=self.repo.commit('add tracker')
        feed,_=existing.sync.synchronize(self.repo.initial,head,'2026-09-25')
        self.repo.write_feed(feed);base=self.repo.commit('published')
        (self.root/'apps/ass-tracker/README.md').write_text('documentation')
        head=self.repo.commit('docs')
        _,plan=existing.sync.synchronize(base,head,'2026-09-26');self.assertEqual(plan,[])
        self.repo.write_script('wenhe.ASSTracker','0.4.1')
        (self.root/'apps/ass-tracker/ass_tracker/VERSION').write_text('0.4.1')
        base=self.repo.commit('version not published')
        (self.root/'apps/ass-tracker/README.md').write_text('more docs')
        head=self.repo.commit('later commit')
        _,plan=existing.sync.synchronize(base,head,'2026-09-26')
        self.assertEqual(plan[0]['version'],'0.4.1')


def fake_bundle(directory,version='0.4.0',commit='abc'):
    files={name:b'fixture' for name in ['ASSTracker.exe','ffmpeg/ffmpeg.exe','ffmpeg/ffprobe.exe']}
    files['_internal/ass_tracker/VERSION']=version.encode()
    manifest=dict(schema=1,version=version,platform='Windows-x64',source_commit=commit,
                  files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})
    directory.mkdir(parents=True,exist_ok=True)
    for name,value in files.items():
        p=directory/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value)
    (directory/'runtime.json').write_text(json.dumps(manifest))
    return manifest


class PublisherTests(unittest.TestCase):
    def test_refuses_overwriting_different_asset(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'asset.zip';p.write_bytes(b'new')
            release={'assets':[dict(name=p.name,digest='sha256:'+hashlib.sha256(b'old').hexdigest())]}
            with self.assertRaises(RuntimeError):publisher.ensure_asset(None,release,p,Path(td))

    def test_bundle_mismatch_and_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);fake_bundle(d/'bundle')
            zpath=d/'a.zip'
            with zipfile.ZipFile(zpath,'w') as z:
                for p in (d/'bundle').rglob('*'):
                    if p.is_file():z.write(p,p.relative_to(d/'bundle'))
            publisher.validate_bundle(zpath,'0.4.0','abc')
            with self.assertRaises(RuntimeError):publisher.validate_bundle(zpath,'0.4.1','abc')
            with self.assertRaises(RuntimeError):publisher.validate_bundle(zpath,'0.4.0','other')

    def test_source_verification_failure_never_publishes_feed(self):
        calls=[]
        def git(*args):
            calls.append(args)
            return 'abc' if args in [('rev-parse','HEAD'),('rev-parse','FETCH_HEAD^{commit}')] else 'existing'
        class API:
            def release(self,tag):return dict(id=1,draft=True)
        with patch.object(publisher,'git',git),patch.object(publisher,'GitHub',API),patch.object(publisher,'verify_raw',side_effect=RuntimeError('bad download')):
            with self.assertRaises(RuntimeError):
                publisher.publish(dict(releases=[dict(tag='test',raw_url='unused',sha1='bad')]),'abc',Path('.'))
        self.assertNotIn(('add','DependencyControl.json'),calls)
        self.assertNotIn(('push','origin','HEAD:main'),calls)


SCRIPT=ROOT/'modules/wenhe/ASSTracker/bootstrap.ps1'


class ReleaseServer:
    """Loopback stand-in for GitHub (/official/...) and a ghproxy-style mirror (/mirror/<full URL>)."""
    def __init__(self,archive,manifest,official=True,tamper=False,chunks=1):
        self.requests=[]
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                owner.requests.append(self.path)
                mirror=self.path.startswith('/mirror/')
                name=self.path.rsplit('/',1)[-1]
                if (not mirror and not official) or name not in ('release-manifest.json',Path(owner.asset).name):
                    self.send_error(404);return
                body=manifest if name=='release-manifest.json' else archive
                if mirror and tamper and body is archive:
                    body=bytearray(body);body[len(body)//2]^=0xFF;body=bytes(body)
                self.send_response(200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                step=-(-len(body)//chunks)
                try:
                    for i in range(0,len(body),step):
                        if i:time.sleep(.4)
                        self.wfile.write(body[i:i+step]);self.wfile.flush()
                except (ConnectionError,OSError):pass
        self.asset=json.loads(manifest)['asset']
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        port=self.server.server_address[1]
        self.official=f'http://127.0.0.1:{port}/official'
        self.mirror=f'http://127.0.0.1:{port}/mirror/'

    def close(self):
        self.server.shutdown();self.server.server_close()


@unittest.skipIf(os.name!='nt','Windows installer tests')
class BootstrapTests(unittest.TestCase):
    def release(self,directory,**options):
        fake_bundle(directory/'bundle')
        archive=directory/'wenhe.ASSTracker-0.4.0-Windows-x64.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for p in sorted((directory/'bundle').rglob('*')):
                if p.is_file():z.write(p,p.relative_to(directory/'bundle').as_posix())
        data=archive.read_bytes()
        manifest=dict(schema=1,version='0.4.0',platform='Windows-x64',source_commit='abc',
                      asset=archive.name,size=len(data),sha256=hashlib.sha256(data).hexdigest())
        server=ReleaseServer(data,json.dumps(manifest).encode(),**options)
        self.addCleanup(server.close)
        return server

    def download(self,directory,server,mirror=None,cancel=False):
        """Run a download-only bootstrap; return exit code, progress lines seen and output."""
        session=directory/'session 中文';session.mkdir(exist_ok=True)
        if cancel:(session/'cancel').write_text('cancel')
        args=['powershell','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(SCRIPT),
              '-Version','0.4.0','-CacheRoot',str(directory/'cache'),'-SessionDir',str(session),
              '-ReleaseBaseUrl',server.official,'-Mirror',mirror or server.mirror,'-PrepareOnly']
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        seen=set()
        while process.poll() is None:
            try:seen.add((session/'progress').read_text(encoding='utf-8'))
            except OSError:pass
            time.sleep(.05)
        output=process.communicate()
        return process.returncode,seen,output,session

    def staging(self,directory):
        return [p.name for p in (directory/'cache').glob('.staging-*')]

    def test_mirror_archive_is_checked_against_github_manifest(self):
        with tempfile.TemporaryDirectory(prefix='tracker mirror 中文 ') as td:
            d=Path(td);server=self.release(d,chunks=3)
            stale=d/'cache'/('.staging-'+'a'*32);fresh=d/'cache'/('.staging-'+'b'*32)
            stale.mkdir(parents=True);fresh.mkdir()
            old=time.time()-2*86400;os.utime(stale,(old,old))
            code,seen,output,_=self.download(d,server)
            self.assertEqual(code,0,output)
            self.assertTrue((d/'cache/0.4.0/ASSTracker.exe').is_file())
            manifest=[p for p in server.requests if p.endswith('release-manifest.json')]
            self.assertEqual(manifest,['/official/wenhe.ASSTracker-v0.4.0/release-manifest.json'])
            archive=[p for p in server.requests if p.endswith('.zip')]
            # ghproxy-style services expect the complete GitHub URL after the prefix.
            self.assertEqual(archive,['/mirror/'+server.official+'/wenhe.ASSTracker-v0.4.0/wenhe.ASSTracker-0.4.0-Windows-x64.zip'])
            self.assertTrue(any(s.startswith('download ') and s.endswith(' github') for s in seen),seen)
            self.assertEqual(self.staging(d),[fresh.name])

    def test_manifest_falls_back_to_mirror_when_github_fails(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);server=self.release(d,official=False,chunks=3)
            code,seen,output,_=self.download(d,server)
            self.assertEqual(code,0,output)
            self.assertTrue((d/'cache/0.4.0/runtime.json').is_file())
            self.assertTrue(any(s.startswith('download ') and s.endswith(' mirror') for s in seen),seen)

    def test_tampered_mirror_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);server=self.release(d,tamper=True)
            code,_,output,session=self.download(d,server)
            self.assertEqual(code,3,output)
            self.assertIn('checksum mismatch',(session/'error.log').read_text(encoding='utf-8'))
            self.assertFalse((d/'cache/0.4.0').exists())
            self.assertEqual(self.staging(d),[])

    def test_cancel_during_download_removes_partial_files(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);server=self.release(d,chunks=6)
            code,_,output,session=self.download(d,server,cancel=True)
            self.assertEqual(code,4,output)
            self.assertFalse((d/'cache/0.4.0').exists())
            self.assertEqual(self.staging(d),[])
            self.assertFalse((session/'error.log').exists())

    def test_invalid_mirror_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);server=self.release(d)
            for mirror in ['http://example.com/','https://example.com/p?x=1/','https://example.com']:
                code,_,output,session=self.download(d,server,mirror=mirror)
                self.assertEqual(code,3,(mirror,output))
                self.assertIn('Invalid mirror prefix',(session/'error.log').read_text(encoding='utf-8'))
                (session/'error.log').unlink()
            self.assertEqual(server.requests,[])

    def test_offline_cached_install_and_failed_upgrade(self):
        script=SCRIPT
        with tempfile.TemporaryDirectory(prefix='tracker install 中文 ') as td:
            d=Path(td);fake_bundle(d/'bundle')
            args=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Version','0.4.0',
                  '-OfflineBundle',str(d/'bundle'),'-CacheRoot',str(d/'cache'),'-PrepareOnly']
            first=subprocess.run(args,capture_output=True)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            # Cache must work without contacting GitHub or reading the offline source.
            cached=args[:args.index('-OfflineBundle')]+['-CacheRoot',str(d/'cache'),'-PrepareOnly']
            self.assertEqual(subprocess.run(cached,capture_output=True).returncode,0)
            fake_bundle(d/'bad','0.4.1');(d/'bad/ASSTracker.exe').write_bytes(b'corrupted')
            bad=[s.replace('0.4.0','0.4.1').replace(str(d/'bundle'),str(d/'bad')) for s in args]
            self.assertNotEqual(subprocess.run(bad,capture_output=True).returncode,0)
            self.assertTrue((d/'cache/0.4.0/ASSTracker.exe').exists())
            self.assertFalse((d/'cache/0.4.1').exists())


if __name__=='__main__':unittest.main()
