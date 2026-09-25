"""Check the actual executable with external Python/FFmpeg removed from PATH."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    executable=ROOT/'build/tracker/dist/ASSTracker/ASSTracker.exe'
    with tempfile.TemporaryDirectory(prefix='portable check 中文 ') as td:
        report=Path(td)/'check.json'
        env=os.environ.copy()
        env['PATH']=str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')
        for name in list(env):
            if name.upper() in ('PYTHONPATH','PYTHONHOME'):env.pop(name)
        result=subprocess.run([str(executable),'--self-check','--output',str(report)],cwd=td,env=env,timeout=90)
        details=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'error':'No self-check report'}
        if result.returncode or details.get('status')!='ok':raise RuntimeError(details)
        print(json.dumps(details,ensure_ascii=False))
        # Actual PowerShell -> frozen GUI wait/cancel path, with no external
        # Python/FFmpeg in PATH. A mocked source-mode launcher cannot cover it.
        session=Path(td)/'bridge session & 中文';session.mkdir()
        (session/'job.json').write_text(json.dumps(dict(tool_version=details['version'])),encoding='utf-8')
        (session/'cancel').touch()
        powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        command=[str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
                 str(executable.parent/'automation/include/wenhe/ASSTracker/bootstrap.ps1'),
                 '-Version',details['version'],'-OfflineBundle',str(executable.parent),
                 '-CacheRoot',str(Path(td)/'cache'),'-SessionDir',str(session)]
        result=subprocess.run(command,env=env,capture_output=True,timeout=90)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        if (session/'result.json').exists() or (session/'error.log').read_text(encoding='utf-8'):
            raise RuntimeError('Cancelled GUI must exit cleanly without an applied result.')
        print('Frozen GUI bootstrap wait/cancel: OK')


if __name__=='__main__':main()
