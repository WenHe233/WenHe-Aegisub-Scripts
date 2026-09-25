-- SPDX-License-Identifier: GPL-3.0-only
local source = debug.getinfo(1,'S').source
if source:sub(1,1)=='@' then source=source:sub(2) end
local directory = source:gsub('%.lua$','')
local f = assert(io.open(directory..'/VERSION','rb'),'ASS 追踪版本文件缺失，请重新安装。')
local version = f:read('*a'):match('^%s*(%d+%.%d+%.%d+)'); f:close()
assert(version,'ASS 追踪版本文件无效。')
local M = {version_string=version, directory=directory, json=require('wenhe.ASSTracker.json')}
local available, DependencyControl = pcall(require,'l0.DependencyControl')
if available then
    M.version = DependencyControl{
        name='ASS 追踪联动组件',description='WenHe ASS Tracker 的 JSON、进程与运行包管理',
        author='WenHe',moduleName='wenhe.ASSTracker',version=version,
        url='https://github.com/WenHe233/WenHe-Aegisub-Scripts',
        feed='https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json'
    }
    return M.version:register(M)
end
M.version = version
return M
