"""Exercise both real Lua macros under the LuaJIT runtime used by Aegisub."""
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime


SCRIPT = Path(__file__).resolve().parents[1] / 'macros/wenhe.RoundPos.lua'


class RoundPosTests(unittest.TestCase):
    def setup_macro(self, dependency_control='absent'):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
            macros = {}; undo_count = 0
            aegisub = {
                register_macro = function(n, d, f, v)
                    macros[#macros + 1] = {name = n, process = f, validate = v}
                end,
                set_undo_point = function(n) undo_count = undo_count + 1; undo_name = n end
            }
        ''')
        if dependency_control == 'present':
            lua.execute('''
                package.preload['l0.DependencyControl'] = function()
                    return function(config)
                        assert(config.feed:match('/main/DependencyControl.json$'))
                        return {registerMacro = function(self, n, d, f, v, _, submenu)
                            assert(submenu == true)
                            aegisub.register_macro(n, d, f, v)
                        end}
                    end
                end
            ''')
        elif dependency_control == 'broken':
            lua.execute('''
                package.preload['l0.DependencyControl'] = function()
                    return function() error('DependencyControl initialization failed') end
                end
            ''')
        lua.execute(SCRIPT.read_text(encoding='utf-8'))
        return lua

    def round_text(self, lua, text, mode):
        lua.globals().input_text = text
        lua.globals().mode = mode
        return lua.execute('''
            local subs = {{class = 'dialogue', text = input_text}}
            macros[mode].process(subs, {1}, 1)
            return subs[1].text
        ''')

    def test_two_modes_and_registration_with_and_without_dependency_control(self):
        for depctrl in ('absent', 'present', 'broken'):
            with self.subTest(dependency_control=depctrl):
                lua = self.setup_macro(depctrl)
                lua.execute('''
                    assert(#macros == 2)
                    assert(macros[1].name:find('四舍五入取整', 1, true))
                    assert(macros[2].name:find('四舍五入取 5 的倍数', 1, true))
                    assert(not macros[1].validate({}, {}))
                    assert(macros[2].validate({}, {1}))
                ''')
                self.assertEqual(self.round_text(lua, r'{\pos(123.4,207.6)}文本', 1),
                                 r'{\pos(123,208)}文本')
                self.assertEqual(self.round_text(lua, r'{\pos(123.4,207.6)}文本', 2),
                                 r'{\pos(125,210)}文本')

    def test_positive_negative_halfway_zero_and_decimal_formats(self):
        cases = [
            (1, '12.49,-12.49', '12,-12'),
            (1, '12.5,-12.5', '13,-13'),
            (1, '.5,-.5', '1,-1'),
            (1, '-.49,+0.49', '0,0'),
            (1, ' +10. , -20.0 ', '10,-20'),
            (2, '12.49,-12.49', '10,-10'),
            (2, '12.5,-12.5', '15,-15'),
            (2, '2.5,-2.5', '5,-5'),
            (2, '-2.49,+2.49', '0,0'),
            (2, ' +10. , -20.0 ', '10,-20'),
            (2, '1000002.5,-1000002.5', '1000005,-1000005'),
        ]
        lua = self.setup_macro()
        for mode, value, expected in cases:
            with self.subTest(mode=mode, value=value):
                self.assertEqual(self.round_text(lua, r'{\pos(' + value + ')}', mode),
                                 r'{\pos(' + expected + ')}')

    def test_selection_other_fields_comments_and_single_undo(self):
        for mode, expected in ((1, r'{\pos(13,-18)}'), (2, r'{\pos(15,-20)}')):
            lua = self.setup_macro()
            lua.globals().mode = mode
            lua.globals().expected = expected
            lua.execute(r'''
                local original = [[{\pos(12.5,-17.5)}]]
                local subs = {
                    {class = 'style', name = 'Style', text = original},
                    {class = 'dialogue', text = original, style = 'Default',
                     start_time = 123, end_time = 456, layer = 2, actor = '文何',
                     effect = 'fx', margin_l = 7, margin_r = 8, margin_t = 9, comment = true},
                    {class = 'dialogue', text = original},
                    {class = 'dialogue', text = original}
                }
                local sel = {4, 1, 2}
                local returned, active = macros[mode].process(subs, sel, 4)
                assert(returned == sel and active == 4)
                assert(subs[1].text == original and subs[3].text == original)
                assert(subs[2].text == expected and subs[4].text == expected)
                local line = subs[2]
                assert(line.style == 'Default' and line.start_time == 123 and line.end_time == 456)
                assert(line.layer == 2 and line.actor == '文何' and line.effect == 'fx')
                assert(line.margin_l == 7 and line.margin_r == 8 and line.margin_t == 9 and line.comment)
                assert(undo_count == 1)
            ''')

    def test_all_override_blocks_preserve_other_tags_and_literal_text(self):
        lua = self.setup_macro()
        original = r'正文\pos(1.2,3.4){\an7\pos (1.2,3.4)\org(1.2,3.4)}字{\pos(-7.5,12.5)\bord2}{\move(1,2,3,4)}'
        expected = r'正文\pos(1.2,3.4){\an7\pos (0,5)\org(1.2,3.4)}字{\pos(-10,15)\bord2}{\move(1,2,3,4)}'
        self.assertEqual(self.round_text(lua, original, 2), expected)

    def test_malformed_tags_remain_unchanged(self):
        invalid = ('', '1', ',2', '1,', '1,2,3', '1..2,3', '1e2,3', '0x10,3',
                   'nan,3', 'inf,3', '1 2,3', '--2,3', '9' * 400 + ',3')
        lua = self.setup_macro()
        for mode in (1, 2):
            for params in invalid:
                with self.subTest(mode=mode, params=params):
                    text = r'{\pos(' + params + ')}'
                    self.assertEqual(self.round_text(lua, text, mode), text)
            for text in (r'{\position(1.2,3.4)}', r'{\pos(1.2,3.4}', r'\pos(1.2,3.4)'):
                self.assertEqual(self.round_text(lua, text, mode), text)
        self.assertEqual(lua.globals().undo_count, 0)

    def test_empty_selection_unchanged_lines_and_repeat_create_no_extra_undo(self):
        for mode in (1, 2):
            lua = self.setup_macro()
            lua.globals().mode = mode
            lua.execute(r'''
                local subs = {{class = 'dialogue', text = [[{\pos(10,-20)}正文]]},
                              {class = 'dialogue', text = '无位置标签'},
                              {class = 'dialogue', text = ''}}
                macros[mode].process(subs, {}, 1)
                macros[mode].process(subs, {1, 2, 3}, 1)
                assert(undo_count == 0)
                subs[1].text = [[{\pos(10.0,-20.000)}]]
                macros[mode].process(subs, {1}, 1)
                assert(subs[1].text == [[{\pos(10,-20)}]] and undo_count == 1)
                macros[mode].process(subs, {1}, 1)
                assert(undo_count == 1)
            ''')


if __name__ == '__main__':
    unittest.main()
