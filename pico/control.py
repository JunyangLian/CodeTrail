"""Cooperative cancellation with explicit uncertainty at side-effect boundaries."""
import os
import subprocess
import threading
import time


class Cancelled(RuntimeError):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise Cancelled('Task cancelled by operator; completed effects are preserved')

    def wait(self, seconds):
        if self.event.wait(seconds):
            self.check()


def kill_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_cancellable(command, cwd, env, timeout, cancellation):
    """Host compatibility executor; this function is NOT an OS sandbox."""
    cancellation.check()
    process = subprocess.Popen(command, cwd=cwd, env=env, shell=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=os.name != 'nt',
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    started = time.monotonic()
    try:
        while True:
            cancellation.check()
            if time.monotonic() - started > timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                out, err = process.communicate(timeout=.1)
                return subprocess.CompletedProcess(command, process.returncode, out, err)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        kill_tree(process)
        process.communicate(timeout=10)
        raise
