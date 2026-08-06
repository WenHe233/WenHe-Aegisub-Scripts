-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe

--[[
  套用平移 (\move)

  痛点：画面平移期间屏幕上有多行特效字，每行都要做 \move 让文字随镜头移动。
  虽然每行起点不同，但平移的方向、距离、时间都一样，逐行手做很繁琐。

  用法：
    1. 在其中一行做好 \move 当「参考行」（含或不含 t1,t2 时间）。
    2. 选中这一行 + 其它目标行（顺序、位置随意）。
    3. 运行「自动化 → 套用平移 (\move)」。
       脚本读出参考行的位移 (dx,dy) 与时间 (t1,t2)，对每个目标行用它自己的当前
       位置当起点，套上方向/距离/时间完全相同的 \move。参考行本身不动。

  目标行起点取值优先级：行内 \move 的起点 > \pos > 按样式默认位置(对齐+边距+分辨率)。
]]

script_name        = "套用平移 (\\move)"
script_description = "把参考行的 \\move 位移(方向/距离)与时间套用到其它选中行"
script_author      = "WenHe"
script_version     = "1.0.0"
script_namespace   = "wenhe.ApplyMove"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local DEPENDENCY_CONTROL_FEED = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json"
local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local dependency_record
if has_dependency_control then
	local initialized, record = pcall(DependencyControl, { feed = DEPENDENCY_CONTROL_FEED })
	if initialized then dependency_record = record end
end

include("karaskel.lua")

-- 格式化数字：整数不带小数；小数最多 3 位并去掉尾随 0
local function fmt(n)
	if math.abs(n - math.floor(n + 0.5)) < 1e-6 then
		return string.format("%d", math.floor(n + 0.5))
	end
	return (string.format("%.3f", n):gsub("%.?0+$", ""))
end

-- 把 "a, b, c" 形式的参数串解析成数字表
local function parse_params(inner)
	local t = {}
	for s in inner:gmatch("[^,]+") do
		t[#t + 1] = tonumber((s:gsub("%s+", "")))
	end
	return t
end

-- 取出某标签括号内的参数，如 get_tag_params(text, "move") / "pos"
local function get_tag_params(text, tag)
	local inner = text:match("\\" .. tag .. "%s*%((.-)%)")
	if not inner then return nil end
	return parse_params(inner)
end

-- 小弹窗提示并终止
local function stop(msg)
	aegisub.dialog.display({ { class = "label", label = msg } }, { "确定" })
	aegisub.cancel()
end

local function apply_move(subs, sel)
	if #sel < 2 then
		stop("请至少选中 2 行：1 个参考行(已做好 \\move) + 1 个或多个目标行。")
	end

	local meta, styles = karaskel.collect_head(subs)

	-- 找参考行：选区中第一个含 \move 的行
	local refIdx, refMove
	for _, i in ipairs(sel) do
		local p = get_tag_params(subs[i].text, "move")
		if p and #p >= 4 then
			refIdx, refMove = i, p
			break
		end
	end
	if not refIdx then
		stop("未找到带 \\move 的参考行，请先在一行上做好 \\move 再运行。")
	end

	local dx = refMove[3] - refMove[1]
	local dy = refMove[4] - refMove[2]
	local timing = ""
	if #refMove >= 6 then
		timing = "," .. fmt(refMove[5]) .. "," .. fmt(refMove[6])
	end

	local count = 0
	for _, i in ipairs(sel) do
		if i ~= refIdx then
			local line = subs[i]

			-- 起点：行内 \move 起点 > \pos > 样式默认位置
			local sx, sy
			local mp = get_tag_params(line.text, "move")
			local pp = get_tag_params(line.text, "pos")
			if mp and #mp >= 2 then
				sx, sy = mp[1], mp[2]
			elseif pp and #pp >= 2 then
				sx, sy = pp[1], pp[2]
			else
				karaskel.preproc_line(subs, meta, styles, line)
				sx, sy = line.x, line.y
			end

			local ex, ey = sx + dx, sy + dy
			local movetag = "\\move(" .. fmt(sx) .. "," .. fmt(sy) ..
				"," .. fmt(ex) .. "," .. fmt(ey) .. timing .. ")"

			-- 去掉旧的 \move / \pos
			local t = line.text:gsub("\\move%s*%b()", ""):gsub("\\pos%s*%b()", "")

			-- 插入新 \move：行首已是覆写块就插进去，否则新建一个块
			local pre = t:match("^%s*{")
			if pre then
				t = pre .. movetag .. t:sub(#pre + 1)
			else
				t = "{" .. movetag .. "}" .. t
			end

			line.text = t
			subs[i] = line
			count = count + 1
		end
	end

	aegisub.set_undo_point(script_name)
	aegisub.log(4, "套用平移：已处理 %d 行（dx=%s, dy=%s%s）。\n",
		count, fmt(dx), fmt(dy), timing ~= "" and (" t=" .. timing:sub(2)) or "")
end

if dependency_record then
	dependency_record:registerMacro(apply_move)
else
	aegisub.register_macro(script_name, script_description, apply_move)
end
