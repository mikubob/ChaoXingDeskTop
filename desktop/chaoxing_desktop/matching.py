"""Conservative text matching: differences in numbers/polarity are never fuzzy."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from html import unescape
import re
import unicodedata

from .bank_question import clean_title, option_lines, text_with_images

PROTECTED = ('不', '非', '未', '无', '错误', '正确', '最大', '最小', '最高', '最低',
             '增加', '减少', '上升', '下降', '大于', '小于', '至少', '至多', '超过',
             '生产性', '生活性', '进口', '出口', '必须', '可以', '不得', '允许')
PROTECTED += ('中国', '美国', '日本', '我国', '他国', '传统产业', '新兴产业',
              '实体经济', '虚拟经济', '内需', '外需', '供给', '需求', '原因', '结果')
NUMBER = re.compile(r'[-+]?\d+(?:\.\d+)?(?:万亿元|亿元|万元|元|亿|万|%|年)?', re.I)
UNITS = {'万亿元': (Decimal('1e12'), '元'), '亿元': (Decimal('1e8'), '元'),
         '万元': (Decimal('1e4'), '元'), '元': (Decimal(1), '元'),
         '亿': (Decimal('1e8'), ''), '万': (Decimal('1e4'), ''),
         '%': (Decimal(1), '%'), '年': (Decimal(1), '年')}


def display_text(value):
    # HTMLParser keeps mathematical '<' intact and includes image URLs.
    return unicodedata.normalize('NFKC', unescape(text_with_images(value)[0])).strip()


def logical_text(value):
    return display_text(value).translate(str.maketrans({'−': '-', '﹣': '-'})).replace('>=', '≥').replace('<=', '≤').replace('!=', '≠')


def has_expression(text):
    return bool(re.search(r'[=+*/^<>≤≥≠×÷-]|[a-z]\s*\(', text, re.I))


def canonical(value):
    text = logical_text(value).casefold()
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)
    text = re.sub(r'百分之\s*(\d+(?:\.\d+)?)', r'\1%', text)
    def quantity(match):
        part = re.fullmatch(r'([-+]?\d+(?:\.\d+)?)(.*)', match[0])
        try:
            factor, unit = UNITS.get(part[2], (Decimal(1), ''))
            number = Decimal(part[1]) * factor
            return '[' + format(number.normalize(), 'f') + unit + ']'
        except (InvalidOperation, AttributeError):
            return match[0]
    text = NUMBER.sub(quantity, text)
    # Preserve comparison/arithmetic operators and all words.
    text = re.sub(r'[\s\u200b\ufeff"\'“”‘’，,。？?；;、:：【】]', '', text)
    text = re.sub(r'[!！]+$', '', text)
    if not has_expression(text):
        text = text.replace('(', '').replace(')', '')
    return re.sub(r'(?<!\d)\.|\.(?!\d)', '', text)


def signature(value):
    source = logical_text(value)
    text = source.casefold()
    key = canonical(value)
    code_literals = tuple(re.findall(r'(["\'])(.*?)\1', text)) if re.search(r'字符串|字符|\bprint\b|\blen\b|[=+*/]', text) else ()
    return (tuple(re.findall(r'\[[^\]]+\]', key)),
            tuple(re.findall(r'[零〇一二三四五六七八九十百千万亿两]+', key)),
            tuple((word, text.count(word)) for word in PROTECTED if word in text),
            tuple(re.findall(r'\b(?:not|no|never|except|least|most|increase|decrease|before|after|true|false|cannot|without|must|may)\b', text)),
            tuple(re.findall(r'[<>=≤≥≠±+*/^×÷!−-]', text.rstrip('!！'))),
            tuple(re.findall(r'[()]', text)) if has_expression(text) else (),
            tuple(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', source)) if has_expression(source) else (),
            tuple(text_with_images(value)[1]), code_literals)


def similarity(left, right):
    a, b = canonical(left), canonical(right)
    if not a or not b or signature(left) != signature(right):
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 8 or max(len(a), len(b)) > 4096:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def title_similarity(left, right):
    a, b = clean_title(display_text(left)), clean_title(display_text(right))
    if canonical(a) == canonical(b) and signature(a) == signature(b):
        return 1.0
    left_images = text_with_images(a)[1]
    same_images = bool(left_images) and left_images == text_with_images(b)[1]
    if not same_images and min(len(canonical(a)), len(canonical(b))) < 12:
        return 0.0
    return similarity(a, b)


def answer_body(answer):
    answer = unicodedata.normalize('NFKC', unescape(answer)).strip()
    return re.sub(r'^(?:(?:本题)?(?:正确|参考)?答案\s*(?:[:：]\s*|(?:是|为)\s*[:：]?\s*))', '', answer).strip()


@dataclass
class ChoiceMatch:
    pairs: list
    mode: str
    score: float = 1.0


def match_part(part, pairs, fuzzy, threshold, margin):
    # Exact option content outranks a letter interpretation (e.g. option text B).
    exact = [(k, t) for k, t in pairs if t.strip() == part.strip()]
    if exact:
        return ChoiceMatch(exact, 'exact') if len(exact) == 1 else None
    label = re.fullmatch(r'\(?([A-Za-z])\)?', part.strip())
    part = re.sub(r'^\s*[A-Za-z][.、．:：\s]+', '', part).strip()
    formatted = [(k, t) for k, t in pairs if canonical(t) == canonical(part) and signature(t) == signature(part)]
    if formatted:
        return ChoiceMatch(formatted, 'formatted') if len(formatted) == 1 else None
    if label:
        found = [(k, t) for k, t in pairs if k == label[1].upper()]
        if len(found) == 1:
            return ChoiceMatch(found, 'letter')
    if not fuzzy:
        return None
    ranked = sorted(((similarity(part, text), key, text) for key, text in pairs), reverse=True)
    if not ranked or ranked[0][0] < threshold:
        return None
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    if ranked[0][0] - runner_up < margin:
        return None
    score, key, text = ranked[0]
    return ChoiceMatch([(key, text)], 'fuzzy', score)


def match_choices(answer, pairs, kind, fuzzy=True, threshold=.92, margin=.08):
    if not isinstance(answer, str) or not answer.strip() or not pairs:
        return None
    body = answer_body(answer)
    # A whole option may itself contain commas, so match it before splitting.
    whole = match_part(body, pairs, False, threshold, margin)
    if whole:
        return whole
    letters = body.strip('()[] ')
    if re.fullmatch(r'[A-Za-z](?:[\s,，、;；#|]*[A-Za-z])*', letters):
        keys = set(re.findall(r'[A-Za-z]', letters.upper()))
        found = [(key, text) for key, text in pairs if key in keys]
        if len(found) == len(keys) and (kind == 'multiple' or len(found) == 1):
            return ChoiceMatch(found, 'letter')
        return None
    if kind == 'single':
        fragments = [p.strip() for p in re.split(r'[\n#|;；]', body) if p.strip()]
        if len(fragments) > 1:
            # Some services split one compound option into answer fragments.
            # Accept only a unique exact/format match of the complete content.
            merged = match_part(''.join(fragments), pairs, False, threshold, margin)
            if merged and merged.mode != 'letter':
                return merged
    parts = [body] if kind == 'single' else [p.strip() for p in re.split(r'[\n#|;；]', body) if p.strip()]
    if kind == 'multiple' and len(parts) == 1:
        # Only split alternative delimiters when the whole answer isn't an option.
        parts = [p.strip() for p in re.split(r'[,，、]', body) if p.strip()]
    found, modes, scores = [], [], []
    for part in parts:
        match = match_part(part, pairs, fuzzy, threshold, margin)
        if match is None:
            return None  # Never return a partially matched multiple-choice answer.
        found.extend(match.pairs)
        modes.append(match.mode)
        scores.append(match.score)
    unique = dict(found)
    if not unique or (kind == 'single' and len(unique) != 1):
        return None
    return ChoiceMatch(list(unique.items()), 'fuzzy' if 'fuzzy' in modes else 'formatted', min(scores))


def result_options(value):
    """Convert optional bank result options to labelled content for reordering."""
    if isinstance(value, dict):
        return [(str(k).upper(), str(v)) for k, v in value.items() if re.fullmatch('[A-Za-z]', str(k))]
    lines = option_lines(value)
    if len(lines) > 26:
        return []
    pairs = []
    for index, line in enumerate(lines):
        match = re.match(r'^\s*([A-Za-z])[.、．:：\s]+(.+)$', line)
        pairs.append((match[1].upper(), match[2]) if match else (chr(65 + index), line))
    return pairs


class AnswerMemory:
    """Bounded, in-memory bank answers; no AI/random answers or disk cache."""
    def __init__(self, capacity=200):
        self.rows = []
        self.capacity = capacity

    def clear(self):
        self.rows.clear()

    def remember(self, question, selected):
        if question.get('type') not in ('single', 'multiple'):
            return
        from .answers import option_pairs
        options = option_pairs(question)
        entry = dict(title=question.get('title', ''), kind=question['type'],
                     options=sorted((canonical(t), signature(t)) for _, t in options),
                     images=tuple(text_with_images(question.get('title'))[1]),
                     selected=[text for _, text in selected])
        if sum(len(str(v)) for v in entry.values()) > 16_384:
            return
        self.rows.append(entry)
        del self.rows[:-self.capacity]

    def find(self, question, threshold=.96, margin=.08):
        from .answers import option_pairs
        pairs = option_pairs(question)
        options = sorted((canonical(t), signature(t)) for _, t in pairs)
        images = tuple(text_with_images(question.get('title'))[1])
        candidates = []
        for row in self.rows:
            if row['kind'] != question.get('type') or row['options'] != options or row['images'] != images:
                continue
            score = title_similarity(row['title'], question.get('title', ''))
            if score < threshold:
                continue
            found = []
            for value in row['selected']:
                match = match_part(value, pairs, False, 1, 1)
                if match is None:
                    break
                found.extend(match.pairs)
            else:
                candidates.append((score, found))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best = candidates[0]
        best_keys = {k for k, _ in best}
        # A second plausible answer is ambiguity, even if the first ranks higher.
        if any({k for k, _ in found} != best_keys and best_score - score < margin for score, found in candidates[1:]):
            return None
        return ChoiceMatch(best, 'memory', best_score)
