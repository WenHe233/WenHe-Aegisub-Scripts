"""Run the real LuaJIT/GDI resolver with process-private, deterministic test fonts."""
import ctypes
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from lupa.luajit21 import LuaRuntime


SCRIPT = Path(__file__).resolve().parents[1] / 'macros/wenhe.EnglishFontName.lua'


def checksum(data):
    data += b'\0' * (-len(data) % 4)
    return sum(struct.unpack(f'>{len(data) // 4}I', data)) & 0xffffffff


def renamed_font(source, names):
    """Rebuild a system TTF with a controlled name table; never distribute it."""
    count = struct.unpack_from('>H', source, 4)[0]
    tables = {}
    for i in range(count):
        tag, _, offset, length = struct.unpack_from('>4sIII', source, 12 + i * 16)
        tables[tag] = source[offset:offset + length]
    records, strings = [], b''
    for name_id, language, text in names:
        encoded = text.encode('utf-16-be')
        records.append(struct.pack('>6H', 3, 1, language, name_id, len(encoded), len(strings)))
        strings += encoded
    tables[b'name'] = struct.pack('>3H', 0, len(records), 6 + len(records) * 12) + b''.join(records) + strings
    head = bytearray(tables[b'head'])
    head[8:12] = b'\0' * 4
    tables[b'head'] = bytes(head)
    offset = 12 + len(tables) * 16
    directory, contents, head_offset = [], [], None
    for tag, data in sorted(tables.items()):
        directory.append(struct.pack('>4sIII', tag, checksum(data), offset, len(data)))
        if tag == b'head':
            head_offset = offset
        contents.append(data + b'\0' * (-len(data) % 4))
        offset += len(contents[-1])
    result = bytearray(source[:12] + b''.join(directory) + b''.join(contents))
    struct.pack_into('>I', result, head_offset + 8, (0xb1b0afba - checksum(bytes(result))) & 0xffffffff)
    return result


