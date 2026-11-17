-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe

script_name        = "坐标取整 (\\pos)"
script_description = "将选中行的 \\pos 坐标四舍五入为整数或最接近的 5 的倍数"
script_author      = "WenHe"
script_version     = "1.0.0"
script_namespace   = "wenhe.RoundPos"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local dependency_record
if has_dependency_control then
    local initialized, record = pcall(DependencyControl, {
        feed = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json",
    })
    if initialized then dependency_record = record end
end

local function parse_coordinate(value)
    value = value:match("^%s*(.-)%s*$")
    if not (value:match("^[+-]?%d+%.?%d*$") or value:match("^[+-]?%.%d+$")) then return nil end
    local number = tonumber(value)
    if not number or number == math.huge or number == -math.huge then return nil end
    return number
end

-- Halfway values round away from zero, for both integers and multiples of five.
local function round_coordinate(value, step)
    local rounded = math.floor(math.abs(value) / step + 0.5) * step
    if rounded == 0 then return "0" end
    if value < 0 then rounded = -rounded end
    return string.format("%.0f", rounded)
end

local function make_rounder(step, name)
    return function(subs, sel, active)
        local changed = false
        for _, i in ipairs(sel) do
            local line = subs[i]
            if line.class == "dialogue" then
                -- Only complete override blocks contain ASS tags; leave literal text alone.
                local text = line.text:gsub("{[^{}]*}", function(block)
                    return (block:gsub("(\\pos%s*%()([^()]*)(%))", function(prefix, params, suffix)
                        local x, y = params:match("^([^,]*),([^,]*)$")
                        if not x then return nil end
                        x, y = parse_coordinate(x), parse_coordinate(y)
                        if not x or not y then return nil end
                        return prefix .. round_coordinate(x, step) .. "," .. round_coordinate(y, step) .. suffix
                    end))
                end)
                if text ~= line.text then
                    line.text = text
                    subs[i] = line
                    changed = true
                end
            end
        end
        if changed then aegisub.set_undo_point(script_name .. "/" .. name) end
        return sel, active
    end
end

local function validate(_, sel)
    return #sel > 0
end

local macros = {
    {name = "四舍五入取整", description = "将选中行的 \\pos 坐标四舍五入为整数", step = 1},
    {name = "四舍五入取 5 的倍数", description = "将选中行的 \\pos 坐标四舍五入为最接近的 5 的倍数", step = 5},
}

for _, macro in ipairs(macros) do
    local process = make_rounder(macro.step, macro.name)
    if dependency_record then
        dependency_record:registerMacro(macro.name, macro.description, process, validate, nil, true)
    else
        aegisub.register_macro(script_name .. "/" .. macro.name, macro.description, process, validate)
    end
end
