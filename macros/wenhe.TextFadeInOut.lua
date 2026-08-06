-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe

--[[
============================================================
  文字渐入渐出                                      v2.1.0
============================================================

  用一叠"条带 clip + 固定 alpha"模拟带柔和边缘的擦除，
  实现文字 / 绘图的渐隐出现与消失。

  原理：
    把过渡带切成 N 条，每条一个副本、一个固定 alpha，
    整叠带子沿方向平移。矩形 clip 可以被 \t 动画，
    所以每条只需一行、一个 \t，不必逐帧。

  v2.1 新增：
    · 可复用行内 \fad / \fade 的淡入 / 淡出时间作为擦除时长，
      被复用的那一半会从 \fad 中清零，另一半原样保留。
      例：\fad(200,200) 勾选复用淡入 → 出现擦除 200ms，
          标签改写为 \fad(0,200)，淡出仍由 \fad 负责。

  v2 相对 v1 的修正：
    · 自己解析位置，不再依赖 karaskel（karaskel 不看覆写标签）
      现已支持 \pos \move \an \a \org
    · 测量时应用首块的 \fn \fs \b \i \fscx \fscy \fsp
    · 支持 \N \n 多行
    · \bord \xbord \ybord \shad \xshad \yshad \blur \be
      的覆写值计入边距
    · \frz 旋转后取外接矩形
    · \p 绘图模式从绘图坐标算 bbox
    · 修复 strip 会误删 \t() 内部 clip 的 bug
    · 对无法静态求解的情况给出警告

  仍然无法处理（会警告）：
    · \frx \fry 透视变形
    · \t() 内改变几何的标签（bbox 随时间变化）
    · 只支持四个正交方向：矩形 clip 是轴对齐的，
      斜向擦除需要矢量 clip，而矢量 clip 不能被 \t 动画
    · clip 是屏幕坐标，\move 取起止两点的并集作为范围框

  想要像素级精确的 bbox，正路是接 SubInspector 模块
  （lyger.GradientEverything 等脚本走的就是这条）。
  本脚本用解析法，覆盖绝大多数常规情况。

  任何情况下，行内已有的矩形 \clip 都会被优先当作范围框。
  这是最可靠的兜底手段。

============================================================
]]

script_name        = "文字渐入渐出"
script_description = "用分层 clip 生成带柔和边缘的文字渐入 / 渐出效果"
script_author      = "WenHe"
script_version     = "2.1.0"
script_namespace   = "wenhe.TextFadeInOut"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local DEPENDENCY_CONTROL_FEED = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json"
local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local dependency_record
if has_dependency_control then
    local initialized, record = pcall(DependencyControl, { feed = DEPENDENCY_CONTROL_FEED })
    if initialized then dependency_record = record end
end

include("karaskel.lua")

-- ============================================================
-- 常量与小工具
-- ============================================================

local DIR_LABELS = { "从左到右", "从右到左", "从上到下", "从下到上" }

local function dirkey(lbl)
    if     lbl == "从左到右" then return "l2r"
    elseif lbl == "从右到左" then return "r2l"
    elseif lbl == "从上到下" then return "t2b"
    else                          return "b2t" end
end

local function is_horizontal(dk) return dk == "l2r" or dk == "r2l" end

local function clamp(v, lo, hi)
    if v < lo then return lo elseif v > hi then return hi else return v end
end

local function alpha_of(color)
    local a = tostring(color):match("&[Hh](%x%x)%x*&")
    return a and tonumber(a, 16) or 0
end

-- 取最后一次匹配（同名标签后面的覆盖前面的）
local function last_match(s, pat)
    local v
    for m in s:gmatch(pat) do v = m end
    return v
end

local function num_tag(block, name)
    local v = last_match(block, "\\" .. name .. "%s*([%-%d%.]+)")
    return v and tonumber(v) or nil
end

-- 拆出首个覆写块与其余文本
local function split_head(text)
    local head, tail = text:match("^{(.-)}(.*)$")
    if head then return head, tail end
    return "", text
end

-- ============================================================
-- 标签剥离（保护 \t() 内部不被误伤）
-- ============================================================