@unittest.skipUnless(sys.platform == 'win32', 'The resolver uses Windows GDI')
class EnglishFontNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.fonts = []
        cls.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        cls.gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        cls.gdi.RemoveFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        source = (Path(os.environ['WINDIR']) / 'Fonts/arial.ttf').read_bytes()
        fixtures = [
            ('WenHe Test Soup', '文何测试星星汤'),
            ('WenHe Test (Round)', '文何测试（圆）'),
            (None, '文何仅中文名称'),
        ]
        for i, (english, localized) in enumerate(fixtures):
            names = [(1, 0x804, localized), (2, 0x804, 'Regular'), (4, 0x804, localized),
                     (3, 0x409, f'WenHePrivateTest{i}'), (5, 0x409, 'Version 1.000'),
                     (6, 0x409, f'WenHePrivateTest{i}')]
            if english:
                names += [(1, 0x409, english), (2, 0x409, 'Regular'), (4, 0x409, english)]
            # A full name is deliberately distinct from its family for the first fixture.
            if i == 0:
                names = [(n, lang, text) for n, lang, text in names if n != 4]
                names += [(4, 0x804, '文何测试星星汤 常规'), (4, 0x409, 'WenHe Test Soup Regular')]
            path = Path(cls.temp.name) / f'font-{i}.ttf'
            path.write_bytes(renamed_font(source, sorted(names, key=lambda item: (item[1], item[0]))))
            if not cls.gdi.AddFontResourceExW(str(path), 0x10, None):
                raise RuntimeError(f'Failed to load private test font: {path}')
            cls.fonts.append(path)

    @classmethod
    def tearDownClass(cls):
        for path in cls.fonts:
            cls.gdi.RemoveFontResourceExW(str(path), 0x10, None)
        cls.temp.cleanup()

    def setup_macro(self, dependency_control='absent', unsupported=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
            macros = {}; undo_count = 0; dialogs = {}
            aegisub = {
                register_macro = function(n, d, f, v)
                    macros[#macros + 1] = {name = n, process = f, validate = v}
                end,
                set_undo_point = function() undo_count = undo_count + 1 end,
                dialog = {display = function(config)
                    dialogs[#dialogs + 1] = config[1].label
                end}
            }
        ''')
        if dependency_control == 'present':
            lua.execute('''
                package.preload['l0.DependencyControl'] = function()
                    return function(config)
                        assert(config.feed:match('/main/DependencyControl.json$'))
                        return {registerMacro = function(self, n, d, f, v)
                            aegisub.register_macro(n, d, f, v)
                        end}
                    end
                end
            ''')
        elif dependency_control == 'broken':
            lua.execute('''
                package.preload['l0.DependencyControl'] = function()
                    return function() error('broken dependency control') end
                end
            ''')
        if unsupported:
            lua.execute("package.preload['ffi'] = function() return {os = 'Linux'} end")
        lua.execute(SCRIPT.read_text(encoding='utf-8'))
        return lua

    def convert(self, lua, text):
        lua.globals().input_text = text
        return lua.execute('''
            local subs = {{class = 'dialogue', text = input_text}}
            macros[1].process(subs, {1}, 1)
            return subs[1].text
        ''')

    def test_registration_and_real_font_lookup_with_each_dependency_control_state(self):
        for depctrl in ('absent', 'present', 'broken'):
            with self.subTest(depctrl=depctrl):
                lua = self.setup_macro(depctrl)
                lua.execute('''
                    assert(#macros == 1)
                    assert(not macros[1].validate({}, {}))
                    assert(macros[1].validate({}, {1}))
                ''')
                self.assertEqual(self.convert(lua, r'{\fn文何测试星星汤}'), r'{\fnWenHe Test Soup}')
                self.assertEqual(len(lua.globals().dialogs), 0)

    def test_vertical_prefix_multiple_tags_whitespace_and_literal_text(self):
        lua = self.setup_macro()
        self.assertEqual(
            self.convert(lua, r'正文\fn文何测试星星汤{\an7\fn@文何测试星星汤\bord2}字{\fn 文何测试星星汤 \i1}'),
            r'正文\fn文何测试星星汤{\an7\fn@WenHe Test Soup\bord2}字{\fn WenHe Test Soup \i1}')
        self.assertEqual(lua.globals().undo_count, 1)

    def test_transforms_and_parentheses_in_font_names(self):
        lua = self.setup_macro()
        cases = [
            (r'{\t(0,500,\fn文何测试星星汤)\bord2}', r'{\t(0,500,\fnWenHe Test Soup)\bord2}'),
            (r'{\t(\fn@文何测试（圆）)}', r'{\t(\fn@WenHe Test (Round))}'),
            (r'{\fn文何测试（圆）}', r'{\fnWenHe Test (Round)}'),
            (r'{\t(\fn文何测试星星汤\bord2)}', r'{\t(\fnWenHe Test Soup\bord2)}'),
            (r'{\clip(1,2,3,4)\fn文何测试星星汤}', r'{\clip(1,2,3,4)\fnWenHe Test Soup}'),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(self.convert(lua, original), expected)

    def test_selection_comments_fields_and_one_undo(self):
        lua = self.setup_macro()
        lua.execute(r'''
            local original = [[{\fn文何测试星星汤}正文]]
            local subs = {
                {class = 'style', fontname = '文何测试星星汤', text = original},
                {class = 'dialogue', text = original, style = 'Default', comment = true,
                 start_time = 123, end_time = 456, layer = 2, actor = '文何', effect = 'fx', margin_l = 7},
                {class = 'dialogue', text = original},
                {class = 'dialogue', text = original}
            }
            local sel = {4, 1, 2}
            local returned, active = macros[1].process(subs, sel, 4)
            assert(returned == sel and active == 4)
            assert(subs[1].text == original and subs[1].fontname == '文何测试星星汤')
            assert(subs[3].text == original)
            assert(subs[2].text == [[{\fnWenHe Test Soup}正文]] and subs[4].text == subs[2].text)
            local line = subs[2]
            assert(line.style == 'Default' and line.comment and line.start_time == 123 and line.end_time == 456)
            assert(line.layer == 2 and line.actor == '文何' and line.effect == 'fx' and line.margin_l == 7)
            assert(undo_count == 1)
            macros[1].process(subs, sel, 4)
            assert(undo_count == 1 and #dialogs == 0)
        ''')

    def test_missing_font_and_no_english_name_never_use_gdi_fallback(self):
        lua = self.setup_macro()
        text = r'{\fn绝对未安装的文何字体}{\fn文何仅中文名称}{\fn@绝对未安装的文何字体}'
        self.assertEqual(self.convert(lua, text), text)
        self.assertEqual(lua.globals().undo_count, 0)
        self.assertEqual(len(lua.globals().dialogs), 1)
        self.assertEqual(lua.globals().dialogs[1].count('绝对未安装的文何字体'), 1)

    def test_unselectable_full_face_name_is_preserved_instead_of_collapsing_to_family(self):
        lua = self.setup_macro()
        # GDI selects this fixture by family, but not by its distinct full name.
        original = r'{\fn文何测试星星汤 常规}'
        self.assertEqual(self.convert(lua, original), original)
        self.assertEqual(lua.globals().undo_count, 0)
        self.assertEqual(len(lua.globals().dialogs), 1)

    def test_english_names_case_and_empty_resets_stay_unchanged(self):
        lua = self.setup_macro()
        for text in (r'{\fnArial\fn@Arial}', r'{\fnarial}', r'{\fn\b1}', r'{\fn@}', r'{\fn   }'):
            with self.subTest(text=text):
                self.assertEqual(self.convert(lua, text), text)
        self.assertEqual(lua.globals().undo_count, 0)
        self.assertEqual(len(lua.globals().dialogs), 0)

    def test_literal_incomplete_blocks_and_no_tags_do_not_load_ffi(self):
        lua = self.setup_macro(unsupported=True)
        for text in ('', '普通正文', r'\fn文何测试星星汤', r'{\fn文何测试星星汤', r'{\fscx100}', r'{\fn}'):
            self.assertEqual(self.convert(lua, text), text)
        lua.execute('macros[1].process({}, {}, nil)')
        self.assertEqual(len(lua.globals().dialogs), 0)
        self.assertEqual(lua.globals().undo_count, 0)

    def test_unsupported_host_has_actionable_message_and_no_edits(self):
        lua = self.setup_macro(unsupported=True)
        text = r'{\fn文何测试星星汤}'
        self.assertEqual(self.convert(lua, text), text)
        self.assertIn('Windows', lua.globals().dialogs[1])
        self.assertEqual(lua.globals().undo_count, 0)

    def test_results_with_missing_fonts_still_apply_verified_names(self):
        lua = self.setup_macro()
        self.assertEqual(self.convert(lua, r'{\fn文何测试星星汤}{\fn没有这个字体}'),
                         r'{\fnWenHe Test Soup}{\fn没有这个字体}')
        self.assertEqual(lua.globals().undo_count, 1)
        self.assertEqual(len(lua.globals().dialogs), 1)

    def test_cancel_after_lookup_leaves_all_selected_rows_untouched(self):
        lua = self.setup_macro()
        lua.execute(r'''
            local calls, cancelled = 0, {}
            aegisub.progress = {is_cancelled = function()
                calls = calls + 1
                return calls == 2
            end}
            aegisub.cancel = function() error(cancelled, 0) end
            local original = [[{\fn文何测试星星汤}]]
            local subs = {{class = 'dialogue', text = original}, {class = 'dialogue', text = original}}
            local ok, reason = pcall(macros[1].process, subs, {1, 2}, 1)
            assert(not ok and reason == cancelled)
            assert(subs[1].text == original and subs[2].text == original)
            assert(undo_count == 0 and #dialogs == 0)
        ''')

    def test_long_names_and_many_unresolved_fonts_remain_unchanged(self):
        lua = self.setup_macro()
        names = ['文何测试星星汤' + 'X' * 40] + [f'文何不存在的字体{i}' for i in range(22)]
        text = ''.join(r'{\fn' + name + '}' for name in names)
        self.assertEqual(self.convert(lua, text), text)
        self.assertEqual(lua.globals().undo_count, 0)
        self.assertEqual(len(lua.globals().dialogs), 1)
        self.assertIn('另有 3 个名称', lua.globals().dialogs[1])

    def test_repeated_loading_does_not_conflict_with_ffi_declarations(self):
        lua = self.setup_macro()
        self.convert(lua, r'{\fn文何测试星星汤}')
        lua.execute(SCRIPT.read_text(encoding='utf-8'))
        self.convert(lua, r'{\fn文何测试星星汤}')
        self.assertEqual(len(lua.globals().dialogs), 0)

    def test_requested_example_with_locally_installed_font(self):
        if not (Path(os.environ['WINDIR']) / 'Fonts/FZSJ-AOCXXT.TTF').exists():
            self.skipTest('The user example font is not installed on this machine')
        lua = self.setup_macro()
        self.assertEqual(self.convert(lua, r'{\fn熬成星星汤}{\fn@熬成星星汤}'),
                         r'{\fnFZSJ-AOCXXT}{\fn@FZSJ-AOCXXT}')
        self.assertEqual(len(lua.globals().dialogs), 0)


if __name__ == '__main__':
    unittest.main()
