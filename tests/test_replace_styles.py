"""Run the actual macro under LuaJIT with an Aegisub dialog fixture."""
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime


class ReplaceStylesTests(unittest.TestCase):
    def setup_macro(self, dependency_control=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
            undo_count = 0
            aegisub = {
                register_macro = function(n, d, f, v) macro = f; validate = v end,
                set_undo_point = function() undo_count = undo_count + 1 end,
                dialog = {display = function(d, b)
                    local result = {}
                    for _, c in ipairs(d) do
                        if c.name then result[c.name] = c.value end
                    end
                    return respond(d, b, result)
                end}
            }
            subs = {}
            for _, name in ipairs({'A', 'B', 'C', 'D', 'E', 'F'}) do
                subs[#subs + 1] = {class = 'style', name = name}
            end
            for _, name in ipairs({'A', 'B', 'C', 'A'}) do
                subs[#subs + 1] = {class = 'dialogue', style = name,
                    text = 'unchanged', start_time = 123, comment = true}
            end
            sel = {9, 7, 8}
        ''')
        if dependency_control:
            lua.execute('''
                package.preload['l0.DependencyControl'] = function()
                    return function() return {registerMacro = function(self, f, v)
                        macro = f; validate = v
                    end} end
                end
            ''')
        lua.execute((Path(__file__).resolve().parents[1] / 'macros/wenhe.ReplaceStyles.lua').read_text(encoding='utf-8'))
        return lua

    def test_mapping_only_selected_style_fields_and_one_undo(self):
        for depctrl in (False, True):
            lua = self.setup_macro(depctrl)
            lua.execute('''
                respond = function(d, b, r)
                    r.target_1 = 'D'; r.target_2 = 'E'; r.target_3 = 'F'
                    return '应用全部', r
                end
                returned, active = macro(subs, sel, 8)
                assert(returned == sel and active == 8)
                assert(subs[7].style == 'D' and subs[8].style == 'E' and subs[9].style == 'F')
                assert(subs[10].style == 'A' and subs[1].name == 'A')
                assert(subs[7].text == 'unchanged' and subs[7].start_time == 123 and subs[7].comment)
                assert(undo_count == 1)
            ''')

    def test_swap_is_simultaneous(self):
        lua = self.setup_macro()
        lua.execute('''
            respond = function(d, b, r)
                r.target_1 = 'B'; r.target_2 = 'A'; return '应用全部', r
            end
            macro(subs, sel, 7)
            assert(subs[7].style == 'B' and subs[8].style == 'A')
        ''')

    def test_cancel_close_and_noop_do_not_create_undo(self):
        for button in ('取消', False, '应用全部'):
            lua = self.setup_macro()
            lua.globals().button = button
            lua.execute('''
                respond = function(d, b, r) return button, r end
                macro(subs, sel, 7)
                assert(subs[7].style == 'A' and undo_count == 0)
            ''')

    def test_missing_source_can_be_preserved_or_repaired(self):
        lua = self.setup_macro()
        lua.execute('''
            subs[7].style = '缺失样式'
            respond = function(d, b, r)
                assert(r.target_1 == '缺失样式')
                r.target_1 = 'D'; return '应用全部', r
            end
            macro(subs, sel, 7)
            assert(subs[7].style == 'D')
        ''')

    def test_pagination_retains_choices_without_early_mutation(self):
        lua = self.setup_macro()
        lua.execute('''
            subs = {}; sel = {}
            for n = 1, 13 do subs[n] = {class = 'style', name = '样式' .. n} end
            for n = 1, 13 do
                subs[13+n] = {class = 'dialogue', style = '样式' .. n}
                sel[n] = 13+n
            end
            local step = 0
            respond = function(d, b, r)
                step = step + 1
                assert(subs[14].style == '样式1' and undo_count == 0)
                if step == 1 then r.target_1 = '样式2'; return '下一页', r end
                if step == 2 then r.target_13 = '样式3'; return '上一页', r end
                assert(r.target_1 == '样式2'); return '应用全部', r
            end
            macro(subs, sel, 14)
            assert(subs[14].style == '样式2' and subs[26].style == '样式3')
            assert(undo_count == 1)
        ''')

    def test_empty_selection_and_no_styles(self):
        lua = self.setup_macro()
        lua.execute('''
            assert(not validate(subs, {}))
            macro(subs, {}, 7)
            subs = {{class = 'dialogue', style = 'Missing'}}
            respond = function(d, b, r) return '确定', r end
            macro(subs, {1}, 1)
            assert(subs[1].style == 'Missing' and undo_count == 0)
        ''')


if __name__ == '__main__':
    unittest.main()
