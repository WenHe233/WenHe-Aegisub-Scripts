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


if __name__=='__main__':main()
