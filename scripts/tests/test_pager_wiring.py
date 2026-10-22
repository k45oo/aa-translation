# -*- coding: utf-8 -*-
"""章节分页接线测试（2026-09-13）：app.js 的 aaPager 被四个页面正确接入。

为什么不测 JS 行为：仓库没有 JS 测试运行时。这里锁住"接线不丢"——分页条容器、
表格 id、aaPager 调用、阅读器目录的 activeOf 定位（打开任意章不被翻页挡住）。
运行：python -m unittest scripts.tests.test_pager_wiring
"""
import os
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
_TPL = os.path.join(_ROOT, "aa_web_app", "templates")
_APPJS = os.path.join(_ROOT, "aa_web_app", "static", "app.js")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestPagerWiring(unittest.TestCase):
    def test_appjs_defines_pager_with_three_sizes(self):
        js = _read(_APPJS)
        self.assertIn("function aaPager(", js)
        self.assertIn("[10, 50, 100]", js)
        self.assertIn("localStorage", js)          # 每页大小记忆
        self.assertIn("r.style.display", js)       # 只改 display（不动 DOM → 勾选跨页保留）
        self.assertIn("'none'", js)

    def test_translate_page(self):
        t = _read(os.path.join(_TPL, "translate.html"))
        self.assertIn('id="chapters-pager"', t)
        self.assertIn('id="chapters-tbl"', t)
        # 2026-09-16: 分页行集合改为 curRows（受「仅未译」筛选影响），aaPager 仍在同页接线
        self.assertIn("aaPager({rows: curRows, controls: '#chapters-pager'", t)
        self.assertIn("'translate:' + workId", t)

    def test_proofread_page(self):
        t = _read(os.path.join(_TPL, "proofread.html"))
        self.assertIn('id="chapters-pager"', t)
        self.assertIn('id="chapters-tbl"', t)
        self.assertIn("aaPager({rows: '#chapters-tbl tbody tr'", t)
        self.assertIn("'proofread:' + workId", t)

    def test_work_page(self):
        t = _read(os.path.join(_TPL, "work.html"))
        self.assertIn('id="chapters-pager"', t)
        self.assertIn('id="chapters-tbl"', t)
        self.assertIn("aaPager({rows: '#chapters-tbl tbody tr'", t)

    def test_translate_page_current_page_buttons(self):
        t = _read(os.path.join(_TPL, "translate.html"))
        self.assertIn('id="p-untr"', t)
        self.assertIn('id="p-tr"', t)
        self.assertIn('id="p-none"', t)
        self.assertIn("function pageChks()", t)          # 只用 pager.visible() 的行
        self.assertIn("pager.visible", t)
        self.assertIn('data-tr="{{ 1 if c.translated else 0 }}"', t)

    def test_translate_page_pager_row_holds_page_buttons_and_filter(self):
        """2026-09-16：本页按钮搬进换页行（pagerRowCtrls 挂到 #chapters-pager）+ 仅未译筛选 + 默认全不选。"""
        t = _read(os.path.join(_TPL, "translate.html"))
        self.assertIn("box.appendChild(pagerRowCtrls())", t)
        self.assertIn("box.className = 'pager-page-btns'", t)
        self.assertIn('id="flt-untr"', t)
        self.assertIn("onlyUntr ? allRows.filter(r => r.dataset.tr === '0') : allRows", t)
        # 本页按钮不再留在顶部 actions 行（搬迁完成：旧的 btn small ghost 静态标记已移走）
        self.assertNotIn('<button id="p-untr" class="btn small ghost"', t)
        self.assertIn('<button id="p-untr" class="btn tiny ghost" title="只勾选当前页里的未译章"', t)
        # 章节勾选默认全不选：模板 checkbox 不带 checked
        self.assertNotIn('{% if not c.translated %}checked{% endif %}', t)
        self.assertIn('data-eid="{{ c.src_file }}"></td>', t)

    def test_proofread_page_current_page_buttons(self):
        t = _read(os.path.join(_TPL, "proofread.html"))
        self.assertIn('id="p-tr"', t)
        self.assertIn('id="p-none"', t)
        self.assertIn("function pageChks()", t)
        self.assertIn('data-tr="1"', t)

    def test_reader_toc_jumps_to_active_chapter(self):
        t = _read(os.path.join(_TPL, "reader.html"))
        self.assertIn('id="toc-pager"', t)
        # 2026-10-17：目录行改由 applyTocFilter() 交给 aaPager（「只看收藏」时只传收藏行；全部时就是全部行）
        self.assertIn("aaPager({rows: vis, controls: '#toc-pager'", t)
        self.assertIn("activeOf: el => el.classList.contains('active')", t)   # activeOf：自动定位当前章所在页

    def test_reader_pager_symbol_buttons_and_size_below(self):
        """阅读页目录分页条（2026-09-13）：只传 symbols/sizeLast → 符号按钮 + 每页选择在页数下方。"""
        t = _read(os.path.join(_TPL, "reader.html"))
        self.assertIn("symbols: true", t)
        self.assertIn("sizeLast: true", t)
        css = _read(os.path.join(_ROOT, "aa_web_app", "static", "style.css"))
        self.assertIn(".reader-nav .pager-row .pager-prev", css)
        self.assertIn("flex-direction: column", css)      # 两行：页数行在上、每页选择在下
        self.assertIn("align-items: center; justify-content: center", css)   # 符号在按钮内居中
        self.assertIn("background: #3f3f55", css)         # 配色贴深色侧栏

    def test_other_pages_keep_text_buttons(self):
        """其余页（作品/翻译/校对）不传新参数 → 仍是「‹ 上一页 / 下一页 ›」文字按钮。"""
        js = _read(_APPJS)
        self.assertIn("'‹ 上一页'", js)
        self.assertIn("'下一页 ›'", js)
        self.assertIn("opts.sizeLast ? (navHTML + sizeHTML) : (sizeHTML + navHTML)", js)


if __name__ == "__main__":
    unittest.main()