local function strip_first(block)
    local saved = {}
    block = block:gsub("\\t%b()", function(s)
        saved[#saved + 1] = s
        return "\1" .. #saved .. "\2"
    end)
    block = block:gsub("\\i?clip%b()", "")
    block = block:gsub("\\alpha%s*&[Hh]%x%x&", "")
    block = block:gsub("\\[1234]a%s*&[Hh]%x%x&", "")
    block = block:gsub("\1(%d+)\2", function(n) return saved[tonumber(n)] end)
    return block
end

-- ============================================================
-- \fad / \fade 复用
-- ============================================================
--
--  \fad(a,b)  等价于 \fade(255,0,255, 0,a, dur-b,dur)
--  淡入段：delay = 0,       dur = a
--  淡出段：lead  = 0,       dur = b
--
--  \fade(a1,a2,a3,t1,t2,t3,t4)
--  淡入段：delay = t1,      dur = t2 - t1
--  淡出段：lead  = dur - t4, dur = t4 - t3
--
local function parse_fad(head, line_dur)
    local a, b = head:match("\\fad%(%s*([%d%.]+)%s*,%s*([%d%.]+)%s*%)")
    if a then
        return { kind = "fad",
                 inn  = { off = 0, dur = tonumber(a) },
                 outt = { off = 0, dur = tonumber(b) } }
    end
    local f = head:match("\\fade%b()")
    if f then
        local n = {}
        for v in f:gmatch("[%-%d%.]+") do n[#n + 1] = tonumber(v) end
        if #n >= 7 then
            return { kind = "fade", nums = n,
                     inn  = { off = n[4],            dur = n[5] - n[4] },
                     outt = { off = line_dur - n[7], dur = n[7] - n[6] } }
        end
    end
    return nil
end

-- 把被复用掉的那一半从标签中抹平，另一半原样保留
local function neutralize_fad(head, info, use_in, use_out)
    if info.kind == "fad" then
        local a = use_in  and 0 or info.inn.dur
        local b = use_out and 0 or info.outt.dur
        local repl = (a == 0 and b == 0) and ""
                     or string.format("\\fad(%d,%d)", a, b)
        return (head:gsub("\\fad%(%s*[%d%.]+%s*,%s*[%d%.]+%s*%)",
                          function() return repl end, 1))
    else
        -- \fade 用把起止 alpha 拉平的方式中和，避免 t 相等造成的边界情况
        local n  = info.nums
        local a1 = use_in  and n[2] or n[1]
        local a3 = use_out and n[2] or n[3]
        local repl = string.format("\\fade(%d,%d,%d,%d,%d,%d,%d)",
                                   a1, n[2], a3, n[4], n[5], n[6], n[7])
        return (head:gsub("\\fade%b()", function() return repl end, 1))
    end
end

-- ============================================================
-- alpha
-- ============================================================

local function base_alphas(styleref, head)
    local ba = {
        alpha_of(styleref.color1), alpha_of(styleref.color2),
        alpha_of(styleref.color3), alpha_of(styleref.color4),
    }
    local all = last_match(head, "\\alpha%s*&[Hh](%x%x)&")
    if all then
        local v = tonumber(all, 16)
        for k = 1, 4 do ba[k] = v end
    end
    for k = 1, 4 do
        local m = last_match(head, "\\" .. k .. "a%s*&[Hh](%x%x)&")
        if m then ba[k] = tonumber(m, 16) end
    end
    return ba
end

-- 与基础 alpha 做乘法合成：可见度 = (255 - A) / 255
local function compound_alpha(ba, w)
    local o = {}
    for k = 1, 4 do
        local v = 255 - (255 - ba[k]) * (255 - w) / 255
        o[k] = clamp(math.floor(v + 0.5), 0, 255)
    end
    return string.format("\\1a&H%02X&\\2a&H%02X&\\3a&H%02X&\\4a&H%02X&",
                         o[1], o[2], o[3], o[4])
end

-- ============================================================
-- 范围框计算
-- ============================================================

-- 旧式 \a 到 \an 的映射
local A_LEGACY = { [1]=1,[2]=2,[3]=3, [5]=7,[6]=8,[7]=9, [9]=4,[10]=5,[11]=6 }

local function get_align(head, styleref)
    local an = num_tag(head, "an")
    if an and an >= 1 and an <= 9 then return math.floor(an) end
    local a = num_tag(head, "a")
    if a and A_LEGACY[math.floor(a)] then return A_LEGACY[math.floor(a)] end
    return styleref.align
end

-- 按首块的字体相关标签造一个临时样式，用于测量
local function measuring_style(styleref, head)
    local st = table.copy(styleref)
    st.fontname = last_match(head, "\\fn([^\\}]*)") or st.fontname
    st.fontsize = num_tag(head, "fs")   or st.fontsize
    st.scale_x  = num_tag(head, "fscx") or st.scale_x
    st.scale_y  = num_tag(head, "fscy") or st.scale_y
    st.spacing  = num_tag(head, "fsp")  or st.spacing
    local b = num_tag(head, "b"); if b then st.bold   = (b ~= 0) end
    local i = num_tag(head, "i"); if i then st.italic = (i ~= 0) end
    return st
end

-- 描边 / 阴影 / 模糊造成的外扩
local function decoration_pad(styleref, head)
    local bord = num_tag(head, "bord")
    if not bord then
        local xb, yb = num_tag(head, "xbord"), num_tag(head, "ybord")
        if xb or yb then bord = math.max(xb or 0, yb or 0) end
    end
    bord = bord or styleref.outline or 0

    local shad = num_tag(head, "shad")
    if not shad then
        local xs, ys = num_tag(head, "xshad"), num_tag(head, "yshad")
        if xs or ys then shad = math.max(math.abs(xs or 0), math.abs(ys or 0)) end
    end
    shad = shad or styleref.shadow or 0

    local blur = num_tag(head, "blur") or 0
    local be   = num_tag(head, "be")   or 0

    -- 高斯模糊的可见外扩约 2.5 倍 sigma；\be 每次约 1px
    return bord + shad + blur * 2.5 + be
end

-- 文本测量：处理 \N \n 换行
local function measure(st, plain)
    local rows = {}
    for seg in (plain .. "\\N"):gmatch("(.-)\\[Nn]") do rows[#rows + 1] = seg end
    if #rows == 0 then rows = { plain } end

    local w, h, lead = 0, 0, 0
    for _, seg in ipairs(rows) do
        local rw, rh, _, rl = aegisub.text_extents(st, seg)
        if rw > w    then w    = rw end
        if rh > h    then h    = rh end
        if rl > lead then lead = rl end
    end
    return w, h * #rows + lead * math.max(#rows - 1, 0)
end

-- 绘图模式：从绘图坐标求相对锚点的范围
local function drawing_extents(text, scale)
    local body = text:gsub("{.-}", " ")
    local nums = {}
    for n in body:gmatch("[%-%d%.]+") do nums[#nums + 1] = tonumber(n) end
    local x0, y0, x1, y1
    for k = 1, #nums - 1, 2 do
        local x, y = nums[k], nums[k + 1]
        x0 = (not x0 or x < x0) and x or x0
        x1 = (not x1 or x > x1) and x or x1
        y0 = (not y0 or y < y0) and y or y0
        y1 = (not y1 or y > y1) and y or y1
    end
    if not x0 then return nil end
    local d = 2 ^ (scale - 1)
    -- 渲染器把绘图的"字形范围"从原点算起，所以并入 (0,0)
    return math.min(x0, 0) / d, math.min(y0, 0) / d,
           math.max(x1, 0) / d, math.max(y1, 0) / d
end

-- 由锚点 + 对齐 + 尺寸求左上角
local function anchor_to_topleft(px, py, an, w, h)
    local col = (an - 1) % 3               -- 0 左 1 中 2 右
    local row = math.floor((an - 1) / 3)   -- 0 下 1 中 2 上
    local left = px - w * col / 2
    local top
    if     row == 2 then top = py
    elseif row == 1 then top = py - h / 2
    else                 top = py - h end
    return left, top
end

-- 无 \pos 时，由边距与对齐求锚点
local function margin_anchor(line, styleref, an, resx, resy)
    local ml = (line.margin_l ~= 0) and line.margin_l or styleref.margin_l
    local mr = (line.margin_r ~= 0) and line.margin_r or styleref.margin_r
    local mt = (line.margin_t ~= 0) and line.margin_t or styleref.margin_t
    local mb = (line.margin_b ~= 0) and line.margin_b or styleref.margin_b

    local col = (an - 1) % 3
    local row = math.floor((an - 1) / 3)

    local px
    if     col == 0 then px = ml
    elseif col == 1 then px = (ml + (resx - mr)) / 2
    else                 px = resx - mr end

    local py
    if     row == 2 then py = mt
    elseif row == 1 then py = resy / 2
    else                 py = resy - mb end

    return px, py
end

-- 绕 org 旋转四角后取外接矩形
local function rotate_bbox(bb, frz, ox, oy)
    local th = math.rad(frz)
    local c, s = math.cos(th), math.sin(th)
    local xs, ys = {}, {}
    for _, p in ipairs({ {bb.x0,bb.y0}, {bb.x1,bb.y0}, {bb.x0,bb.y1}, {bb.x1,bb.y1} }) do
        local dx, dy = p[1] - ox, p[2] - oy
        xs[#xs + 1] = ox + dx * c + dy * s
        ys[#ys + 1] = oy - dx * s + dy * c
    end
    local function mn(t) local v = t[1] for _,x in ipairs(t) do if x < v then v = x end end return v end
    local function mx(t) local v = t[1] for _,x in ipairs(t) do if x > v then v = x end end return v end
    return { x0 = mn(xs), y0 = mn(ys), x1 = mx(xs), y1 = mx(ys) }
end

-- 主入口：返回 bbox 与警告集合
local function compute_bbox(line, styleref, resx, resy, user_pad)
    local head, tail = split_head(line.text)
    local warn = {}

    -- 1) 显式矩形 clip 优先
    local cx0, cy0, cx1, cy1 = head:match(
        "\\clip%(%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*%)")
    if cx0 then
        return { x0 = tonumber(cx0), y0 = tonumber(cy0),
                 x1 = tonumber(cx1), y1 = tonumber(cy1) }, warn
    end

    -- 2) 各类警告
    if line.text:match("\\i?clip%b()") then
        warn["行内 \\clip 位于 \\t 中或非首块：无法当范围框，且会与本效果冲突"] = true
    end
    if line.text:match("\\fr[xy]") then
        warn["含 \\frx / \\fry 透视变形，范围框只能估算，建议手动画矩形 clip"] = true
    end
    for t in line.text:gmatch("\\t%b()") do
        if t:match("\\fsc") or t:match("\\fs[%d%.]") or t:match("\\frz")
           or t:match("\\bord") or t:match("\\blur") then
            warn["\\t 中含改变几何的标签，范围框随时间变化，请把边距调大"] = true
        end
    end
    if line.text:match("\\r") then
        warn["含 \\r，会重置本效果注入的 alpha，建议换成显式标签"] = true
    end
    if tail:match("\\alpha") or tail:match("\\[1234]a") then
        warn["首块之后仍有 alpha 标签，会覆盖本效果"] = true
    end

    -- 3) 锚点
    local an = get_align(head, styleref)
    local px, py = head:match("\\pos%(%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*%)")
    local moved
    if not px then
        local a, b, c, d = head:match(
            "\\move%(%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)")
        if a then
            px, py = a, b
            moved = { tonumber(c), tonumber(d) }
            warn["含 \\move：范围框取起止两点的并集，clip 不跟随位移"] = true
        end
    end
    if px then px, py = tonumber(px), tonumber(py)
    else       px, py = margin_anchor(line, styleref, an, resx, resy) end

    -- 4) 尺寸
    local bb
    local pscale = num_tag(head, "p")
    if pscale and pscale >= 1 then
        local dx0, dy0, dx1, dy1 = drawing_extents(line.text, pscale)
        if not dx0 then return nil, warn end
        local w, h = dx1 - dx0, dy1 - dy0
        local left, top = anchor_to_topleft(px, py, an, w, h)
        bb = { x0 = left, y0 = top, x1 = left + w, y1 = top + h }
        warn["绘图模式的范围框在不同渲染器下可能有差异，请目视核对"] = true
    else
        local st    = measuring_style(styleref, head)
        local plain = line.text:gsub("{.-}", "")
        local w, h  = measure(st, plain)
        local left, top = anchor_to_topleft(px, py, an, w, h)
        bb = { x0 = left, y0 = top, x1 = left + w, y1 = top + h }
    end

    -- 5) \move 的第二个位置
    if moved then
        local dx, dy = moved[1] - px, moved[2] - py
        bb.x0 = math.min(bb.x0, bb.x0 + dx); bb.x1 = math.max(bb.x1, bb.x1 + dx)
        bb.y0 = math.min(bb.y0, bb.y0 + dy); bb.y1 = math.max(bb.y1, bb.y1 + dy)
    end

    -- 6) 旋转
    local frz = num_tag(head, "frz") or num_tag(head, "fr") or styleref.angle or 0
    if frz ~= 0 then
        local ox = head:match("\\org%(%s*([%-%d%.]+)%s*,")
        local oy = head:match("\\org%(%s*[%-%d%.]+%s*,%s*([%-%d%.]+)%s*%)")
        bb = rotate_bbox(bb, frz, ox and tonumber(ox) or px, oy and tonumber(oy) or py)
        warn["含 \\frz：擦除方向仍沿屏幕轴向，不沿文字基线"] = true
    end

    -- 7) 外扩
    local pad = user_pad + decoration_pad(styleref, head)
    bb.x0 = bb.x0 - pad; bb.y0 = bb.y0 - pad
    bb.x1 = bb.x1 + pad; bb.y1 = bb.y1 + pad

    return bb, warn
