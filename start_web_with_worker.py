"""Run Unified web and its durable job worker in the existing Render service."""
import os
import signal
import subprocess
import sys
import time


def commands():
    return [
        [sys.executable, '-m', 'gunicorn', '-c', 'gunicorn.conf.py', 'run:app'],
        [sys.executable, '-m', 'flask', '--app', 'run.py', 'run-job-worker',
         '--forever', '--sleep', os.getenv('JOB_WORKER_SLEEP_SECONDS', '5'),
         '--worker-id', 'render-unified-territory-worker', '--queue', 'territory', '--release-stale', '--stale-minutes', '15'],
    ]


def supervise(command_list, *, popen=subprocess.Popen, sleep=time.sleep):
    children=[]
    stopping=False

    def stop(signum, frame):
        nonlocal stopping
        stopping=True

    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        for command in command_list:
            children.append(popen(command))
        while not stopping:
            for child in children:
                code=child.poll()
                if code is not None:
                    # An unexpected clean worker exit also requires a restart.
                    print('Unified child process exited; restarting service.',flush=True)
                    return code if code else 1
            sleep(1)
        return 0
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for sig,handler in previous.items():signal.signal(sig,handler)


if __name__=='__main__':
    raise SystemExit(supervise(commands()))
