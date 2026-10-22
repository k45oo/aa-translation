# -*- coding: utf-8 -*-
"""yaruobook 解析器：正文红字片段 → colormap 片段着色（2026-10-21）。

原站骰子/安価结果只把**片段**染红（`color: rgb(255, 0, 0);` 包住 `③>>843` 或 `【1D100:75】`），
旧解析器 colormap 恒空 → 下载后颜色全丢；整行标红又会把同一行的 AA 笔画一起染红。
现口径：colormap 值 = `{"style":..., "spans":[片段文本...]}`，渲染时只包命中的片段；
片段在译文里对不上则不上色（整行原本全红的行带 `all`，那种行对不上就整行上色）。
蓝色 `#0000ff` 是 >>N 引用（原站只把引用染蓝），**不上色**。

运行：python scripts/tools/run_tests.py --modules test_site_yaruobook
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "sites"))
sys.path.insert(0, os.path.join(SCRIPTS, "common"))

import gen_aa_html  # noqa: E402
import yaruobook  # noqa: E402

RED = 'color: rgb(255, 0, 0);'


def _page(*bodies):
    """把若干 dd 正文拼成最小 yaruobook 页面（entry-content + dt/dd）"""
    posts = []
    for i, body in enumerate(bodies, 1):
        posts.append(
            f'<dt class="author-res-dt">{i} ： <span style="color:#008800;">'
            f'<strong>◆作者</strong></span> ： 2026/10/21(水) 12:00:00.00 ID:abc</dt>'
            f'<dd class="author-res">{body}</dd>')
    return ('<html><body><h1>测试标题</h1><div class="entry-content cf">'
            + ''.join(posts) + '</div></article></body></html>')


def _texts(cm):
    """colormap → {行号: [片段文本...]}（spans 项是 {"text","cols"}，兼容纯文本写法）"""
    out = {}
    for k, v in cm.items():
        out[int(k)] = [sp['text'] if isinstance(sp, dict) else sp for sp in v['spans']]
    return out


def _extract(body):
    return yaruobook.extract(_page(body), '1', 'entry_1.html')


class TestRedColor(unittest.TestCase):
    def test_只标红字片段_不整行(self):
        # 开标签在上一行末尾（空行），内容在下一行 + 行尾还有不红的 `※【1D3:2】`
        body = (' 一行普通文本 <br>'
                ' <span style="color: rgb(255, 0, 0);"><br>'
                ' ２　保険適用外の闇医者での治療　</span>　※【1D3:2】 <br>'
                ' ３　另一个选项')
        lines, cm, _ = _extract(body)
        self.assertEqual(len(cm), 1, f'只应标 1 行，实际 {cm}')
        k = int(list(cm)[0])
        cm = {int(kk): v for kk, v in cm.items()}      # 进程内键是 int，落盘 JSON 才变字符串
        self.assertEqual(cm[k]['style'], RED)
        self.assertEqual(_texts(cm)[k], ['２　保険適用外の闇医者での治療'])
        self.assertNotIn('all', cm[k], '这行还有不红的 ※【1D3:2】 → 不能整行上色')

    def test_十六进制与_font_写法(self):
        body = (' <span style="color:#ff0000;">⑥死線が交差する</span> <br>'
                ' <font color="#ff0000">【1D100:43】≦75で妊娠</font> <br>'
                ' 普通行')
        lines, cm, _ = _extract(body)
        got = {lines[int(k)].strip(): t for k, t in _texts(cm).items()}
        self.assertEqual(len(got), 2, cm)
        self.assertEqual(got['⑥死線が交差する'], ['⑥死線が交差する'])
        self.assertEqual(got['【1D100:43】≦75で妊娠'], ['【1D100:43】≦75で妊娠'])

    def test_整行全红标_all_片段后还有黑字则不标(self):
        body = (' <span style="color: rgb(255, 0, 0);">【1D100:43】≦75で妊娠</span> <br>'
                ' <span style="color: rgb(255, 0, 0);">③&gt;&gt;843</span> 后面还有黑字')
        lines, cm, _ = _extract(body)
        vals = {lines[int(k)].strip(): v for k, v in cm.items()}
        self.assertTrue(vals['【1D100:43】≦75で妊娠'].get('all'), '整行都是红字 → all')
        self.assertNotIn('all', vals['③>>843 后面还有黑字'], '片段后还有黑字 → 不能整行上色')

    def test_跨行红_span_落在字所在行(self):
        # 红 span 开标签是上一行最后一个东西（该行没有红字内容）→ 上一行不标色，下一行标
        body = (' ⑥死線が交差する <span style="color: rgb(255, 0, 0);"><br>'
                ' ⑦三日三晩の死闘</span>')
        lines, cm, _ = _extract(body)
        got = {lines[k].strip(): v for k, v in _texts(cm).items()}
        self.assertEqual(got, {'⑦三日三晩の死闘': ['⑦三日三晩の死闘']}, got)

    def test_实体编码与嵌套_span(self):
        body = '<span style="color: rgb(255, 0, 0);">&#9314;<span>&gt;&gt;843</span></span>'
        lines, cm, _ = _extract(body)
        cm = {int(k): v for k, v in cm.items()}
        sp = cm[3]['spans'][0]
        self.assertEqual(sp['text'], '③>>843', '嵌套 span + 实体要在清洗后的行里对得上')
        self.assertEqual(sp['cols'], [0, 7], '③ 占 2 格 + ASCII「>>843」各 1 格 = 7')

    def test_蓝色引用不标色且不以红显示(self):
        body = (' その後で<span style="color: #0000ff;">&gt;&gt;1</span>がダイスを振る <br>'
                ' ①<span style="color: #0000ff;">&gt;&gt;118</span>　②<span style="color: #0000ff;">&gt;&gt;121</span>')
        lines, cm, _ = _extract(body)
        self.assertEqual(cm, {}, f'蓝色 >>N 不该上色：{cm}')

    def test_深红_darkred_不算(self):
        body = ' <span style="color:darkred;">这一行不是骰子标红</span>'
        lines, cm, _ = _extract(body)
        self.assertEqual(cm, {})

    def test_txt_内容与无颜色时完全一致(self):
        plain = ' 选项一 <br> 选项二 <br> 普通行'
        colored = (' 选项一 <br>'
                   ' <span style="color: rgb(255, 0, 0);">选项二</span> <br>'
                   ' 普通行')
        a, cm_a, _ = _extract(plain)
        b, cm_b, _ = _extract(colored)
        self.assertEqual(a, b, 'colormap 只加样式，不能改 txt 一行一个字')
        self.assertEqual(len(cm_a), 0)
        self.assertEqual(len(cm_b), 1)

    def test_空行不占_colormap(self):
        body = (' 第一行 <br>'
                ' <span style="color: rgb(255, 0, 0);"><br><br><br>红色内容</span>')
        lines, cm, _ = _extract(body)
        for k in cm:
            self.assertTrue(lines[int(k)].strip(), f'空行不该进 colormap: 行{k}=空')

    def test_colormap_键是_0based_txt_行号(self):
        body = ' 普通 <br> <span style="color: rgb(255, 0, 0);">红色行</span>'
        lines, cm, _ = _extract(body)
        for k in cm:
            self.assertEqual(lines[int(k)].strip(), '红色行')


class TestRenderFragment(unittest.TestCase):
    """片段着色在交付页渲染端（gen_aa_html.render_line）的表现"""

    site = yaruobook

    def _render(self, line, color):
        return gen_aa_html.render_line(3, line, {'3': color}, self.site)

    def test_只包片段_其余原样(self):
        line = ' 　　　====! 　 ||　　　　　　 ③>>843'
        html = self._render(line, {'style': RED, 'spans': ['③>>843']})
        self.assertEqual(
            html,
            ' 　　　====! 　 ||　　　　　　 <span style="color: rgb(255, 0, 0);">'
            '③&gt;&gt;843</span>')
        self.assertFalse(html.startswith('<span style='), '整行不得被包红')

    def test_片段对不上则不上色(self):
        color = {'style': RED, 'spans': ['③>>843']}
        html = self._render(' 中文译掉了 ③号选项', color)
        self.assertNotIn('color: rgb(255, 0, 0)', html)
        self.assertEqual(html, ' 中文译掉了 ③号选项')

    def test_all_行片段对不上时整行上色(self):
        color = {'style': RED, 'spans': ['【1D100:43】≦75で妊娠'], 'all': True}
        html = self._render('【1D100:43】≤75就怀孕', color)
        self.assertTrue(html.startswith('<span style="color: rgb(255, 0, 0);">'))
        self.assertIn('≤75就怀孕', html)

    def test_旧字符串格式仍然整行(self):
        html = self._render(' 整行红', RED)
        self.assertEqual(html, '<span style="color: rgb(255, 0, 0);"> 整行红</span>')

    def test_译文改写片段时按原行列位回填(self):
        # 源行红字整条是安価选项文本，译文换成中文 → 文本匹配失败，按 cols 回填
        src = ' 　　　/ 　ｨ　　　　　　 3：ドンナの『パンプアップ』'
        tr = ' 　　　/ 　ｨ　　　　　　 3：被贵妇人的『泵感』　　　　　　　'
        frag = '3：ドンナの『パンプアップ』'
        from aa_common import disp_width
        w1 = disp_width(src[:src.index(frag)])
        color = {'style': RED,
                 'spans': [{'text': frag, 'cols': [w1, w1 + disp_width(frag)]}]}
        html = self._render(tr, color)
        self.assertIn('<span style="color: rgb(255, 0, 0);">3：被贵妇人的『泵感』</span>', html)
        self.assertFalse(html.startswith('<span style='), '整行不得被包红')
        # 原文模式：文本精确命中，一个字不差
        html_src = self._render(src, color)
        self.assertIn('<span style="color: rgb(255, 0, 0);">3：ドンナの『パンプアップ』</span>', html_src)

    def test_列位越界不上色(self):
        color = {'style': RED, 'spans': [{'text': '③>>843', 'cols': [200, 220]}]}
        html = self._render(' 短行', color)
        self.assertNotIn('color: rgb(255, 0, 0)', html)

    def test_与_sfx_区间并存不重叠(self):
        line = '　拟声词译文　③>>843'
        html = self._render(line, {'style': RED, 'spans': ['③>>843']})
        self.assertIn('<span style="color: rgb(255, 0, 0);">③&gt;&gt;843</span>', html)
        self.assertTrue(html.startswith('　拟声词译文　'))


class TestColormapShape(unittest.TestCase):
    def test_parse_posts_返回行颜色元组(self):
        posts = yaruobook.parse_posts(yaruobook.extract_body(
            _page(' a <br> <span style="color: rgb(255, 0, 0);">b</span>')))
        self.assertEqual(len(posts), 1)
        header, rows = posts[0]
        self.assertTrue(all(isinstance(r, tuple) and len(r) == 2 for r in rows), rows)
        got = [(r[0].strip(), r[1]) for r in rows]
        # 行文本是 ' b'（前导空格保留）→ 片段从第 1 格起
        self.assertIn(('b', {'style': RED, 'spans': [{'text': 'b', 'cols': [1, 2]}], 'all': True}), got)

    def test_帖头行来自_dt_不进_colormap(self):
        lines, cm, _ = _extract(' 正文')
        self.assertTrue(lines[0].startswith('# '))
        self.assertTrue(lines[1].startswith('# 原帖地址'))
        self.assertIn('◆作者', lines[2])
        self.assertNotIn(2, {int(k) for k in cm})


if __name__ == '__main__':
    unittest.main()
