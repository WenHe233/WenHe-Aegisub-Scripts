-- SPDX-License-Identifier: GPL-3.0-only
-- Copyright (C) 2026 WenHe

script_name        = "字体名称转英文 (\\fn)"
script_description = "将选中行 \\fn 标签中的本地化字体名称替换为已安装字体的英文名称，保留 @ 前缀"
script_author      = "WenHe"
script_version     = "1.0.0"
script_namespace   = "wenhe.EnglishFontName"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"

local has_dependency_control, DependencyControl = pcall(require, "l0.DependencyControl")
local dependency_record
if has_dependency_control then
    local initialized, record = pcall(DependencyControl, {
        feed = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json",
    })
    if initialized then dependency_record = record end
end

-- Load lazily so unsupported hosts can still load the macro and see its explanation.
local function windows_resolver()
    local ffi = require("ffi")
    if ffi.os ~= "Windows" then error("此功能需要 Windows 版 Aegisub（LuaJIT）。", 0) end
    ffi.cdef[[
        void * __stdcall CreateCompatibleDC(void *hdc);
        int __stdcall DeleteDC(void *hdc);
        void * __stdcall CreateFontW(int, int, int, int, int, uint32_t, uint32_t,
            uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, const uint16_t *);
        void * __stdcall SelectObject(void *hdc, void *object);
        int __stdcall DeleteObject(void *object);
        uint32_t __stdcall GetFontData(void *hdc, uint32_t table, uint32_t offset,
            void *buffer, uint32_t size);
        int __stdcall MultiByteToWideChar(uint32_t, uint32_t, const char *, int, uint16_t *, int);
        int __stdcall WideCharToMultiByte(uint32_t, uint32_t, const uint16_t *, int,
            char *, int, const char *, int *);
        int __stdcall CompareStringOrdinal(const uint16_t *, int, const uint16_t *, int, int);
    ]]
    local gdi, kernel = ffi.load("gdi32"), ffi.load("kernel32")

    local function wide(text)
        local length = kernel.MultiByteToWideChar(65001, 8, text, #text, nil, 0)
        if length == 0 then return nil end
        local buffer = ffi.new("uint16_t[?]", length + 1)
        if kernel.MultiByteToWideChar(65001, 8, text, #text, buffer, length) ~= length then return nil end
        return buffer, length
    end

    local function same_name(a, b)
        local wa, la = wide(a)
        local wb, lb = wide(b)
        return wa ~= nil and wb ~= nil and kernel.CompareStringOrdinal(wa, la, wb, lb, 1) == 2
    end

    local function utf16be(text)
        if #text == 0 or #text % 2 ~= 0 then return nil end
        local buffer = ffi.new("uint16_t[?]", #text / 2)
        for i = 1, #text, 2 do buffer[(i - 1) / 2] = text:byte(i) * 256 + text:byte(i + 1) end
        local length = kernel.WideCharToMultiByte(65001, 128, buffer, #text / 2, nil, 0, nil, nil)
        if length == 0 then return nil end
        local result = ffi.new("char[?]", length)
        if kernel.WideCharToMultiByte(65001, 128, buffer, #text / 2, result, length, nil, nil) ~= length then return nil end
        return ffi.string(result, length)
    end

    local function read_table(name)
        local face, length = wide(name)
        -- GDI silently truncates long names and substitutes missing fonts.
        if not face or length > 31 or name:find("%z") then return nil end
        local dc = gdi.CreateCompatibleDC(nil)
        if dc == nil then error("无法创建字体查询设备。", 0) end
        local font = gdi.CreateFontW(0, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, face)
        if font == nil then gdi.DeleteDC(dc); return nil end
        local old = gdi.SelectObject(dc, font)
        local ok, data = pcall(function()
            if old == nil or old == ffi.cast("void *", -1) then return nil end
            local size = tonumber(gdi.GetFontData(dc, 0x656D616E, 0, nil, 0)) -- 'name', little endian
            if size == 0xFFFFFFFF or size < 6 or size > 4 * 1024 * 1024 then return nil end
            local buffer = ffi.new("uint8_t[?]", size)
            if gdi.GetFontData(dc, 0x656D616E, 0, buffer, size) ~= size then return nil end
            return ffi.string(buffer, size)
        end)
        if old ~= nil and old ~= ffi.cast("void *", -1) then gdi.SelectObject(dc, old) end
        gdi.DeleteObject(font)
        gdi.DeleteDC(dc)
        if not ok then error(data, 0) end
        return data
    end

    local function records(data)
        local function u16(pos) return data:byte(pos) * 256 + data:byte(pos + 1) end
        local count, storage = u16(3), u16(5)
        local format = u16(1)
        if format > 1 or storage < 6 + count * 12 or storage > #data then return {} end
        local result = {}
        for i = 0, count - 1 do
            local p = 7 + i * 12
            local platform, encoding, language, id = u16(p), u16(p + 2), u16(p + 4), u16(p + 6)
            local length, offset = u16(p + 8), u16(p + 10)
            if storage + offset + length <= #data and (id == 1 or id == 4 or id == 16 or id == 21) then
                local raw = data:sub(storage + offset + 1, storage + offset + length)
                local text, english
                if platform == 3 and (encoding == 0 or encoding == 1 or encoding == 10) then
                    text = utf16be(raw)
                    english = language < 0x8000 and language % 1024 == 9
                elseif platform == 0 then
                    text = utf16be(raw)
                elseif platform == 1 and encoding == 0 and not raw:find("[^ -~]") then
                    text, english = raw, language == 0
                end
                if text and text ~= "" and not text:find("[%z\1-\31\127\\{}]") then
                    result[#result + 1] = {id = id, text = text, english = english,
                        rank = platform == 3 and (language == 0x409 and 1 or 2) or 3}
                end
            end
        end
        return result
    end

    return function(name)
        local data = read_table(name)
        if not data then return nil end
        local names, matching = records(data), {}
        for _, record in ipairs(names) do
            if same_name(record.text, name) then
                if record.english then return name end
                matching[record.id] = true
            end
        end
        -- Keep the same naming category: a localized full face name must not
        -- collapse to its broader family and accidentally lose its weight.
        for _, id in ipairs({1, 4, 16, 21}) do
            if matching[id] then
                local candidates = {}
                for _, record in ipairs(names) do
                    if record.id == id and record.english then candidates[#candidates + 1] = record end
                end
                table.sort(candidates, function(a, b)
                    if a.rank ~= b.rank then return a.rank < b.rank end
                    return a.text < b.text
                end)
                for _, candidate in ipairs(candidates) do
                    if same_name(candidate.text, name) then return name end
                    -- Also verify that the English alias selects this exact face.
                    if read_table(candidate.text) == data then return candidate.text end
                end
            end
        end
        return nil
    end
end

local function convert_text(text, resolve)
    return (text:gsub("{[^{}]*}", function(block)
        local output, position, depth = {}, 1, 0
        while true do
            local start = block:find("\\", position, true)
            if not start then output[#output + 1] = block:sub(position); break end
            output[#output + 1] = block:sub(position, start - 1)
            local next_tag = block:find("\\", start + 1, true) or #block
            local tag = block:sub(start, next_tag - 1)
            if tag:sub(1, 3) == "\\fn" then
                local value, closing = tag:sub(4), ""
                -- A font name can contain parentheses. Only remove the closing
                -- delimiters belonging to an enclosing transform, from the end.
                while depth > 0 do
                    local body, suffix = value:match("^(.-)(%)%s*)$")
                    if not body then break end
                    value, closing, depth = body, suffix .. closing, depth - 1
                end
                local leading, name, trailing = value:match("^(%s*)(.-)(%s*)$")
                if name ~= "" and name ~= "@" then
                    local prefix = name:sub(1, 1) == "@" and "@" or ""
                    local family = prefix == "@" and name:sub(2) or name
                    local english = resolve(family)
                    if english then tag = "\\fn" .. leading .. prefix .. english .. trailing .. closing end
                end
            else
                for char in tag:gmatch("[()]") do depth = math.max(0, depth + (char == "(" and 1 or -1)) end
            end
            output[#output + 1], position = tag, next_tag
        end
        return table.concat(output)
    end))
end

local function show_message(message)
    aegisub.dialog.display({{class = "label", label = message}}, {"确定"})
end

local function process(subs, sel, active)
    local resolver, cache, missing, pending = nil, {}, {}, {}
    local cancelled = {}
    local ok, reason = pcall(function()
        local function resolve(name)
            if cache[name] == nil then
                if not resolver then resolver = windows_resolver() end
                cache[name] = resolver(name) or false
                if not cache[name] then missing[#missing + 1] = name end
            end
            return cache[name] or nil
        end
        for _, i in ipairs(sel) do
            if aegisub.progress and aegisub.progress.is_cancelled() then error(cancelled, 0) end
            local line = subs[i]
            if line.class == "dialogue" then
                local text = convert_text(line.text, resolve)
                if text ~= line.text then pending[#pending + 1] = {index = i, text = text} end
            end
        end
    end)
    if not ok then
        if reason == cancelled then aegisub.cancel() end
        show_message("未修改字幕。字体查询失败：\n" .. tostring(reason))
        return sel, active
    end
    for _, change in ipairs(pending) do
        local line = subs[change.index]
        line.text = change.text
        subs[change.index] = line
    end
    if #pending > 0 then aegisub.set_undo_point(script_name) end
    if #missing > 0 then
        local shown = {}
        for i = 1, math.min(#missing, 20) do shown[i] = missing[i] end
        if #missing > 20 then shown[#shown + 1] = "……另有 " .. (#missing - 20) .. " 个名称" end
        show_message("已修改 " .. #pending .. " 行。以下字体未安装、没有可验证的英文名称，或无法读取，已保留原样：\n" .. table.concat(shown, "\n"))
    end
    return sel, active
end

local function validate(_, sel) return #sel > 0 end

if dependency_record then
    dependency_record:registerMacro(script_name, script_description, process, validate)
else
    aegisub.register_macro(script_name, script_description, process, validate)
end
