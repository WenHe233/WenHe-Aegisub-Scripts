-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe

--[[
  复制选中行正文

  提供两个可以分别绑定快捷键的宏：
    · 纯文本：移除所有完整的 {...} ASS 覆写块。
    · 原始 Text 字段：完整保留每行的 line.text。

  多行按字幕网格顺序用换行连接；成功复制时不显示对话框，也不修改字幕。
]]

script_name        = "复制选中行正文"
script_description = "将选中行的纯文本正文或原始 Text 字段复制到剪贴板"
script_author      = "WenHe"
script_version     = "1.0.0"
script_namespace   = "wenhe.CopySelectedText"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local DEPENDENCY_CONTROL_FEED = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json"
local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local dependency_record
if has_dependency_control then
	local initialized, record = pcall(DependencyControl, { feed = DEPENDENCY_CONTROL_FEED })
	if initialized then dependency_record = record end
end

local clipboard
for _, module_name in ipairs({ "aegisub.clipboard", "clipboard" }) do
	local loaded, module = pcall(require, module_name)
	if loaded and type(module) == "table" and type(module.set) == "function" then
		clipboard = module
		break
	end
end

local function strip_override_blocks(text)
	return (text:gsub("{[^}]*}", ""))
end

local function collect_selected_text(subs, sel, transform)
	local ordered = {}
	for i, line_index in ipairs(sel) do ordered[i] = line_index end
	table.sort(ordered)

	local lines = {}
	for _, line_index in ipairs(ordered) do
		local text = subs[line_index].text or ""
		lines[#lines + 1] = transform and transform(text) or text
	end
	return table.concat(lines, "\n")
end

local function copy_to_clipboard(text)
	if not clipboard then
		aegisub.dialog.display({
			{ class = "label", label = "当前 Aegisub 缺少可用的剪贴板模块。" },
		}, { "确定" })
		return
	end

	local succeeded, result = pcall(clipboard.set, text)
	if not succeeded or not result then
		aegisub.dialog.display({
			{ class = "label", label = "无法写入系统剪贴板，请稍后重试。" },
		}, { "确定" })
	end
end

local function copy_plain_text(subs, sel)
	copy_to_clipboard(collect_selected_text(subs, sel, strip_override_blocks))
end

local function copy_raw_text(subs, sel)
	copy_to_clipboard(collect_selected_text(subs, sel))
end

local function validate(_, sel)
	return #sel > 0
end

local macros = {
	{
		name = "纯文本",
		description = "移除 ASS 覆写块后，将选中行正文复制到剪贴板",
		process = copy_plain_text,
	},
	{
		name = "原始 Text 字段",
		description = "将选中行的原始 Text 字段完整复制到剪贴板",
		process = copy_raw_text,
	},
}

for _, macro in ipairs(macros) do
	if dependency_record then
		dependency_record:registerMacro(
			macro.name, macro.description, macro.process, validate, nil, true
		)
	else
		aegisub.register_macro(
			script_name .. "/" .. macro.name,
			macro.description,
			macro.process,
			validate
		)
	end
end
