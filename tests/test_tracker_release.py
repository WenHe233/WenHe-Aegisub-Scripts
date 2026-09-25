import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
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


@unittest.skipIf(os.name!='nt','Windows installer tests')
class BootstrapTests(unittest.TestCase):
    def test_offline_cached_install_and_failed_upgrade(self):
        script=ROOT/'modules/wenhe/ASSTracker/bootstrap.ps1'
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
