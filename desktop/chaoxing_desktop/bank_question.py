"""Build the text and image environment used by OCS-compatible v2 banks."""
import base64
from collections import OrderedDict
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlparse


def option_lines(options):
    if isinstance(options, list):
        return [str(value) for value in options if value is not None]
    text = str(options or '')
    # Accept an explicitly labelled, single-line list such as a.1 b.2 c.3.
    if '\n' not in text and re.match(r'^\s*[Aa][.、．:：]', text):
        parts = re.split(r'\s+(?=[A-Za-z][.、．:：])', text.strip())
        labels = [re.match(r'^([A-Za-z])[.、．:：]', part) for part in parts]
        if len(parts) > 1 and all(labels) and [m[1].upper() for m in labels] == list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'[:len(parts)]):
            return parts
    return text.splitlines()


class _TextWithImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            source = unescape(dict(attrs).get('src', ''))
            if source.startswith('//'):
                source = 'https:' + source
            if source.startswith(('https://', 'http://', 'data:image/')):
                self.images.append(source)
                self.parts.append(' ' + source + ' ')
        elif tag in ('br', 'p', 'div'):
            self.parts.append(' ')

    def handle_data(self, data):
        self.parts.append(data)


def text_with_images(value):
    parser = _TextWithImages()
    parser.feed(str(value or ''))
    text = re.sub(r'[\s\u200b\ufeff]+', ' ', ''.join(parser.parts)).strip()
    images = parser.images + re.findall(r'https?://[^\s<>"\']+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>"\']*)?', text, re.I)
    return text, list(dict.fromkeys(images))


QUESTION_TYPE = r'(?:单选题|多选题|判断题|填空题|简答题|名词解释|完形填空|阅读理解)'


def clean_title(title):
    # A number directly followed by an explicit type badge is a question index,
    # even without punctuation. Remove it before removing the badge, otherwise
    # "5【单选题】2025年" would become the ambiguous "52025年".
    title = re.sub(r'^\s*\d+\s*(?=[\[(【（]\s*' + QUESTION_TYPE + r'(?:\s*[,，]|\s*[\])】）]))', '', title)
    title = re.sub(r'^\s*\d+[.、。](?!\d)\s*', '', title)
    title = re.sub(r'[\[(【（]\s*' + QUESTION_TYPE + r'(?:\s*[,，]\s*\d+(?:\.\d+)?\s*分)?\s*[\])】）]', '', title)
    title = re.sub(r'[（(]\s*\d+(?:\.\d+)?\s*分\s*[)）]', '', title)
    return title.strip()


def query_title_variants(title, limit=2):
    """Typographical equivalents only: keep numbers, words and logical symbols."""
    if text_with_images(title)[1] or len(title) > 4096:
        return []
    normalized = unicodedata.normalize('NFKC', title)
    normalized = re.sub(r'\(\s*\)', '()', normalized)
    normalized = normalized.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"}))
    # A '?' after '=' is an answer placeholder, not sentence punctuation.
    if not re.search(r'=\s*[?？]$', normalized):
        normalized = re.sub(r'[。?？]+$', '', normalized).rstrip()
    compact = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', normalized)
    if not re.search(r'字符串|字符|\bprint\b|\blen\b|[=+*/]', normalized):
        compact = compact.replace('"', '').replace("'", '')
    variants = []
    for item in (normalized, compact):
        if item and item != title and item not in variants:
            variants.append(item)
    return variants[:limit]


class ImageTooLarge(ValueError):
    pass


