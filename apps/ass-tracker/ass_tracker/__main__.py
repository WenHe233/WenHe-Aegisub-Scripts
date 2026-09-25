import argparse
import sys
from pathlib import Path
from .core import read_job, run_job, save_json
from . import __version__


def main():
    parser = argparse.ArgumentParser(description=f"ASS 追踪 {__version__}")
    parser.add_argument('--version',action='version',version=__version__)
    parser.add_argument('--self-check',action='store_true',help='检查打包依赖、窗口和视频解码')
    parser.add_argument("job", nargs="?", help="Aegisub 导出的 .job.json")
    parser.add_argument("--headless", action="store_true", help="使用任务中的 ROI，无界面运行")
    parser.add_argument("--output", help="输出 .result.json 路径")
    parser.add_argument('--bridge-dir', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_check:
        from .selfcheck import check
        report=check()
        if args.output: save_json(args.output,report)
        elif sys.stdout: print(report)
        return 0 if report['status']=='ok' else 1
    if args.bridge_dir:
        if args.headless or args.job or args.output:
            parser.error('--bridge-dir 不能与其他工作模式同时使用')
        directory = Path(args.bridge_dir).resolve(strict=True)
        import traceback
        with (directory / 'error.log').open('w', encoding='utf-8') as log:
            previous = sys.stderr
            sys.stderr = log
            try:
                from .gui import main as gui
                gui(str(directory / 'job.json'), bridge_dir=directory)
            except Exception:
                traceback.print_exc(file=log)
                return 1
            finally:
                sys.stderr = previous
        return 0
    if not args.headless:
        from .gui import main as gui
        gui(args.job)
        return 0
    if not args.job:
        parser.error("--headless 需要任务路径")
    job = read_job(args.job)
    result = run_job(job)
    target = args.output or str(Path(args.job).with_suffix(".result.json"))
    save_json(target, result)
    if sys.stdout: print(f"{result['status']}: {len(result['generated'])} events -> {target}")
    if result["status"] != "complete":
        for failure in result["failures"]:
            print(f"frame {failure['frame']}: {failure['reason']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