end

-- ============================================================
-- 条带生成
-- ============================================================
--
--  e(p) = (L + W) * p          渐变带前缘位置
--  第 i 条覆盖 u ∈ [e - W*i/N, e - W*(i-1)/N]
--
--  出现：u < e-W 已显示(不透明)，u > e 未显示(全透明)
--  消失：u < e-W 已消失(全透明)，u > e 未消失(不透明)
--
local function rectstr(bb, dk, ua, ub)
    local a, b, c, d
    if     dk == "l2r" then a, b, c, d = bb.x0 + ua, bb.y0, bb.x0 + ub, bb.y1
    elseif dk == "r2l" then a, b, c, d = bb.x1 - ub, bb.y0, bb.x1 - ua, bb.y1
    elseif dk == "t2b" then a, b, c, d = bb.x0, bb.y0 + ua, bb.x1, bb.y0 + ub
    else                    a, b, c, d = bb.x0, bb.y1 - ub, bb.x1, bb.y1 - ua end
    return string.format("\\clip(%.2f,%.2f,%.2f,%.2f)", a, b, c, d)
end

local function build_group(bb, dk, W, N, T1, T2, accel, gamma, mode)
    local L   = is_horizontal(dk) and (bb.x1 - bb.x0) or (bb.y1 - bb.y0)
    local E   = L + W
    local BIG = L + 2 * W + 200
    local items = {}

    local function at(p, oa, ob)
        local e = E * p
        return rectstr(bb, dk, e + oa, e + ob)
    end
    local function trans(cf)
        return string.format("\\t(%d,%d,%.3f,%s)", T1, T2, accel, cf)
    end

    for i = 1, N do
        local f = ((i - 0.5) / N) ^ gamma
        local w = (mode == "in") and (255 * (1 - f)) or (255 * f)
        local oa, ob = -W * i / N, -W * (i - 1) / N
        items[#items + 1] = { alpha = w, clip = at(0, oa, ob), trans = trans(at(1, oa, ob)) }
    end

    -- 实心区：一条边固定在画面外，另一条边跟随前缘。
    -- 与条带共用同一时间窗与 accel，接缝不会因缓动不同而错开。
    local c0, c1
    if mode == "in" then
        c0 = rectstr(bb, dk, -BIG, -W)
        c1 = rectstr(bb, dk, -BIG, E - W)
    else
        c0 = rectstr(bb, dk, 0, BIG)
        c1 = rectstr(bb, dk, E, BIG)
    end
    items[#items + 1] = { alpha = 0, clip = c0, trans = trans(c1), is_solid = true }

    return items
end

-- ============================================================
-- 对话框
-- ============================================================

local function make_config(r)
    r = r or {}
    local function g(k, d) if r[k] ~= nil then return r[k] end return d end
    return {
        { class="checkbox", name="do_in", x=0, y=0, width=2,
          label="出现（行首）", value=g("do_in", true) },
        { class="label",    x=2, y=0, label=" 方向 " },
        { class="dropdown", name="dir_in", x=3, y=0, width=2,
          items=DIR_LABELS, value=g("dir_in", DIR_LABELS[1]) },

        { class="label",   x=0, y=1, label="  出现时长 (ms)" },
        { class="intedit", name="t_in", x=1, y=1, min=1, max=100000, value=g("t_in", 500) },
        { class="label",   x=2, y=1, label="  延迟 (ms)" },
        { class="intedit", name="off_in", x=3, y=1, width=2, min=0, max=100000, value=g("off_in", 0) },

        { class="checkbox", name="fad_in", x=1, y=2, width=4,
          label="改用行内 \\fad 的淡入时间（并把它清零）", value=g("fad_in", false) },

        { class="checkbox", name="do_out", x=0, y=4, width=2,
          label="消失（行尾）", value=g("do_out", false) },
        { class="label",    x=2, y=4, label=" 方向 " },
        { class="dropdown", name="dir_out", x=3, y=4, width=2,
          items=DIR_LABELS, value=g("dir_out", DIR_LABELS[1]) },

        { class="label",   x=0, y=5, label="  消失时长 (ms)" },
        { class="intedit", name="t_out", x=1, y=5, min=1, max=100000, value=g("t_out", 500) },
        { class="label",   x=2, y=5, label="  提前 (ms)" },
        { class="intedit", name="off_out", x=3, y=5, width=2, min=0, max=100000, value=g("off_out", 0) },

        { class="checkbox", name="fad_out", x=1, y=6, width=4,
          label="改用行内 \\fad 的淡出时间（并把它清零）", value=g("fad_out", false) },

        { class="label",     x=0, y=8, label="渐隐宽度 (px)" },
        { class="floatedit", name="width", x=1, y=8, min=1, max=4000, step=1, value=g("width", 60) },
        { class="label",     x=2, y=8, label="  每条像素 (px)" },
        { class="floatedit", name="step", x=3, y=8, width=2, min=0.5, max=200, step=0.5, value=g("step", 5) },

        { class="label",     x=0, y=9, label="额外边距 (px)" },
        { class="floatedit", name="pad", x=1, y=9, min=0, max=500, step=1, value=g("pad", 6) },
        { class="label",     x=2, y=9, label="  缓动 accel" },
        { class="floatedit", name="accel", x=3, y=9, width=2, min=0.05, max=10, step=0.05, value=g("accel", 1) },

        { class="label",     x=0, y=10, label="alpha 曲线 gamma" },
        { class="floatedit", name="gamma", x=1, y=10, min=0.1, max=5, step=0.05, value=g("gamma", 1) },
        { class="checkbox",  name="keep", x=2, y=10, width=3,
          label="保留原行（注释）", value=g("keep", true) },

        { class="label", x=0, y=12, width=5,
          label="描边 / 阴影 / \\blur 已自动计入边距，这里只填额外余量。" },
        { class="label", x=0, y=13, width=5,
          label="范围框不确定时，先画一个矩形 \\clip，脚本会优先采用它。" },
    }
end

-- ============================================================
-- 主处理
-- ============================================================

local function process(subs, sel)
    local meta, styles = karaskel.collect_head(subs, false)
    local resx = meta.res_x or 1920
    local resy = meta.res_y or 1080

    local btn, res, saved
    repeat
        btn, res = aegisub.dialog.display(make_config(saved), { "应用", "取消" },
                                          { ok = "应用", cancel = "取消" })
        if not btn then aegisub.cancel() end
        saved = res
        if not res.do_in and not res.do_out then
            aegisub.dialog.display(
                { { class="label", x=0, y=0, label="请至少勾选「出现」或「消失」。" } }, { "好" })
        end
    until res.do_in or res.do_out

    local W      = res.width
    local N      = clamp(math.ceil(W / res.step), 2, 200)
    local dk_in  = dirkey(res.dir_in)
    local dk_out = dirkey(res.dir_out)

    local total, warnings, skipped = 0, {}, 0

    for si = #sel, 1, -1 do
        local idx  = sel[si]
        local line = subs[idx]

        if line.class == "dialogue" and not line.comment then
            karaskel.preproc_line(subs, meta, styles, line)

            local bb, warn = compute_bbox(line, line.styleref, resx, resy, res.pad)
            for k in pairs(warn or {}) do warnings[k] = true end

            if not bb then
                skipped = skipped + 1
            else
                if bb.x1 < bb.x0 then bb.x0, bb.x1 = bb.x1, bb.x0 end
                if bb.y1 < bb.y0 then bb.y0, bb.y1 = bb.y1, bb.y0 end

                local head, tail = split_head(line.text)
                local ba  = base_alphas(line.styleref, head)
                local dur = line.end_time - line.start_time

                -- \fad / \fade 复用
                local d_in,  o_in  = res.t_in,  res.off_in
                local d_out, o_out = res.t_out, res.off_out
                local want_fi = res.fad_in  and res.do_in
                local want_fo = res.fad_out and res.do_out
                if want_fi or want_fo then
                    local fad = parse_fad(head, dur)
                    if fad then
                        local use_fi = want_fi and fad.inn.dur  > 0
                        local use_fo = want_fo and fad.outt.dur > 0
                        if want_fi and not use_fi then
                            warnings["有行的 \\fad 淡入时间为 0，该行改用对话框中的出现时长"] = true
                        end
                        if want_fo and not use_fo then
                            warnings["有行的 \\fad 淡出时间为 0，该行改用对话框中的消失时长"] = true
                        end
                        if use_fi then d_in,  o_in  = fad.inn.dur,  fad.inn.off  end
                        if use_fo then d_out, o_out = fad.outt.dur, fad.outt.off end
                        if use_fi or use_fo then
                            head = neutralize_fad(head, fad, use_fi, use_fo)
                        end
                    else
                        warnings["有行没有 \\fad / \\fade，已改用对话框中的时长"] = true
                    end
                end

                local clean = strip_first(head)

                local T1i = math.floor(o_in)
                local T2i = math.floor(T1i + d_in)
                local T2o = math.floor(dur - o_out)
                local T1o = math.floor(T2o - d_out)
                if T2i <= T1i then T2i = T1i + 1 end
                if T2o <= T1o then T2o = T1o + 1 end
                if res.do_in and res.do_out and T2i >= T1o then
                    warnings["出现与消失的时间窗重叠，请缩短时长"] = true
                end

                local groups = {}
                if res.do_in then
                    groups[#groups+1] = {
                        items = build_group(bb, dk_in, W, N, T1i, T2i, res.accel, res.gamma, "in"),
                        kill  = res.do_out and T1o or nil,
                    }
                end
                if res.do_out then
                    groups[#groups+1] = {
                        items = build_group(bb, dk_out, W, N, T1o, T2o, res.accel, res.gamma, "out"),
                        born  = res.do_in and T2i or nil,
                    }
                end

                local newlines = {}
                for _, grp in ipairs(groups) do
                    for _, it in ipairs(grp.items) do
                        local tags = it.clip .. it.trans
                        if it.is_solid and grp.kill then
                            tags = compound_alpha(ba, it.alpha) .. tags
                                 .. string.format("\\t(%d,%d,\\alpha&HFF&)", grp.kill, grp.kill + 1)
                        elseif it.is_solid and grp.born then
                            tags = "\\alpha&HFF&" .. tags
                                 .. string.format("\\t(%d,%d,%s)", grp.born, grp.born + 1,
                                                  compound_alpha(ba, it.alpha))
                        else
                            tags = compound_alpha(ba, it.alpha) .. tags
                        end
                        local nl = table.copy(line)
                        nl.comment = false
                        nl.effect  = "softwipe"
                        nl.text    = "{" .. tags .. clean .. "}" .. tail
                        newlines[#newlines+1] = nl
                    end
                end

                subs.delete(idx)
                for k = #newlines, 1, -1 do subs.insert(idx, newlines[k]) end
                if res.keep then
                    local orig = table.copy(line)
                    orig.comment = true
                    orig.effect  = "softwipe-src"
                    subs.insert(idx, orig)
                end
                total = total + #newlines
            end
        end
    end

    aegisub.set_undo_point(script_name)

    -- 警告汇总
    local list = {}
    for k in pairs(warnings) do list[#list+1] = k end
    if #list > 0 or skipped > 0 then
        local cfg = { { class="label", x=0, y=0, width=3,
                        label=string.format("已生成 %d 行（每方向 %d 条带 + 1 实心区）", total, N) } }
        local y = 1
        if skipped > 0 then
            cfg[#cfg+1] = { class="label", x=0, y=y, width=3,
                            label=string.format("跳过 %d 行（无法确定范围框）", skipped) }
            y = y + 1
        end
        if #list > 0 then
            cfg[#cfg+1] = { class="label", x=0, y=y, width=3, label="注意事项：" }
            y = y + 1
            for _, m in ipairs(list) do
                cfg[#cfg+1] = { class="label", x=0, y=y, width=3, label="  · " .. m }
                y = y + 1
            end
        end
        aegisub.dialog.display(cfg, { "知道了" })
    end
end

local function validate(subs, sel)
    for _, i in ipairs(sel) do
        if subs[i].class == "dialogue" and not subs[i].comment then return true end
    end
    return false
end

if dependency_record then
    dependency_record:registerMacro(process, validate)
else
    aegisub.register_macro(script_name, script_description, process, validate)
end