class ImageEncoder:
    MAX_BYTES = 8 * 1024 * 1024
    MAX_CACHE_BYTES = 16 * 1024 * 1024

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.cache = OrderedDict()
        self.cache_bytes = 0

    def download(self, url):
        from .http import service_request
        origin = urlparse(url)
        import requests
        try:
            response = service_request(self, 'get', url, max_bytes=self.MAX_BYTES, headers={
                'Referer': f'{origin.scheme}://{origin.netloc}/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            }, timeout=(10, self.timeout), stream=True)
        except requests.RequestException:
            raise
        except ValueError:
            raise ImageTooLarge('图片文件过大') from None
        try:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise requests.HTTPError('图源发生重定向')
            parts, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.MAX_BYTES:
                    raise ImageTooLarge('图片文件过大')
                parts.append(chunk)
            return b''.join(parts)
        finally:
            response.close()

    def encode(self, url):
        if url in self.cache:
            self.cache.move_to_end(url)
            return self.cache[url]
        if url.startswith('data:image/'):
            raw = base64.b64decode(url.partition(',')[2], validate=True)
        else:
            raw = self.download(url)
        if len(raw) > self.MAX_BYTES:
            raise ImageTooLarge('图片文件过大')
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
        from PySide6.QtGui import QImageReader
        buffer = QBuffer()
        buffer.setData(QByteArray(raw))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if not size.isValid():
            raise ValueError('图片格式无效')
        if size.width() * size.height() > 16_000_000:
            raise ImageTooLarge('图片尺寸过大')
        picture = reader.read()
        if picture.isNull():
            raise ValueError('图片无法解码')
        if max(picture.width(), picture.height()) > 2000:
            picture = picture.scaled(2000, 2000, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if min(picture.width(), picture.height()) < 256:
            factor = 256 / min(picture.width(), picture.height())
            # Keep exceptionally long formula images bounded as well.
            factor = min(factor, 2000 / max(picture.width(), picture.height()))
            if factor > 1:
                picture = picture.scaled(round(picture.width() * factor), round(picture.height() * factor), Qt.AspectRatioMode.KeepAspectRatio)
        output = QBuffer()
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        if not picture.save(output, 'PNG'):
            raise ValueError('图片无法转换为 PNG')
        result = 'data:image/png;base64,' + base64.b64encode(bytes(output.data())).decode('ascii')
        if len(result) > self.MAX_CACHE_BYTES:
            raise ImageTooLarge('转换后的图片过大')
        # Inline image sources can themselves be several MiB; caching them as
        # keys would bypass the cache's retained-data limit.
        if not url.startswith('data:'):
            while self.cache and (len(self.cache) >= 64 or self.cache_bytes + len(result) > self.MAX_CACHE_BYTES):
                self.cache_bytes -= len(self.cache.popitem(last=False)[1])
            self.cache[url] = result
            self.cache_bytes += len(result)
        return result


def build_yanxi_environment(question, encoder, warning=lambda message: None):
    """Return wire fields and the original URLs in uploaded image order."""
    title, title_images = text_with_images(question.get('title'))
    title = clean_title(title)
    options, option_images = [], []
    for line in option_lines(question.get('options')):
        text, images = text_with_images(line)
        text = re.sub(r'^\s*[A-Za-z][.、．:：\s]+', '', text).strip()
        if text:
            options.append(text)
        option_images.extend(images)
    options = '\n'.join(options)
    encoded, image_urls, replacements, total_bytes = [], [], {}, 0
    for url in list(dict.fromkeys(title_images + option_images))[:32]:
        try:
            image = encoder.encode(url)
        except Exception as exc:
            warning(f'图片转换失败（{type(exc).__name__}），保留原图片地址供题库检索')
            continue
        total_bytes += len(image)
        if total_bytes > ImageEncoder.MAX_CACHE_BYTES:
            warning('题目图片总量过大，剩余图片保留原地址供题库检索')
            break
        encoded.append(image)
        image_urls.append(url)
        replacements[url] = f' [图片{len(encoded)}] '
    def suggestion(text):
        for url, placeholder in replacements.items():
            text = text.replace(url, placeholder)
        return text
    env = dict(title=title, options=options, type=question.get('type') or 'unknown', images=encoded,
               suggestion_title=suggestion(title) if encoded else '',
               suggestion_options=suggestion(options) if any(url in replacements for url in option_images) else '')
    return env, image_urls


def yanxi_environment(question, encoder, warning=lambda message: None):
    return build_yanxi_environment(question, encoder, warning)[0]


def restore_image_placeholders(value, image_urls):
    """Resolve only known uploaded images; retain unknown diagnostic tokens."""
    if isinstance(value, str):
        def replace(match):
            index = int(match[1]) - 1
            return image_urls[index] if 0 <= index < len(image_urls) else match[0]
        return re.sub(r'\[图片(\d{1,6})\]', replace, value)
    if isinstance(value, list):
        return [restore_image_placeholders(item, image_urls) for item in value]
    if isinstance(value, dict):
        return {key: restore_image_placeholders(item, image_urls) for key, item in value.items()}
    return value
