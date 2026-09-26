-- Windows/LuaJIT bridge. No cmd.exe, PowerShell, or shell interpolation.
local ffi = require('ffi')
local bit = require('bit')
if ffi.os ~= 'Windows' then error('一键追踪目前需要 Windows；其他平台仍可手动导入导出。') end
ffi.cdef[[
typedef struct { unsigned long cb; wchar_t *reserved,*desktop,*title;
 unsigned long x,y,xsize,ysize,xchars,ychars,fill,flags;
 unsigned short show,reserved_size; unsigned char *reserved_bytes;
 void *stdin_handle,*stdout_handle,*stderr_handle; } AT_STARTUP;
typedef struct { void *process,*thread; unsigned long pid,tid; } AT_PROCESS;
typedef struct { int64_t process_time,job_time; unsigned long flags;
 size_t min_work,max_work; unsigned long active; size_t affinity;
 unsigned long priority,scheduling; } AT_LIMITS;
typedef struct { uint64_t a,b,c,d,e,f; } AT_IO;
typedef struct { AT_LIMITS basic; AT_IO io; size_t a,b,c,d; } AT_JOB_LIMITS;
int __stdcall MultiByteToWideChar(unsigned int,unsigned long,const char*,int,wchar_t*,int);
int __stdcall WideCharToMultiByte(unsigned int,unsigned long,const wchar_t*,int,char*,int,const char*,int*);
int __stdcall CreateProcessW(const wchar_t*,wchar_t*,void*,void*,int,unsigned long,void*,const wchar_t*,AT_STARTUP*,AT_PROCESS*);
unsigned long __stdcall WaitForSingleObject(void*,unsigned long);
int __stdcall GetExitCodeProcess(void*,unsigned long*);
int __stdcall TerminateProcess(void*,unsigned int);
int __stdcall CloseHandle(void*);
unsigned long __stdcall ResumeThread(void*);
unsigned long __stdcall GetLastError(void);
unsigned long __stdcall GetTempPathW(unsigned long,wchar_t*);
unsigned long __stdcall GetEnvironmentVariableW(const wchar_t*,wchar_t*,unsigned long);
unsigned long __stdcall GetFileAttributesW(const wchar_t*);
unsigned long __stdcall GetCurrentProcessId(void);
unsigned long __stdcall GetTickCount(void);
int __stdcall CreateDirectoryW(const wchar_t*,void*);
int __stdcall RemoveDirectoryW(const wchar_t*);
int __stdcall DeleteFileW(const wchar_t*);
void* __stdcall CreateJobObjectW(void*,const wchar_t*);
int __stdcall SetInformationJobObject(void*,int,void*,unsigned long);
int __stdcall AssignProcessToJobObject(void*,void*);
]]
local K=ffi.load('kernel32')
local M={}
local function wide(value)
    local count=K.MultiByteToWideChar(65001,0,value,#value,nil,0)
    if count==0 then error('无法编码 Windows 路径。') end
    local buffer=ffi.new('wchar_t[?]',count+1)
    K.MultiByteToWideChar(65001,0,value,#value,buffer,count)
    return buffer
end
local function utf8(buffer,count)
    local size=K.WideCharToMultiByte(65001,0,buffer,count,nil,0,nil,nil)
    local output=ffi.new('char[?]',size)
    K.WideCharToMultiByte(65001,0,buffer,count,output,size,nil,nil)
    return ffi.string(output,size)
end
local function quote(value)
    -- Windows CommandLineToArgv/CRT quoting, including trailing backslashes.
    value=value:gsub('(\\*)"',function(s)return s..s..'\\"' end)
    value=value:gsub('(\\+)$',function(s)return s..s end)
    return '"'..value..'"'
end
function M.session()
    local buffer=ffi.new('wchar_t[32768]')
    local count=K.GetTempPathW(32768,buffer)
    if count==0 or count>=32768 then error('无法读取临时目录。') end
    local base=utf8(buffer,count)
    for i=1,100 do
        local path=base..'ass-tracker-'..tonumber(K.GetCurrentProcessId())..'-'..tonumber(K.GetTickCount())..'-'..i
        if K.CreateDirectoryW(wide(path),nil)~=0 then return path end
    end
    error('无法创建追踪会话目录。')
end
function M.local_app_data()
    -- os.getenv returns the ANSI code page; user names may not fit in it.
    local buffer=ffi.new('wchar_t[32768]')
    local count=K.GetEnvironmentVariableW(wide('LOCALAPPDATA'),buffer,32768)
    if count==0 or count>=32768 then error('无法读取 LOCALAPPDATA 目录。') end
    return utf8(buffer,count)
end
local function attributes(path)
    local value=K.GetFileAttributesW(wide(path))
    if value~=0xFFFFFFFF then return value end -- INVALID_FILE_ATTRIBUTES
end
function M.is_file(path)
    local value=attributes(path)
    return value~=nil and bit.band(value,0x10)==0 -- FILE_ATTRIBUTE_DIRECTORY
end
function M.runtime_present(cache_root,version)
    -- Existence only; bootstrap.ps1 still verifies every file hash before launch.
    local directory=cache_root..'/'..version
    return M.is_file(directory..'/runtime.json') and M.is_file(directory..'/ASSTracker.exe')
end
function M.make_directory(path)
    if K.CreateDirectoryW(wide(path),nil)~=0 then return end
    local value=attributes(path)
    if value==nil or bit.band(value,0x10)==0 then error('无法创建目录：'..path) end
end
function M.cleanup(session)
    -- Delete only this session's known files; never recursively delete a path.
    for _,name in ipairs({'job.json','result.json','result.json.tmp','cancel','error.log','progress'}) do
        K.DeleteFileW(wide(session..'/'..name))
    end
    K.RemoveDirectoryW(wide(session))
end
function M.spawn(executable,args,cwd)
    local command={quote(executable)}
    for _,value in ipairs(args) do command[#command+1]=quote(value) end
    local start=ffi.new('AT_STARTUP');start.cb=ffi.sizeof(start)
    local process=ffi.new('AT_PROCESS')
    -- Start suspended so the owned process tree is attached before any decode.
    if K.CreateProcessW(wide(executable),wide(table.concat(command,' ')),nil,nil,0,0x08000004,nil,wide(cwd),start,process)==0 then
        error('无法启动 Python（Windows 错误 '..tonumber(K.GetLastError())..'）。请重新运行 install.ps1。')
    end
    local job=K.CreateJobObjectW(nil,nil)
    local limits=ffi.new('AT_JOB_LIMITS');limits.basic.flags=0x2000 -- KILL_ON_JOB_CLOSE
    if job==nil or K.SetInformationJobObject(job,9,limits,ffi.sizeof(limits))==0 or K.AssignProcessToJobObject(job,process.process)==0 then
        local code=tonumber(K.GetLastError())
        K.TerminateProcess(process.process,1);K.CloseHandle(process.thread);K.CloseHandle(process.process)
        if job~=nil then K.CloseHandle(job) end
        error('无法管理 Python 子进程（Windows 错误 '..code..'）。')
    end
    K.ResumeThread(process.thread);K.CloseHandle(process.thread)
    return {handle=process.process,job=job}
end
function M.poll(process,milliseconds)
    local result=K.WaitForSingleObject(process.handle,milliseconds or 100)
    if result==258 then return nil end -- WAIT_TIMEOUT
    if result~=0 then error('等待 Python 进程失败。') end
    local code=ffi.new('unsigned long[1]')
    if K.GetExitCodeProcess(process.handle,code)==0 then error('无法读取 Python 退出状态。') end
    return tonumber(code[0])
end
function M.close(process)
    if process.job then K.CloseHandle(process.job);process.job=nil end
    if process.handle then K.CloseHandle(process.handle);process.handle=nil end
end
function M.start(root,session,version,setup)
    local powershell=(os.getenv('SystemRoot') or 'C:/Windows')..'/System32/WindowsPowerShell/v1.0/powershell.exe'
    local args={'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',root..'/bootstrap.ps1',
                '-Version',version,'-SessionDir',session}
    if setup and setup.cache_root then args[#args+1]='-CacheRoot';args[#args+1]=setup.cache_root end
    -- An empty argv value is never passed; bootstrap defaults to GitHub itself.
    if setup and setup.mirror and setup.mirror~='' then args[#args+1]='-Mirror';args[#args+1]=setup.mirror end
    return M.spawn(powershell,args,root)
end
return M
