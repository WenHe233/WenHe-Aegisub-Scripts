-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe
script_name = "ASS 追踪"
script_description = "五项联动平面追踪，支持裁切、绘图和缩放外观，自动回填字幕并保留原行。"
script_author = "WenHe"
script_version = "0.4.0"
script_namespace = "wenhe.ASSTracker"
script_url = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local feed = 'https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json'
local available, DependencyControl = pcall(require,'l0.DependencyControl')
local dependency_record, runtime
if available then
    dependency_record = DependencyControl{feed=feed,{{'wenhe.ASSTracker',version=script_version,feed=feed}}}
    runtime = dependency_record:requireModules()
else
    runtime = require('wenhe.ASSTracker')
end
assert(runtime.version_string==script_version,'ASS 追踪宏和组件版本不同，请重新安装匹配版本并重启 Aegisub。')
local json = runtime.json
local snapshot_fields = {"text", "style", "start_time", "end_time", "layer", "actor", "effect", "margin_l", "margin_r", "margin_t", "comment"}

local function fail(message)
    aegisub.dialog.display({{class="label", label=message, x=0, y=0, width=1, height=1}}, {"关闭"})
    aegisub.cancel()
end

local function copy(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for k, v in pairs(value) do result[k] = copy(v) end
    return result
end

local function info(subs)
    local result = {}
    for i=1,#subs do
        local line = subs[i]
        if line.class == "info" then result[line.key:lower()] = line.value end
    end
    return result
end

local function valid_text(text)
    local n = 0
    for block in text:gmatch("{(.-)}") do
        local _, count = block:gsub("\\pos%(", "")
        n = n + count
        if block:match("\\move%(") or block:match("\\t%(") or block:match("\\fad%(") or block:match("\\fade%(")
           or block:match("\\[kK][fo]?%d") or block:match("\\kt%d") or block:match("\\r") then
            return false, "第一版不支持 move、t、淡入淡出、卡拉 OK 或样式重置，请先展开这些效果。"
        end
    end
    return n == 1, "每行必须有一个显式 pos 标签。请先在参考帧做好位置。"
end

local function build_job(subs, selection, linked)
    local styles = {}
    for i=1,#subs do
        if subs[i].class == 'style' then styles[subs[i].name] = copy(subs[i]) end
    end
    local vw, vh = aegisub.video_size()
    if not vw then fail("请先打开对应视频。") end
    local properties = aegisub.project_properties()
    local video = properties.video_file
    if not video or video == "" or video:sub(1,1) == "?" then
        video = aegisub.dialog.open("选择当前加载的原视频", "", "", "视频文件|*.*", false, true)
        if not video then return end
    end
    local first, last = math.huge, -1
    local lines = {}
    for _, index in ipairs(selection) do
        local line = subs[index]
        if line.class ~= "dialogue" or line.comment then fail("只选择需要追踪的非注释字幕行。") end
        if line.end_time <= line.start_time then fail("字幕持续时间必须大于 0。") end
        local ok, why = valid_text(line.text)
        if not ok then fail("第 " .. index .. " 行：" .. why) end
        local a = aegisub.frame_from_ms(line.start_time)
        -- Automation frame_from_ms uses START semantics: first frame whose
        -- presentation time is >= the supplied timestamp. For a half-open
        -- subtitle interval [start,end), that is already the exclusive end.
        -- Using end-1 and adding 1 incorrectly includes the next shot.
        local b = aegisub.frame_from_ms(line.end_time)
        if not a or not b or b <= a then fail("字幕时间内没有可显示的视频帧，请检查起止时间。") end
        first, last = math.min(first, a), math.max(last, b)
        local entry = {index=index, start_frame=a, end_frame=b}
        for _, field in ipairs(snapshot_fields) do entry[field] = line[field] end
        local style = styles[line.style]
        if style then
            entry.style_data = copy(style)
            local measure = copy(style)
            local head = line.text:match('^{(.-)}') or ''
            measure.fontname = head:match('\\fn([^\\]+)') or measure.fontname
            local overrides = {fs='fontsize',fsp='spacing',b='bold',i='italic'}
            for tag,field in pairs(overrides) do
                local value = head:match('\\'..tag..'([%d%.%-]+)')
                if value then
                    if field=='bold' or field=='italic' then measure[field]=tonumber(value)~=0
                    else measure[field]=tonumber(value) end
                end
            end
            measure.scale_x, measure.scale_y = 100,100
            local plain = line.text:gsub('{.-}',''):gsub('\\h',' ')
            if not plain:match('\\[Nn]') and not head:match('\\p[1-9]') and aegisub.text_extents then
                local width,height,descent = aegisub.text_extents(measure,plain)
                entry.metrics = {width=width,height=height,descent=descent}
            end
        end
        lines[#lines+1] = entry
    end
    if #lines == 0 then fail("没有选择字幕。") end
    if last-first > 2400 then fail("第一版每个任务最多 2400 帧，请按镜头分段。") end
    local default_ref = tonumber(properties.video_position) or first
    default_ref = math.max(first, math.min(last-1, default_ref))
    local values = {reference=default_ref}
    if not linked then
    local button
    button, values = aegisub.dialog.display({
        {class="label", label="字幕应已经在参考帧排好位置。所有帧号从 0 开始。", x=0,y=0,width=2},
        {class="label", label="参考帧", x=0,y=1},
        {class="intedit", name="reference", value=default_ref, min=first, max=last-1, x=1,y=1},
        {class="label", label="在追踪窗口中选择平移、缩放、旋转、斜切和透视。中文和白框可一起选择。", x=0,y=2,width=2}
    }, {"导出", "取消"})
    if button ~= '导出' then return end
    end
    for _, line in ipairs(lines) do
        if values.reference < line.start_frame or values.reference >= line.end_frame then
            fail("所有所选行都必须在参考帧可见。不同出现时间的消息请分开导出。")
        end
    end
    local script = info(subs)
    local sw, sh = tonumber(script.playresx), tonumber(script.playresy)
    if not sw or not sh or sw <= 0 or sh <= 0 then fail("请先设置脚本 PlayResX / PlayResY。") end
    local bounds = {}
    for f=first,last do
        -- Same centisecond policy as Aegisub-Motion: use Aegisub's loaded
        -- timecodes rather than a nominal FPS calculation (supports VFR).
        bounds[#bounds+1] = math.floor(math.max(0,aegisub.ms_from_frame(f))/10)*10
        -- A short VFR interval can make rounding down cross a frame. Verify
        -- the centisecond value with the API rather than assume it is safe.
        if aegisub.frame_from_ms(bounds[#bounds]) ~= f then
            bounds[#bounds] = bounds[#bounds] + 10
            if aegisub.frame_from_ms(bounds[#bounds]) ~= f then fail("这一帧没有可用的 ASS 厘秒边界，无法无损拆帧。") end
        end
        if #bounds > 1 and bounds[#bounds] <= bounds[#bounds-1] then fail("帧时间无法以 ASS 厘秒精度表示。") end
    end
    local job = {schema="ass-tracker-job-v1", timing_policy="aegisub-start-exclusive-v2", job_id=tostring(os.time()).."-"..tostring(math.random(100000,999999)),
                 video=video, video_width=vw, video_height=vh, script_width=sw, script_height=sh,
                 start_frame=first, end_frame=last, reference_frame=values.reference, boundaries_ms=bounds, lines=lines,
                 mode='similarity',tool_version=script_version,
                 script_info={scaled_border_and_shadow=script.scaledborderandshadow or 'yes'},
                 move_clips=true,scale_appearance=true,options={search_radius=220,threshold=.85,max_width=960,quantum=.5}}
    return job
end

local function export_job(subs,selection)
    local job=build_job(subs,selection,false)
    if not job then return end
    local path = aegisub.dialog.save("保存追踪任务", "", "track.job.json", "追踪任务|*.job.json", false)
    if not path then return end
    local file, err = io.open(path,"wb")
    if not file then fail("保存失败："..tostring(err)) end
    local ok, encoded = pcall(json.encode, job)
    if not ok then file:close(); fail("任务编码失败："..tostring(encoded)) end
    file:write(encoded);file:close()
    aegisub.dialog.display({{class="label", label="已导出："..path.."\n打开完整包的 ASSTracker.exe（或运行 python -m ass_tracker），加载此任务，框选并追踪。\n计算期间请不要修改这些原行；完成后运行“2. 导入结果”。", x=0,y=0,width=1}}, {"完成"})
end

local function import_result(subs,path,expected_job)
    path = path or aegisub.dialog.open("选择追踪结果", "", "", "追踪结果|*.result.json", false, true)
    if not path then return end
    local file = io.open(path,"rb")
    if not file then fail("无法读取结果文件。") end
    local raw = file:read("*a");file:close()
    if #raw > 64*1024*1024 then fail("结果文件过大。") end
    local ok, result = pcall(json.decode,raw)
    if not ok or type(result) ~= "table" or result.schema ~= "ass-tracker-result-v1" then fail("不是有效的追踪结果。") end
    if result.status ~= "complete" then fail("该结果有失锁帧，不能导入。请先重新追踪。") end
    local job = result.job
    if type(job) ~= "table" or result.job_id ~= job.job_id or type(job.lines) ~= "table" or type(result.generated) ~= "table" then fail("结果结构不完整。") end
    if expected_job then
        if job.job_id~=expected_job.job_id or #job.lines~=#expected_job.lines then fail('返回的结果不属于本次追踪。') end
        for i,line in ipairs(job.lines) do
            if line.index~=expected_job.lines[i].index then fail('返回的字幕范围与本次选择不一致。') end
        end
    end
    local script = info(subs)
    if job.tool_version and job.tool_version ~= script_version then
        fail('结果与当前宏版本不同，请使用配套版本重新追踪。')
    end
    if job.script_info and job.script_info.scaled_border_and_shadow ~= (script.scaledborderandshadow or 'yes') then
        fail('脚本描边／阴影缩放属性在导出后发生变化，请重新导出。')
    end
    if tonumber(script.playresx) ~= job.script_width or tonumber(script.playresy) ~= job.script_height then
        fail("脚本分辨率在导出后发生变化，请重新导出。")
    end
    local vw,vh = aegisub.video_size()
    if not vw or vw ~= job.video_width or vh ~= job.video_height then fail("请加载与任务一致的视频。") end
    local video = aegisub.project_properties().video_file
    if video and video ~= "" and video:gsub("\\","/") ~= job.video:gsub("\\","/") then
        fail("当前视频路径与任务不一致，请重新导出，避免套错视频。")
    end
    local sources, generated = {}, {}
    for _, line in ipairs(job.lines) do
        if line.start_frame ~= aegisub.frame_from_ms(line.start_time) or line.end_frame ~= aegisub.frame_from_ms(line.end_time) then
            fail('任务的帧范围与当前时间码不一致，可能来自旧版导出。请使用新版宏重新导出并追踪。')
        end
        if line.style_data then
            local matched = false
            for i=1,#subs do
                local style = subs[i]
                if style.class=='style' and style.name==line.style then
                    matched = true
                    for key,value in pairs(line.style_data) do
                        if key~='raw' and style[key]~=value then fail('样式在导出后已修改，请重新导出。') end
                    end
                    break
                end
            end
            if not matched then fail('原样式已丢失，请重新导出。') end
        end
        if type(line.index) ~= "number" or sources[line.index] or line.index < 1 or line.index > #subs then fail("原行索引无效。") end
        local current = subs[line.index]
        if current.class ~= "dialogue" then fail("字幕顺序已变化，请重新导出。") end
        for _, field in ipairs(snapshot_fields) do
            if current[field] ~= line[field] then fail("第 "..line.index.." 行已被修改，请重新导出任务。") end
        end
        sources[line.index] = current
        generated[line.index] = {}
    end
    if #result.generated == 0 or #result.generated > 150000 then fail("生成行数异常。") end
    for _, row in ipairs(result.generated) do
        local source = sources[row.source_index]
        if not source or type(row.text) ~= "string" or type(row.start_time) ~= "number" or type(row.end_time) ~= "number" then fail("生成行格式错误。") end
        if row.start_time < source.start_time or row.end_time > source.end_time or row.start_time >= row.end_time then fail("生成行超出原行时间范围。") end
        local valid = valid_text(row.text)
        if not valid then fail("生成行包含不支持的标签。") end
        table.insert(generated[row.source_index],row)
    end
    -- Validate the entire result before making any subtitle mutation.
    local order = {}
    for index, rows in pairs(generated) do
        table.sort(rows,function(a,b)return a.start_time<b.start_time end)
        local cursor = sources[index].start_time
        for _, row in ipairs(rows) do
            if row.start_time ~= cursor then fail("结果有时间缺口或重叠，未应用任何修改。") end
            cursor = row.end_time
        end
        if cursor ~= sources[index].end_time then fail("结果没有覆盖原行全部时间，未应用任何修改。") end
        order[#order+1] = index
    end
    if not expected_job then
        local choice = aegisub.dialog.display({{class="label", label="将生成 "..#result.generated.." 行。原来的 "..#job.lines.." 行保留为注释，可以通过撤销恢复。",x=0,y=0,width=1}}, {"应用", "取消"})
        if choice ~= "应用" then return end
    end
    table.sort(order,function(a,b)return a>b end)
    for _, index in ipairs(order) do
        local original = copy(sources[index])
        original.comment = true
        subs[index] = original
        local rows = generated[index]
        for j=#rows,1,-1 do
            local line = copy(sources[index])
            line.start_time, line.end_time, line.text = rows[j].start_time, rows[j].end_time, rows[j].text
            line.comment = false
            subs.insert(index+1,line)
        end
    end
    aegisub.set_undo_point("ASS 追踪：应用结果")
end

local function linked_track(subs,selection)
    local ok,bridge=pcall(require,'wenhe.ASSTracker.bridge')
    if not ok then fail('无法加载联动组件：'..tostring(bridge)) end
    local job=build_job(subs,selection,true)
    if not job then return end
    local created,session=pcall(bridge.session)
    if not created then fail(tostring(session)) end
    local file,err=io.open(session..'/job.json','wb')
    if not file then bridge.cleanup(session);fail('无法创建追踪任务：'..tostring(err)) end
    file:write(json.encode(job));file:close()
    local started,process=pcall(bridge.start,runtime.directory,session,script_version)
    if not started then bridge.cleanup(session);fail(tostring(process)) end
    aegisub.progress.title('ASS 追踪：准备并打开追踪窗口')
    aegisub.progress.task('首次运行会下载配套环境。窗口打开后框选并追踪，点击“应用并返回 Aegisub”；关闭窗口可取消。')
    local cancelled=false
    local cancel_ticks=0
    while true do
        if aegisub.progress.is_cancelled() then
            if not cancelled then
                local signal=io.open(session..'/cancel','wb')
                if signal then signal:write('cancel');signal:close() end
            end
            cancelled=true
        end
        local polled,code=pcall(bridge.poll,process,100)
        if not polled then bridge.close(process);bridge.cleanup(session);fail(tostring(code)) end
        if cancelled then cancel_ticks=cancel_ticks+1 end
        if code~=nil or cancel_ticks>=50 then
            bridge.close(process) -- also closes any remaining owned FFmpeg child
            if cancelled then bridge.cleanup(session);aegisub.cancel() end
            if code~=0 then
                fail('追踪窗口异常退出，未更改字幕。诊断日志：'..session..'/error.log')
            end
            break
        end
    end
    local result=io.open(session..'/result.json','rb')
    if not result then bridge.cleanup(session);return end -- user closed Python
    result:close()
    aegisub.progress.task('正在将追踪结果应用到字幕…')
    import_result(subs,session..'/result.json',job)
    bridge.cleanup(session)
end

for _,macro in ipairs({{'开始追踪（自动回填）',linked_track},{'1. 导出选中行',export_job},
                       {'2. 导入结果',function(subs)return import_result(subs)end}}) do
    if dependency_record then dependency_record:registerMacro(macro[1],script_description,macro[2],nil,nil,true)
    else aegisub.register_macro(script_name..'/'..macro[1],script_description,macro[2]) end
end
