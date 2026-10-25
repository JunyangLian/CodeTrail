"""Windows AppContainer verification worker. Never falls back to a host process.

No existing ACLs are changed. Windows creates a private per-execution profile;
only a filtered copy of the repository and a private stdlib interpreter enter it.
This is an OS access-control boundary, not a VM or kernel-exploit guarantee.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid

from .persistence import write_json_atomic
from .security import sensitive_workspace_path
from .workspace import IGNORED_PATH_NAMES

VERIFY = 'python -B -m unittest discover -v'


class SandboxUnavailable(RuntimeError):
    pass


def snapshot(source, destination):
    """No links, credentials, runtime state or host mount in the execution view."""
    source=Path(source).resolve();destination=Path(destination)
    known=[v.encode() for k,v in os.environ.items() if len(v)>=8 and any(m in k.upper() for m in ('API_KEY','SECRET','PASSWORD','TOKEN'))]
    total=0;count=0
    for directory,dirs,files in os.walk(source,followlinks=False):
        parent=Path(directory)
        for name in list(dirs):
            p=parent/name;relative=p.relative_to(source)
            if name in IGNORED_PATH_NAMES or sensitive_workspace_path(relative):dirs.remove(name);continue
            if getattr(p.lstat(),'st_file_attributes',0) & 0x400 or p.is_symlink():raise ValueError('Reparse directory refused in sandbox snapshot')
        for name in files:
            p=parent/name;relative=p.relative_to(source)
            if sensitive_workspace_path(relative) or name.casefold().endswith(('.env','.pyc')):continue
            if getattr(p.lstat(),'st_file_attributes',0) & 0x400 or p.is_symlink():raise ValueError('Reparse file refused in sandbox snapshot')
            info=p.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('Non-regular or hardlinked file refused in sandbox snapshot')
            if not p.resolve().is_relative_to(source):raise ValueError('Sandbox snapshot escaped workspace')
            total+=p.stat().st_size;count+=1
            if total>50*1024*1024 or count>10000:raise ValueError('Sandbox snapshot size limit exceeded')
            data=p.read_bytes()
            if any(value in data for value in known):raise ValueError('Known credential in sandbox input; execution refused')
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    return {'files':count,'bytes':total}


def _private_python(destination):
    home=Path(sys.base_prefix)
    destination.mkdir()
    for name in ('python.exe','python3.dll',f'python{sys.version_info.major}{sys.version_info.minor}.dll','vcruntime140.dll','vcruntime140_1.dll'):
        p=home/name
        if p.exists():shutil.copyfile(p,destination/name)
    if not (destination/'python.exe').exists():raise SandboxUnavailable('A standard Windows CPython installation is required')
    shutil.copytree(home/'Lib',destination/'Lib',ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests','idlelib','tkinter','ensurepip','*.pyc'))
    (destination/'DLLs').mkdir()
    for p in (home/'DLLs').glob('*'):
        if p.suffix.lower() in ('.pyd','.dll'):shutil.copyfile(p,destination/'DLLs'/p.name)


def _execute(source,timeout,audit):
    if os.name!='nt':raise SandboxUnavailable('Windows AppContainer is unavailable on this OS')
    import msvcrt
    k=C.WinDLL('kernel32',use_last_error=True);u=C.WinDLL('userenv',use_last_error=True);a=C.WinDLL('advapi32',use_last_error=True)
    P=C.c_void_p;SIZE=C.c_size_t
    class Caps(C.Structure):_fields_=[('sid',P),('capabilities',P),('count',W.DWORD),('reserved',W.DWORD)]
    class SI(C.Structure):
        _fields_=[('cb',W.DWORD),('reserved',W.LPWSTR),('desktop',W.LPWSTR),('title',W.LPWSTR),
          ('x',W.DWORD),('y',W.DWORD),('xs',W.DWORD),('ys',W.DWORD),('xc',W.DWORD),('yc',W.DWORD),
          ('fill',W.DWORD),('flags',W.DWORD),('show',W.WORD),('reserved2size',W.WORD),('reserved2',P),
          ('stdin',W.HANDLE),('stdout',W.HANDLE),('stderr',W.HANDLE)]
    class SIX(C.Structure):_fields_=[('si',SI),('attributes',P)]
    class PI(C.Structure):_fields_=[('process',W.HANDLE),('thread',W.HANDLE),('pid',W.DWORD),('tid',W.DWORD)]
    class Basic(C.Structure):
        _fields_=[('process_time',C.c_longlong),('job_time',C.c_longlong),('flags',W.DWORD),
          ('min_working',SIZE),('max_working',SIZE),('active_processes',W.DWORD),('affinity',SIZE),('priority',W.DWORD),('scheduling',W.DWORD)]
    class IO(C.Structure):_fields_=[(name,C.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(C.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',SIZE),('job_memory',SIZE),('peak_process',SIZE),('peak_job',SIZE)]
    def bind(dll,name,args,result=W.BOOL):
        f=getattr(dll,name);f.argtypes=args;f.restype=result;return f
    create=bind(u,'CreateAppContainerProfile',[W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,P,W.DWORD,C.POINTER(P)],C.c_long)
    delete=bind(u,'DeleteAppContainerProfile',[W.LPCWSTR],C.c_long)
    folder=bind(u,'GetAppContainerFolderPath',[W.LPCWSTR,C.POINTER(P)],C.c_long)
    convert=bind(a,'ConvertSidToStringSidW',[P,C.POINTER(P)])
    free_sid=bind(a,'FreeSid',[P],P);local_free=bind(k,'LocalFree',[P],P)
    init=bind(k,'InitializeProcThreadAttributeList',[P,W.DWORD,W.DWORD,C.POINTER(SIZE)])
    update=bind(k,'UpdateProcThreadAttribute',[P,W.DWORD,SIZE,P,SIZE,P,P])
    destroy=bind(k,'DeleteProcThreadAttributeList',[P],None)
    job_create=bind(k,'CreateJobObjectW',[P,W.LPCWSTR],W.HANDLE)
    job_set=bind(k,'SetInformationJobObject',[W.HANDLE,C.c_int,P,W.DWORD])
    job_assign=bind(k,'AssignProcessToJobObject',[W.HANDLE,W.HANDLE])
    job_kill=bind(k,'TerminateJobObject',[W.HANDLE,W.UINT])
    close=bind(k,'CloseHandle',[W.HANDLE]);resume=bind(k,'ResumeThread',[W.HANDLE],W.DWORD)
    wait=bind(k,'WaitForSingleObject',[W.HANDLE,W.DWORD],W.DWORD)
    exit_code=bind(k,'GetExitCodeProcess',[W.HANDLE,C.POINTER(W.DWORD)])
    process_create=bind(k,'CreateProcessW',[W.LPCWSTR,W.LPWSTR,P,P,W.BOOL,W.DWORD,P,W.LPCWSTR,C.POINTER(SIX),C.POINTER(PI)])
    terminate=bind(k,'TerminateProcess',[W.HANDLE,W.UINT])
    token_open=bind(a,'OpenProcessToken',[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)])
    token_info=bind(a,'GetTokenInformation',[W.HANDLE,C.c_int,P,W.DWORD,C.POINTER(W.DWORD)])
    def checked(ok):
        if not ok:raise C.WinError(C.get_last_error())
    # Network isolation depends on the OS filtering services. Never reconfigure them.
    scm_open=bind(a,'OpenSCManagerW',[W.LPCWSTR,W.LPCWSTR,W.DWORD],W.HANDLE)
    service_open=bind(a,'OpenServiceW',[W.HANDLE,W.LPCWSTR,W.DWORD],W.HANDLE)
    service_close=bind(a,'CloseServiceHandle',[W.HANDLE])
    class ServiceStatus(C.Structure):_fields_=[(n,W.DWORD) for n in ('type','state','controls','exit','specific_exit','checkpoint','hint')]
    query=bind(a,'QueryServiceStatus',[W.HANDLE,C.POINTER(ServiceStatus)])
    scm=scm_open(None,None,1);checked(scm)
    try:
        states={}
        for service_name in ('BFE','MpsSvc'):
            handle=service_open(scm,service_name,4);checked(handle)
            try:
                status=ServiceStatus();checked(query(handle,C.byref(status)));states[service_name]=status.state
                if status.state!=4:raise SandboxUnavailable('Windows network filtering service is not running; execution refused')
            finally:service_close(handle)
        audit['network_filter_service_states']=states
    finally:service_close(scm)
    name='CodeTrail.Run.'+uuid.uuid4().hex;sid=P();sidtext=P();pathbuf=P();profile=None
    job=None;pi=PI();attributes=None;fds=[];thread=None;chunks=[];limited=[];started=False
    hr=create(name,name,'CodeTrail isolated verification',None,0,C.byref(sid))
    if hr:raise SandboxUnavailable('CreateAppContainerProfile failed: '+hex(hr & 0xffffffff))
    try:
        checked(convert(sid,C.byref(sidtext)));sid_string=C.wstring_at(sidtext)
        hr=folder(sid_string,C.byref(pathbuf))
        if hr:raise SandboxUnavailable('GetAppContainerFolderPath failed: '+hex(hr & 0xffffffff))
        profile=Path(C.wstring_at(pathbuf)).resolve()
        package_root=(Path(os.environ['LOCALAPPDATA'])/'Packages').resolve()
        # Also validates the exact target before DeleteAppContainerProfile cleanup.
        if not profile.is_relative_to(package_root) or profile.relative_to(package_root).parts[0].casefold()!=name.casefold():
            raise SandboxUnavailable('Unexpected AppContainer profile location; refusing execution')
        audit.update(profile_name=name,profile_path=str(profile),appcontainer_sid=sid_string,capabilities=[],host_acl_changed=False)
        runtime=profile/'runtime';workspace=profile/'workspace';workspace.mkdir();(profile/'tmp').mkdir()
        _private_python(runtime);audit['snapshot']=snapshot(source,workspace)
        r,w=os.pipe();ir,iw=os.pipe();fds=[r,w,ir,iw];os.close(iw);fds.remove(iw)
        wh=msvcrt.get_osfhandle(w);ih=msvcrt.get_osfhandle(ir)
        os.set_handle_inheritable(wh,True);os.set_handle_inheritable(ih,True)
        caps=Caps(sid,None,0,0);size=SIZE()
        init(None,2,0,C.byref(size));attributes=C.create_string_buffer(size.value)
        checked(init(attributes,2,0,C.byref(size)))
        checked(update(attributes,0,0x20009,C.byref(caps),C.sizeof(caps),None,None))
        handles=(W.HANDLE*2)(wh,ih)
        checked(update(attributes,0,0x20002,handles,C.sizeof(handles),None,None))
        si=SIX();si.si.cb=C.sizeof(si);si.si.flags=0x100;si.si.stdin=ih;si.si.stdout=wh;si.si.stderr=wh;si.attributes=C.cast(attributes,P)
        job=job_create(None,None);checked(job)
        limits=Extended();limits.basic.flags=0x2000|0x100|0x8;limits.basic.active_processes=1;limits.process_memory=256*1024*1024
        checked(job_set(job,9,C.byref(limits),C.sizeof(limits)))
        env={'SYSTEMROOT':os.environ['SYSTEMROOT'],'WINDIR':os.environ['SYSTEMROOT'],'PATH':str(runtime),
             'TEMP':str(profile/'tmp'),'TMP':str(profile/'tmp'),'LOCALAPPDATA':str(profile),'USERPROFILE':str(profile),
             'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
        block=C.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items()))+'\0\0')
        exe=runtime/'python.exe';command=C.create_unicode_buffer(subprocess.list2cmdline([str(exe),'-I','-X','utf8','-B','-m','unittest','discover','-v']))
        checked(process_create(str(exe),command,None,None,True,0x4|0x80000|0x400|0x08000000,block,str(workspace),C.byref(si),C.byref(pi)))
        checked(job_assign(job,pi.process))
        audit['worker_pid']=pi.pid
        token=W.HANDLE()
        checked(token_open(pi.process,8,C.byref(token)))
        try:
            value=W.DWORD();needed=W.DWORD();checked(token_info(token,29,C.byref(value),C.sizeof(value),C.byref(needed)))
            if value.value!=1:raise SandboxUnavailable('Kernel did not mark worker TokenIsAppContainer; refusing to resume')
            audit['kernel_token_is_appcontainer']=True
        finally:close(token)
        os.close(w);fds.remove(w);os.close(ir);fds.remove(ir)
        def collect():
            size=0
            while True:
                data=os.read(r,4096)
                if not data:return
                size+=len(data)
                if size>65536:limited.append(True);job_kill(job,1);return
                chunks.append(data)
        thread=threading.Thread(target=collect,daemon=True);thread.start()
        if resume(pi.thread)==0xffffffff:checked(False)
        started=True;audit['worker_started']=True
        code=wait(pi.process,int(timeout*1000))
        if code==258:
            job_kill(job,1);audit['timeout_job_terminated']=wait(pi.process,5000)==0
            raise subprocess.TimeoutExpired(VERIFY,timeout)
        if code!=0:raise SandboxUnavailable('Unexpected wait result '+str(code))
        result=W.DWORD();checked(exit_code(pi.process,C.byref(result)));thread.join(5)
        if thread.is_alive():raise SandboxUnavailable('Worker output did not close')
        if limited:raise SandboxUnavailable('Worker output exceeded 65536 bytes; job terminated')
        return {'returncode':result.value,'output':b''.join(chunks).decode('utf-8',errors='replace')}
    finally:
        if pi.process:
            if not started:terminate(pi.process,1)
            if job:job_kill(job,1)
            wait(pi.process,5000)
        if thread:thread.join(5)
        for fd in fds:
            try:os.close(fd)
            except OSError:pass
        for handle in (pi.thread,pi.process,job):
            if handle:close(handle)
        if attributes:destroy(attributes)
        # The OS removes only the unique profile created by this call. No broad ACL or filesystem delete.
        if profile is not None and 'profile_name' in audit:audit['profile_cleanup_hresult']=hex(delete(name) & 0xffffffff)
        if pathbuf:C.WinDLL('ole32').CoTaskMemFree(pathbuf)
        if sidtext:local_free(sidtext)
        if sid:free_sid(sid)


def run_verification(root,command,timeout=20):
    if command.strip()!=VERIFY:raise ValueError('AppContainer only supports the exact approved unittest verification command')
    if not 1<=timeout<=120:raise ValueError('Sandbox timeout must be in [1,120]')
    root=Path(root).resolve();audit={'backend':'windows_appcontainer','command':VERIFY,'timeout':timeout,
       'network_capabilities':False,'child_process_limit':1,'memory_limit_bytes':256*1024*1024,'host_fallback':False}
    start=time.monotonic()
    try:
        result=_execute(root,timeout,audit);audit.update(status='exited',exit_code=result['returncode']);return result
    except Exception as exc:
        audit.update(status='failed_closed',error_type=type(exc).__name__,winerror=getattr(exc,'winerror',None));raise
    finally:
        audit['elapsed_ms']=round((time.monotonic()-start)*1000)
        write_json_atomic(root/'.pico/sandbox'/uuid.uuid4().hex/'audit.json',audit)
