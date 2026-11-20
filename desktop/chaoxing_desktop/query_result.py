"""Immutable query snapshots and bounded, credential-free presentation data."""
from dataclasses import dataclass
import copy
import re
import time
from types import SimpleNamespace
from .text import plain_text, question_parts

QUERY_STATES = {'hit', 'miss', 'auth_error', 'quota_error', 'rate_limited',
                'timeout', 'network_error', 'protocol_error', 'service_error', 'budget_exhausted'}
STATE_TEXT = dict(hit='返回候选', miss='未命中', auth_error='认证失败', quota_error='额度不足',
                  rate_limited='服务限流', timeout='请求超时', network_error='连接失败',
                  protocol_error='响应格式错误', service_error='服务错误', budget_exhausted='本题请求预算已用完')


@dataclass(frozen=True)
class AnswerCandidate:
    answer: str
    title: str = ''
    options: object = None
    ai: bool = False
    tags: tuple = ()


@dataclass(frozen=True)
class BankQueryResult:
    provider: str
    status: str
    candidates: tuple = ()
    reason: str = ''
    elapsed_ms: int = 0
    requests: int = 0
    retries: int = 0

    def review_context(self, candidate):
        return SimpleNamespace(last_result_question=candidate.title,
                               last_result_options=candidate.options, last_result_ai=candidate.ai)


def retry_delay(response, attempt):
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    value = str(getattr(response, 'headers', {}).get('Retry-After', ''))
    try:
        seconds = float(value) if value.isdigit() else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        return min(60, max(0, seconds))
    except (ValueError, TypeError, OverflowError):
        return min(2 ** attempt, 8)


def diagnostic_status(reason):
    """Only classify explicit diagnoses; ambiguous failures remain service_error."""
    value = str(reason).lower()
    if any(x in value for x in ('余额不足', '次数不足', '次数用完', '次数均已用完', 'quota exceeded')):
        return 'quota_error'
    if any(x in value for x in ('token 无效', 'token无效', 'token已过期', '认证失败', 'unauthorized', 'invalid token')):
        return 'auth_error'
    if any(x in value for x in ('流控', '速度太快', '并发限制', '忙不过来', 'rate limit')):
        return 'rate_limited'
    return 'service_error'


def query_bank(name, bank, question):
    bank._http_deadline = time.monotonic() + 600
    # Every call owns its metadata, including failures after a previous hit.
    for key, value in dict(last_result_question='', last_result_options=None, last_result_ai=False,
                           last_result_tags=[], last_query_status='', last_query_reason='',
                           query_http_status=0, query_transport_status='', query_requests=0,
                           query_retries=0).items():
        # Real HTTP adapters expose mutable last-result state; legacy data-only
        # providers may supply immutable metadata alongside their callable.
        if key.startswith('last_result_') and bank.__class__.__module__ not in ('api.answer', 'chaoxing_desktop.declarative'):
            continue
        setattr(bank, key, value)
    started = time.monotonic()
    status, answer, reason = 'miss', None, ''
    try:
        answer = bank._query(copy.deepcopy(question))
        if answer is not None and not isinstance(answer, str):
            raise ValueError('answer must be text')
        reason = str(vars(bank).get('last_query_reason') or '')
        http = vars(bank).get('query_http_status', 0)
        transport = vars(bank).get('query_transport_status', '')
        if answer:
            status = 'hit'
        elif transport in QUERY_STATES:
            status = transport
        elif http in (401, 403):
            status = 'auth_error'
        elif http == 429:
            status = 'rate_limited'
        elif isinstance(http, int) and http >= 500:
            status = 'service_error'
        elif vars(bank).get('last_query_status') == 'error':
            status = diagnostic_status(reason)
        else:
            status = 'miss'
    except (ValueError, TypeError, KeyError, AttributeError):
        status = 'protocol_error'
    except PermissionError:
        status = 'auth_error'
    except Exception as exc:
        import requests
        status = 'timeout' if isinstance(exc, requests.Timeout) else 'network_error' if isinstance(exc, requests.RequestException) else vars(bank).get('query_transport_status') or 'service_error'
    candidate = ()
    if status == 'hit':
        candidate = (AnswerCandidate(answer, str(vars(bank).get('last_result_question') or ''),
                                     copy.deepcopy(vars(bank).get('last_result_options')),
                                     bool(vars(bank).get('last_result_ai', False)),
                                     tuple(copy.deepcopy(vars(bank).get('last_result_tags') or [])[:8])),)
    if status == 'hit' and vars(bank).get('last_candidates'):
        candidate = tuple(copy.deepcopy(bank.last_candidates[:100]))
    return BankQueryResult(name, status, candidate, reason or STATE_TEXT[status],
                           max(0, round((time.monotonic() - started) * 1000)),
                           int(vars(bank).get('query_requests', 0)), int(vars(bank).get('query_retries', 0)))


def safe_text(value, redactor=lambda text: text, limit=1200, image_placeholder=None):
    text = (plain_text(value) if image_placeholder is None else
            ''.join(image_placeholder if image else part for image, part in question_parts(value)))
    text = re.sub(r'data:[^\s<>]+', '[图片数据]', text, flags=re.I)
    # URLs can embed credentials in userinfo, path or query. Results need no live URLs.
    text = re.sub(r'(?:https?://|(?<!\w)//(?=[\w.-]+\.))[^\s<>\"\']+', '[链接]', text, flags=re.I)
    return redactor(text)[:limit]


def safe_record(value, redactor=lambda text: text, depth=0):
    if depth > 5:
        return ''
    if isinstance(value, dict):
        machine_fields = {'id', 'run_id', 'task_id', 'work_id', 'question_id', 'attempt',
                          'stage', 'source', 'match_mode', 'kind', 'status', 'answer_letters'}
        question_fields = {'title', 'options', 'answer', 'candidate', 'returned_title'}
        result = {}
        for key, item in list(value.items())[:32]:
            if isinstance(item, str) and key in machine_fields:
                clean = safe_text(item)
            elif isinstance(item, str) and key in question_fields:
                clean = safe_text(item, redactor, image_placeholder='[图片]')
            else:
                clean = safe_record(item, redactor, depth + 1)
            result[str(key)[:64]] = clean
        return result
    if isinstance(value, (list, tuple)):
        return [safe_record(v, redactor, depth + 1) for v in value[:16]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return safe_text(value, redactor)
