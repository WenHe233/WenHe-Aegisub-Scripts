-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe

script_name        = "批量替换样式"
script_description = "检测选中行的样式，逐一选择目标样式并批量替换"
script_author      = "WenHe"
script_version     = "1.0.0"
script_namespace   = "wenhe.ReplaceStyles"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local dependency_record
if has_dependency_control then
    local initialized, record = pcall(DependencyControl, {
        feed = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json",
    })
    if initialized then dependency_record = record end
end

local function replace_styles(subs, sel, active)
    local targets, available = {}, {}
    for i = 1, #subs do
        local line = subs[i]
        if line.class == "style" and not available[line.name] then
            available[line.name] = true
            targets[#targets + 1] = line.name
        end
    end
    table.sort(targets)

    local sources, counts, originals = {}, {}, {}
    local ordered = {}
    for _, i in ipairs(sel) do ordered[#ordered + 1] = i end
    table.sort(ordered)
    for _, i in ipairs(ordered) do
        local line = subs[i]
        if line.class == "dialogue" and not originals[i] then
            originals[i] = line.style
            if not counts[line.style] then
                sources[#sources + 1] = line.style
                counts[line.style] = 0
            end
            counts[line.style] = counts[line.style] + 1
        end
    end
    if #sources == 0 then return sel, active end
    if #targets == 0 then
        aegisub.dialog.display({{class = "label", label = "当前字幕没有可用的样式，请先创建样式。"}}, {"确定"})
        return sel, active
    end

    -- Remember choices across pages; do not write any lines before final confirmation.
    local mapping = {}
    for _, source in ipairs(sources) do mapping[source] = source end
    local page, page_size = 1, 12
    local pages = math.ceil(#sources / page_size)
    while true do
        local dialog = {
            {class = "label", x = 0, y = 0, width = 2, height = 1,
             label = string.format("为选中行选择目标样式（第 %d/%d 页）。默认保持原样。", page, pages)},
            {class = "label", x = 0, y = 1, width = 1, height = 1, label = "原样式（选中行数）"},
            {class = "label", x = 1, y = 1, width = 1, height = 1, label = "替换为"},
        }
        local first = (page - 1) * page_size + 1
        local last = math.min(page * page_size, #sources)
        for n = first, last do
            local source = sources[n]
            local items = {}
            -- Undefined source styles may be preserved, but cannot be assigned to other styles.
            if not available[source] then items[#items + 1] = source end
            for _, target in ipairs(targets) do items[#items + 1] = target end
            dialog[#dialog + 1] = {class = "label", x = 0, y = n - first + 2,
                width = 1, height = 1, label = string.format("%s（%d 行）", source, counts[source])}
            dialog[#dialog + 1] = {class = "dropdown", x = 1, y = n - first + 2,
                width = 1, height = 1, name = "target_" .. n, items = items, value = mapping[source]}
        end
        local buttons = {"应用全部"}
        if page > 1 then buttons[#buttons + 1] = "上一页" end
        if page < pages then buttons[#buttons + 1] = "下一页" end
        buttons[#buttons + 1] = "取消"
        local button, result = aegisub.dialog.display(dialog, buttons, {ok = "应用全部", cancel = "取消"})
        if not button or button == "取消" then return sel, active end
        for n = first, last do
            local source, target = sources[n], result["target_" .. n]
            if target == source or available[target] then mapping[source] = target end
        end
        if button == "应用全部" then break end
        if button == "上一页" then page = page - 1 end
        if button == "下一页" then page = page + 1 end
    end

    local changed = false
    for _, i in ipairs(ordered) do
        local source = originals[i]
        if source and mapping[source] ~= source then
            local line = subs[i]
            line.style = mapping[source]
            subs[i] = line
            changed = true
        end
    end
    if changed then aegisub.set_undo_point(script_name) end
    return sel, active
end

local function validate(_, sel)
    return #sel > 0
end

if dependency_record then
    dependency_record:registerMacro(replace_styles, validate)
else
    aegisub.register_macro(script_name, script_description, replace_styles, validate)
end
