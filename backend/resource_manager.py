import os
import time
import ctypes
import threading
import subprocess
from datetime import datetime

from ctypes import wintypes

class FILETIME(ctypes.Structure):
    _fields_ = [('dwLowDateTime', ctypes.c_uint), ('dwHighDateTime', ctypes.c_uint)]

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]

class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('PageFaultCount', wintypes.DWORD),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
    ]

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_void_p),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_char * 260)
    ]

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

class ResourceManager:
    _instance = None
    _lock = threading.Lock()

    # Threshold policies (RAM percentage)
    THRESHOLD_NORMAL = 75.0
    THRESHOLD_CAUTION = 85.0
    THRESHOLD_HIGH = 92.0
    THRESHOLD_CRITICAL = 95.0

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ResourceManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self._active_jobs = {}
        self._job_lock = threading.Lock()
        self._last_cpu_sample_time = 0
        self._last_cpu_percent = 0.0
        self._last_idle_time = 0
        self._last_kernel_time = 0
        self._last_user_time = 0
        self._last_gpu_sample_time = 0
        self._last_gpu_info = None

        self._initialized = True

    def get_ram_info(self):
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))

        total_mb = stat.ullTotalPhys / (1024 * 1024)
        avail_mb = stat.ullAvailPhys / (1024 * 1024)
        used_mb = total_mb - avail_mb
        percent = stat.dwMemoryLoad

        return {
            'total_mb': round(total_mb, 1),
            'available_mb': round(avail_mb, 1),
            'used_mb': round(used_mb, 1),
            'percent': float(percent)
        }

    def get_cpu_info(self):
        now = time.time()
        if now - self._last_cpu_sample_time < 0.2:
            return {'percent': self._last_cpu_percent}

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle),
            ctypes.byref(kernel),
            ctypes.byref(user)
        )

        to_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        idle_i = to_int(idle)
        kernel_i = to_int(kernel)
        user_i = to_int(user)

        if self._last_idle_time > 0:
            idle_diff = idle_i - self._last_idle_time
            kernel_diff = kernel_i - self._last_kernel_time
            user_diff = user_i - self._last_user_time
            total_sys = kernel_diff + user_diff

            if total_sys > 0:
                cpu_pct = round((total_sys - idle_diff) * 100.0 / total_sys, 1)
                self._last_cpu_percent = max(0.0, min(100.0, cpu_pct))

        self._last_idle_time = idle_i
        self._last_kernel_time = kernel_i
        self._last_user_time = user_i
        self._last_cpu_sample_time = now

        cores = os.cpu_count() or 4
        return {
            'percent': self._last_cpu_percent,
            'cores': cores,
            'logical_processors': cores
        }

    def get_process_telemetry(self, top_n=5):
        """
        Enumerate active Windows processes using lightweight native Win32 APIs.
        Returns top RAM and CPU processes without shell execution or sensitive argument exposure.
        """
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi

        hSnap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if hSnap == -1 or not hSnap:
            return {
                "top_ram_processes": [],
                "top_cpu_processes": [],
                "total_processes": 0
            }

        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        procs = {}
        ram_list = []

        ft_to_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        if kernel32.Process32First(hSnap, ctypes.byref(pe)):
            while True:
                pid = pe.th32ProcessID
                name = pe.szExeFile.decode('ansi', errors='ignore')
                if pid > 0:
                    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if h:
                        pmc = PROCESS_MEMORY_COUNTERS()
                        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                        mem_mb = 0.0
                        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                            mem_mb = pmc.WorkingSetSize / (1024 * 1024)
                        ram_list.append({
                            "name": name,
                            "pid": pid,
                            "ram_mb": round(mem_mb, 1)
                        })
                        procs[pid] = (name, h)
                if not kernel32.Process32Next(hSnap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(hSnap)

        ram_list.sort(key=lambda x: x["ram_mb"], reverse=True)
        top_ram = ram_list[:top_n]

        cpu_results = []
        if procs:
            idle1, kern1, usr1 = FILETIME(), FILETIME(), FILETIME()
            kernel32.GetSystemTimes(ctypes.byref(idle1), ctypes.byref(kern1), ctypes.byref(usr1))
            times1 = {}
            cr, ex, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            for pid, (name, h) in procs.items():
                if kernel32.GetProcessTimes(h, ctypes.byref(cr), ctypes.byref(ex), ctypes.byref(k), ctypes.byref(u)):
                    times1[pid] = ft_to_int(k) + ft_to_int(u)

            time.sleep(0.10)  # 100ms sample window

            idle2, kern2, usr2 = FILETIME(), FILETIME(), FILETIME()
            kernel32.GetSystemTimes(ctypes.byref(idle2), ctypes.byref(kern2), ctypes.byref(usr2))
            sys_diff = (ft_to_int(kern2) + ft_to_int(usr2)) - (ft_to_int(kern1) + ft_to_int(usr1))

            for pid, (name, h) in procs.items():
                if pid in times1:
                    if kernel32.GetProcessTimes(h, ctypes.byref(cr), ctypes.byref(ex), ctypes.byref(k), ctypes.byref(u)):
                        t2 = ft_to_int(k) + ft_to_int(u)
                        diff = t2 - times1[pid]
                        if sys_diff > 0 and diff > 0:
                            pct = round((diff / sys_diff) * 100.0, 1)
                            cpu_results.append({
                                "name": name,
                                "pid": pid,
                                "cpu_percent": pct
                            })
                kernel32.CloseHandle(h)

            cpu_results.sort(key=lambda x: x["cpu_percent"], reverse=True)

        # Grouping by Application / Executable
        try:
            from backend.assistant.desktop_context import PROCESS_TO_APP_MAP
        except Exception:
            PROCESS_TO_APP_MAP = {}

        app_groups = {}
        for p in ram_list:
            k = p["name"].lower()
            if k not in app_groups:
                display = PROCESS_TO_APP_MAP.get(k)
                if not display:
                    display = os.path.splitext(p["name"])[0].replace("_", " ").title()
                app_groups[k] = {
                    "name": p["name"],
                    "display_name": display,
                    "count": 0,
                    "total_ram_mb": 0.0,
                    "total_cpu_percent": 0.0,
                    "pids": []
                }
            app_groups[k]["count"] += 1
            app_groups[k]["total_ram_mb"] += p["ram_mb"]
            app_groups[k]["pids"].append(p["pid"])

        for p in cpu_results:
            k = p["name"].lower()
            if k in app_groups:
                app_groups[k]["total_cpu_percent"] += p.get("cpu_percent", 0.0)

        grouped = list(app_groups.values())
        for g in grouped:
            g["total_ram_mb"] = round(g["total_ram_mb"], 1)
            g["total_cpu_percent"] = round(g["total_cpu_percent"], 1)
        grouped.sort(key=lambda x: x["total_ram_mb"], reverse=True)

        return {
            "grouped_processes": grouped[:top_n],
            "top_ram_processes": top_ram,
            "top_cpu_processes": cpu_results[:top_n],
            "total_processes": len(ram_list)
        }

    def get_gpu_info(self):
        now = time.time()
        if self._last_gpu_info is not None and (now - self._last_gpu_sample_time < 2.0):
            return self._last_gpu_info

        try:
            out = subprocess.check_output([
                'nvidia-smi',
                '--query-gpu=name,utilization.gpu,memory.used,memory.free,memory.total',
                '--format=csv,noheader,nounits'
            ], timeout=5).decode().strip()

            parts = [p.strip() for p in out.split(',')]
            name = parts[0]
            gpu_util = int(parts[1])
            vram_used = int(parts[2])
            vram_free = int(parts[3])
            vram_total = int(parts[4])

            res = {
                'available': True,
                'name': name,
                'utilization_percent': gpu_util,
                'vram_used_mb': vram_used,
                'vram_free_mb': vram_free,
                'vram_total_mb': vram_total
            }
            self._last_gpu_sample_time = now
            self._last_gpu_info = res
            return res
        except Exception:
            res = {
                'available': False,
                'name': None,
                'utilization_percent': 0,
                'vram_used_mb': 0,
                'vram_free_mb': 0,
                'vram_total_mb': 0
            }
            self._last_gpu_sample_time = now
            self._last_gpu_info = res
            return res

    def register_ai_job(self, job_id, job_type='llm'):
        with self._job_lock:
            self._active_jobs[job_id] = {
                'type': job_type,
                'started_at': time.time()
            }

    def unregister_ai_job(self, job_id):
        with self._job_lock:
            self._active_jobs.pop(job_id, None)

    def get_active_jobs_info(self):
        with self._job_lock:
            qwen_active = any(j['type'] == 'llm' for j in self._active_jobs.values())
            whisper_active = any(j['type'] == 'whisper' for j in self._active_jobs.values())
            return {
                'active_job_count': len(self._active_jobs),
                'qwen_active': qwen_active,
                'whisper_active': whisper_active,
                'jobs': list(self._active_jobs.keys())
            }

    def get_resource_state(self):
        ram = self.get_ram_info()
        pct = ram['percent']

        if pct >= self.THRESHOLD_CRITICAL:
            state = 'CRITICAL'
        elif pct >= self.THRESHOLD_HIGH:
            state = 'HIGH'
        elif pct >= self.THRESHOLD_CAUTION:
            state = 'CAUTION'
        else:
            state = 'NORMAL'

        return {
            'state': state,
            'ram': ram,
            'cpu': self.get_cpu_info(),
            'gpu': self.get_gpu_info(),
            'jobs': self.get_active_jobs_info(),
            'timestamp': time.time()
        }

    def can_start_llm(self):
        ram = self.get_ram_info()
        ram_pct = ram['percent']
        jobs = self.get_active_jobs_info()
        qwen_active = jobs['qwen_active']

        if qwen_active:
            return False, 'Another LLM inference job is currently running.'

        if ram_pct >= self.THRESHOLD_CRITICAL:
            return False, f'System memory at {ram_pct:.1f}% (CRITICAL threshold >= {self.THRESHOLD_CRITICAL}%). Job will queue.'

        return True, 'Resources available.'

    def can_start_whisper(self):
        ram = self.get_ram_info()
        ram_pct = ram['percent']
        jobs = self.get_active_jobs_info()
        whisper_active = jobs['whisper_active']

        if whisper_active:
            return False, 'Another transcription job is currently active.'

        if ram_pct >= self.THRESHOLD_CRITICAL:
            return False, f'System memory at {ram_pct:.1f}% (CRITICAL threshold). Waiting for memory recovery.'

        return True, 'Resources available.'

    def wait_for_resources(self, job_type='llm', timeout=30.0, cancel_event=None, progress_callback=None):
        start_time = time.time()
        check_fn = self.can_start_llm if job_type == 'llm' else self.can_start_whisper

        while time.time() - start_time < timeout:
            if cancel_event and cancel_event.is_set():
                return False

            allowed, reason = check_fn()
            if allowed:
                return True

            if progress_callback:
                try:
                    progress_callback(reason)
                except Exception:
                    pass

            time.sleep(1.0)

        allowed, _ = check_fn()
        return allowed
