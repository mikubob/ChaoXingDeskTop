"""Tests for option extraction when aria-label is truncated by unescaped quotes."""
import sys
from pathlib import Path
import unittest

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor" / "chaoxing"))

from api.decode import _extract_choices, _process_question
from chaoxing_desktop.answers import option_pairs


class DecodeChoicesTest(unittest.TestCase):
    def test_single_quote_in_aria_label_falls_back_to_dom(self):
        html = """
        <div class="TiMu" data="0">
          <div class="Zy_TItle">如果函数f(x)在区间二阶可导，那么以下哪个命题与其他命题不等价？（）</div>
          <ul>
            <li aria-label="A f(x)是严格凸函数选择"><span>A</span><a>f(x)是严格凸函数</a></li>
            <li aria-label='B f'(x)在内部(a,b)不一定连续选择'><span>B</span><a>f'(x)在内部(a,b)不一定连续</a></li>
            <li aria-label='C f''(x) &gt; 0选择'><a>f''(x) &gt; 0</a></li>
          </ul>
        </div>
        """
        div = BeautifulSoup(html, "html.parser")
        div.attrs["data"] = "1001"
        q = _process_question(div)
        lines = q["options"].splitlines()
        self.assertEqual(lines, [
            "A f(x)是严格凸函数",
            "B. f'(x)在内部(a,b)不一定连续",
            "C. f''(x) > 0",
        ])
        self.assertEqual(option_pairs(q, require_labels=True), [
            ("A", "f(x)是严格凸函数"),
            ("B", "f'(x)在内部(a,b)不一定连续"),
            ("C", "f''(x) > 0"),
        ])

    def test_double_quote_right_after_label_and_same_initial_word(self):
        with_span = BeautifulSoup(
            '<li aria-label="A "x"引号测试选择"><span>A</span><a>"x"引号测试</a></li>',
            "html.parser",
        ).li
        self.assertEqual(_extract_choices(with_span), 'A. "x"引号测试')

        same_initial = BeautifulSoup(
            "<li aria-label='E English's rule选择'><a>English's rule</a></li>",
            "html.parser",
        ).li
        self.assertEqual(_extract_choices(same_initial), "E. English's rule")

    def test_font_decoder_applied_to_fallback_and_label(self):
        class FakeDecoder:
            def decode(self, text):
                return text.replace("\ue001", "f").replace("\ue002", "B")

        li = BeautifulSoup(
            "<li aria-label='\ue002 \ue001'(x)选择'><span>\ue002</span><a>\ue001'(x)</a></li>",
            "html.parser",
        ).li
        self.assertEqual(_extract_choices(li, FakeDecoder()), "B. f'(x)")


if __name__ == "__main__":
    unittest.main()
