"""Plain question text with explicit markup, preserving literal comparisons."""
from html.parser import HTMLParser
import re


class _Text(HTMLParser):
    def __init__(self, source, on_image=None):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.on_image = on_image
        # Parsed platform questions already contain plain text plus img tags.
        # Only strip paired formatting markup, never arbitrary < y ... > math.
        self.paired = {tag.lower() for tag in re.findall(r'</(p|div|span|b|strong|em|i|u|sup|sub|code|pre|a)\s*>', source, re.I)}

    def feed(self, source):
        # 未知标签按原文处理，避免 x<y 吞掉后面的实际图片标签。
        tags = '|'.join(sorted(self.paired | {'br', 'img'}))
        super().feed(re.sub(r'<(?!/?(?:' + tags + r')(?=[\s/>]))', '&lt;', source, flags=re.I))

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('\n')
        elif tag == 'img':
            if self.on_image is not None:
                self.parts.append(self.on_image(dict(attrs).get('src') or ''))
        elif tag not in self.paired:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag != 'img' and tag not in self.paired:
            self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    source = str(value)
    parser = _Text(source)
    parser.feed(source)
    parser.close()
    return ''.join(parser.parts)


_IMAGE_LINK = re.compile(r'(https?://[^\s<>"\']+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>"\']*)?)', re.I)


def question_parts(value):
    """按题面顺序产生文本和图源，保留缺失图源供调用方诊断。"""
    source = str(value)
    parser = _Text(source, lambda url: (True, 'https:' + url if url.startswith('//') else url))
    parser.feed(source)
    parser.close()
    text = []
    for part in [*parser.parts, (False, '')]:
        if isinstance(part, str):
            text.append(part)
            continue
        for index, piece in enumerate(_IMAGE_LINK.split(''.join(text))):
            if piece:
                yield bool(index % 2), piece
        text.clear()
        if part[0]:
            yield part
