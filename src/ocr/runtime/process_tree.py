"""Supervised subprocesses: no GPU owner may survive a phase boundary."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import time

import psutil


class ProcessTree:
    def __init__(self, process):
        self.process = process
        self.seen = {}
        self.job = None
        if os.name == "nt":
            from ctypes import wintypes
            class Basic(ctypes.Structure):
                _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                            ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                            ("scheduling", wintypes.DWORD)]
            class Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("read", "write", "other", "rb", "wb", "ob")]
            class Limits(ctypes.Structure):
                _fields_ = [("basic", Basic), ("io", Io), ("process_mem", ctypes.c_size_t),
                            ("job_mem", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                            ("peak_job", ctypes.c_size_t)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel = kernel
            self.job = kernel.CreateJobObjectW(None, None)
            limits = Limits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.job or not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                raise OSError(ctypes.get_last_error(), "Cannot create a kill-on-close phase job")
            if not kernel.AssignProcessToJobObject(self.job, wintypes.HANDLE(int(process._handle))):
                self.close()
                raise OSError(ctypes.get_last_error(), "Cannot assign phase process to Windows job")
        self.observe()

    def observe(self):
        try:
            root = psutil.Process(self.process.pid)
            for proc in [root, *root.children(recursive=True)]:
                self.seen[proc.pid] = proc.create_time()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            # Windows can deny OpenProcess briefly while a process exits.
            if self.process.poll() is None:
                raise

    def close(self):
        self.observe()
        job_owned = bool(self.job)
        if job_owned:
            if not self.kernel.TerminateJobObject(self.job, 1):
                raise OSError(ctypes.get_last_error(), "Cannot terminate phase job")
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # The Windows job already terminates every descendant using its owned
        # handle. Reopening each dying PID to kill it races with kernel teardown
        # and can report AccessDenied even though termination succeeded.
        for pid, created in (() if job_owned else self.seen.items()):
            try:
                proc = psutil.Process(pid)
                if proc.create_time() == created:
                    proc.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                pass  # The survivor check below must confirm exit.
        self.process.wait(timeout=15)
        deadline = time.monotonic() + 15
        while self.survivors() and time.monotonic() < deadline:
            time.sleep(.05)
        if self.survivors():
            raise RuntimeError(f"GPU_PHASE_PROCESS_TREE_SURVIVED: {self.survivors()}")

    def survivors(self):
        alive = []
        for pid, created in self.seen.items():
            try:
                proc = psutil.Process(pid)
                if proc.create_time() == created and proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    alive.append(pid)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                alive.append(pid)  # Never declare an uninspectable PID exited.
        return alive


def supervised_run(command, *, cwd, env, log_path, timeout, observe=None):
    """Return only after the entire tree is gone, including on failure/cancel."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                start_new_session=os.name != "nt")
        try:
            tree = ProcessTree(proc)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        started = time.monotonic()
        try:
            while proc.poll() is None:
                tree.observe()
                if observe:
                    observe(proc.pid, sorted(tree.seen))
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"Phase exceeded {timeout:g}s; see {log_path}")
                time.sleep(.2)
            if proc.returncode:
                raise RuntimeError(f"Phase exited with code {proc.returncode}; see {log_path}")
        finally:
            tree.close()
        return {"pid": proc.pid, "descendants": sorted(set(tree.seen) - {proc.pid}),
                "seconds": round(time.monotonic() - started, 3), "tree_exited": True,
                "log_path": str(log_path)}
