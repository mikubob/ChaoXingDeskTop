"""Question-bank protocol adapters derived from the reference project.

Desktop orchestration, AI and manual interactions live in chaoxing_desktop.
This module contains no terminal or INI configuration flows.
"""
import json
import random
import re
from re import sub
import threading
import time
from typing import Optional
import requests
from chaoxing_desktop.http import service_request
from api.logger import logger


class Tiku:
    DISABLE = False

    def __init__(self):
        self._conf = None
        self.name = ""
        self.api = ""
        self._last_query_request = None

    def config_set(self, config):
        self._conf = config

    def init_tiku(self):
        if self._conf is None:
            raise ValueError("题库必须通过桌面设置初始化")
        self._init_tiku()

    def _wait_for_query_request(self):
        interval = float(self._conf.get('request_interval', 0))
        now = time.monotonic()
        if self._last_query_request is not None:
            from chaoxing_desktop.control import wait
            wait(self, max(0, self._last_query_request + interval - now))
        self._last_query_request = time.monotonic()

    def _before_request(self):
        from chaoxing_desktop.control import checkpoint
        checkpoint(self)
        budget = vars(self).get('budget_check')
        if budget:
            try:
                budget()
            except RuntimeError:
                self.query_transport_status = 'budget_exhausted'
                raise
        self.query_requests = vars(self).get('query_requests', 0) + 1

    def _observe_response(self, response):
        self.query_http_status = response.status_code
        self.query_transport_status = ''
        return response

    def request(self, method, **kwargs):
        """Retry only transient transport failures; never retry invalid credentials."""
        from chaoxing_desktop.control import wait
        retries = int(self._conf.get('request_retries', 0))
        kwargs.setdefault('timeout', (10, int(self._conf.get('request_timeout', 30))))
        for attempt in range(retries + 1):
            self._wait_for_query_request()
            self._before_request()
            self.query_retries = vars(self).get('query_retries', 0) + (1 if attempt else 0)
            response = None
            try:
                response = self._observe_response(service_request(self, method, self.api, **kwargs))
                if response.status_code != 429 and response.status_code < 500:
                    return response
                if attempt == retries:
                    return response
            except requests.RequestException as exc:
                self.query_transport_status = 'timeout' if isinstance(exc, requests.Timeout) else 'network_error'
                if attempt == retries:
                    raise RuntimeError(f'{self.name}连接失败或超时，请检查地址和网络') from None
            from chaoxing_desktop.query_result import retry_delay
            wait(self, retry_delay(response, attempt))

    @staticmethod
    def get_tiku_from_config(config):
        name = config.get("provider", "")
        if not name:
            return DummyTiku()
        provider = PROVIDER_REGISTRY[name]()
        provider.config_set(config)
        return provider


class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        """初始化言溪题库实例."""
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0  # token队列计数器
        self._times = 100  # 查询次数剩余, 初始化为100, 查询后校对修正
        self.last_query_status = ''
        self.last_query_reason = ''
        self._image_encoder = None

    def _query(self, q_info: dict):
        from chaoxing_desktop.bank_question import query_title_variants
        result = self._query_once(q_info)
        if result or self.last_query_status != 'miss' or str(self._conf.get('yanxi_query_variants', 'false')).lower() != 'true':
            return result
        for title in query_title_variants(self.last_query_title, int(self._conf.get('yanxi_variant_limit', 2))):
            logger.info('言溪未命中，尝试格式等价题干（保留数字与题目含义）')
            result = self._query_once(dict(q_info, title=title))
            if result or self.last_query_status != 'miss':
                return result
        return None

    def _query_once(self, q_info: dict):
        from chaoxing_desktop.bank_question import ImageEncoder, build_yanxi_environment, restore_image_placeholders
        from chaoxing_desktop.query_result import safe_text
        self.last_query_status, self.last_query_reason = 'error', ''
        self.last_result_question, self.last_result_options = '', None
        self.last_result_ai = False
        self.last_result_tags = []
        if self._image_encoder is None:
            self._image_encoder = ImageEncoder(int(self._conf.get('request_timeout', 30)))
        self._image_encoder.control = vars(self).get('control')
        env, image_urls = build_yanxi_environment(q_info, self._image_encoder, logger.warning)
        self.last_query_title = env['title']
        # v2 follows the supplied OCS wrapper: code=0 is a diagnostic result,
        # while nonzero returns data.question + data.answer.
        while self._token_index < len(self._tokens):
            res = self.request('post', headers={'Content-Type': 'application/json'}, json=dict(env, token=self._token, v='2'))
            if res.status_code != 200:
                self.last_query_reason = f'HTTP {res.status_code}'
                logger.warning(f'{self.name}请求错误：{self.last_query_reason}')
                return None
            try:
                body = res.json()
                if not isinstance(body, dict) or 'code' not in body:
                    raise ValueError
                code = int(body['code'])
                data = body.get('data')
                if not isinstance(data, dict):
                    if code == 0 and body.get('message'):
                        data = {}
                    else:
                        raise ValueError
            except (ValueError, TypeError, AttributeError):
                self.query_transport_status = 'protocol_error'
                logger.warning(f'{self.name}响应格式错误：需要 JSON code 和 data')
                return None
            answer = data.get('answer')
            self.last_result_tags = data.get('tags') if isinstance(data.get('tags'), list) else []
            if code == 0:
                from chaoxing_desktop.answers import plain_text
                detail = plain_text(answer).strip() if isinstance(answer, str) else ''
                message = str(body.get('message') or '').strip()
                tags = data.get('tags')
                tag_text = '，'.join(str(tag.get('text') or '') for tag in tags if isinstance(tag, dict)) if isinstance(tags, list) else ''
                reason = detail or tag_text or message or '服务没有提供失败原因'
                # The live v2 API uses the generic message "请求失败" even for
                # ordinary misses. data.answer/tags are the actual diagnosis.
                if message and message not in ('请求失败', '查询失败') and message not in reason:
                    reason += '；' + message
                reason = re.sub(r'\s+', ' ', reason)[:600]
                self.last_query_reason = reason
                if any(word in reason for word in ('次数不足', '余额不足', '次数用完')):
                    self._token_index += 1
                    if self._token_index < len(self._tokens):
                        self.load_token()
                        logger.info('言溪查询次数不足，切换下一个 Token')
                        continue
                preview = safe_text(q_info.get('title', ''), limit=80, image_placeholder='[图片]')
                log_reason = safe_text(reason)
                if any(word in reason + tag_text for word in ('题库没有该题', '没有该题的答案', '未找到答案', '搜不到答案', '未收录', '未命中')):
                    self.last_query_status = 'miss'
                    logger.info(f'{self.name}未命中：{log_reason}；题干：{preview}')
                else:
                    logger.warning(f'{self.name}请求错误（HTTP 200，code=0）：{log_reason}；题干：{preview}')
                return None
            self._times = data.get('times', self._times)
            answer = restore_image_placeholders(answer, image_urls)
            if isinstance(answer, list) and all(isinstance(item, str) for item in answer):
                answer = json.dumps(answer, ensure_ascii=False) if q_info.get('type') == 'completion' else '\n'.join(answer)
            answer = answer.strip() if isinstance(answer, str) else ''
            if answer:
                self.last_query_status = 'hit'
                self.last_result_question = restore_image_placeholders(data.get('question'), image_urls) if isinstance(data.get('question'), str) else ''
                self.last_result_options = restore_image_placeholders(data.get('options'), image_urls)
                self.last_result_ai = str(data.get('ai', False)).lower() in ('true', '1')
                return answer
            self.last_query_status = 'miss'
            self.last_query_reason = '服务未返回可用答案'
            preview = safe_text(q_info.get('title', ''), limit=80, image_placeholder='[图片]')
            logger.info(f'{self.name}未命中：{self.last_query_reason}；题干：{preview}')
            return None
        self.last_query_reason = '所有 Token 查询次数均已用完'
        logger.warning(f'{self.name}请求错误：{self.last_query_reason}')
        return None

    def load_token(self):
        if self._token_index >= len(self._tokens):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = self._tokens[self._token_index]

    def _init_tiku(self):
        self.api = self._conf.get('request_endpoint', self.api).strip()
        self._tokens = [token.strip() for token in self._conf.get('tokens', '').split(',') if token.strip()]
        self.load_token()


class TikuGo(Tiku):
    # GO题（网课小工具题库）实现
    def __init__(self) -> None:
        """初始化GO题实例."""
        super().__init__()
        self.name = 'GO题（网课小工具题库）'
        self.api = 'https://q.icodef.com/wyn-nb?v=4'
        self._headers = {
            'Authorization': '',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        self._request_lock = threading.Lock()
        self._last_request_time = 0.0
        self._min_interval = 1.0
        self._retry_times = 3
        self._retry_backoff = 1.2

    def _sleep_for_next_request(self) -> None:
        with self._request_lock:
            now = time.time()
            wait_time = max(0.0, self._last_request_time + self._min_interval - now)
            self._last_request_time = now + wait_time
        if wait_time > 0:
            from chaoxing_desktop.control import wait
            wait(self, wait_time)

    def _mark_request_finished(self) -> None:
        with self._request_lock:
            self._last_request_time = time.time()

    def _request_question(self, question: str, attempt: int) -> Optional[requests.Response]:
        try:
            self._sleep_for_next_request()
            self._before_request()
            res = service_request(
                self, 'post', self.api,
                data={'question': question},
                headers=self._headers,
                verify=True,
                timeout=int(self._conf.get('request_timeout', 15))
            )
            self._observe_response(res)
            self._mark_request_finished()
            return res
        except requests.exceptions.RequestException as e:
            self.query_transport_status = 'timeout' if isinstance(e, requests.Timeout) else 'network_error'
            logger.error(f'{self.name}查询异常 ({attempt}/{self._retry_times}): {e}')
            self._mark_request_finished()
            return None

    def _parse_response(self, res: requests.Response) -> Optional[dict]:
        if res.status_code != 200:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应: {res.text}')
            return None

        try:
            res_json = res.json()
        except ValueError:
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}查询失败: 返回内容不是有效JSON, 响应: {res.text}')
            return None

        if not isinstance(res_json, dict) or 'code' not in res_json:
            self.query_transport_status = 'protocol_error'
            return None
        try:
            code = int(str(res_json.get('code', '')).strip())
        except ValueError:
            code = 0

        answer = str(res_json.get('data', '')).strip()
        msg = str(res_json.get('msg', '')).strip()
        raw_text = f'{answer} {msg}'
        is_throttled = any(key in raw_text for key in ['流控限制', '速度太快', '并发限制', '忙不过来'])
        return {
            'code': code,
            'answer': answer,
            'msg': msg,
            'is_throttled': is_throttled,
        }

    def _sleep_retry(self, attempt: int, reason: str, include_min_interval: bool = False) -> None:
        if include_min_interval:
            sleep_seconds = max(self._min_interval, self._retry_backoff * attempt)
        else:
            sleep_seconds = self._retry_backoff * attempt
        logger.warning(f'{self.name}{reason}，{sleep_seconds:.1f}s 后重试 ({attempt}/{self._retry_times})')
        from chaoxing_desktop.control import wait
        self.query_retries = vars(self).get('query_retries', 0) + 1
        wait(self, sleep_seconds)

    @staticmethod
    def _is_placeholder_answer(answer: str, msg: str) -> bool:
        return '李恒雅' in answer or '李恒雅' in msg

    def _query(self, q_info: dict):
        title = q_info.get('title', '')
        candidates = [
            title,
            re.sub(r'^【[^】]+】\s*', '', title).strip(),
            re.sub(r'^\[[^\]]+\]\s*', '', title).strip(),
        ]
        seen = set()
        normalized_titles = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                normalized_titles.append(item)

        for query_title in normalized_titles:
            answer = self._query_once(query_title)
            if answer:
                return answer
            if vars(self).get('query_http_status') in (401, 403, 429) or vars(self).get('query_transport_status'):
                break
        return None

    def _query_once(self, question: str) -> Optional[str]:
        for attempt in range(1, self._retry_times + 1):
            res = self._request_question(question, attempt)
            if res is None:
                if attempt < self._retry_times:
                    self._sleep_retry(attempt, '查询异常', include_min_interval=True)
                    continue
                break

            parsed = self._parse_response(res)
            if not parsed:
                return None

            code = parsed['code']
            answer = parsed['answer']
            msg = parsed['msg']
            is_throttled = parsed['is_throttled']

            if code != 1:
                self.last_query_reason = msg
                from chaoxing_desktop.query_result import diagnostic_status
                self.query_transport_status = diagnostic_status(msg) if is_throttled or diagnostic_status(msg) != 'service_error' else ''
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '触发流控')
                    continue
                logger.info(f"{self.name}未命中或失败: {msg or '未知错误'}")
                return None

            if not answer:
                return None

            # GO题库在未搜到时可能在 data/msg 中返回“李恒雅正在努力撰写中...”。
            if self._is_placeholder_answer(answer, msg):
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '命中流控提示')
                    continue
                return None

            return answer

        return None

    def _init_tiku(self):
        self.api = self._conf.get('request_endpoint', self.api).strip()
        self._headers['Authorization'] = self._conf.get('go_authorization', self._headers['Authorization'])
        try:
            min_interval = float(self._conf.get('go_min_interval', self._min_interval))
            if min_interval < 0:
                raise ValueError('go_min_interval must be non-negative')
            self._min_interval = min_interval
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_min_interval 无效，使用默认值 {self._min_interval}')

        try:
            retry_times = int(self._conf.get('go_retry_times', self._retry_times))
            if retry_times < 1:
                raise ValueError('go_retry_times must be >= 1')
            self._retry_times = retry_times
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_times 无效，使用默认值 {self._retry_times}')

        try:
            retry_backoff = float(self._conf.get('go_retry_backoff', self._retry_backoff))
            if retry_backoff < 0:
                raise ValueError('go_retry_backoff must be non-negative')
            self._retry_backoff = retry_backoff
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_backoff 无效，使用默认值 {self._retry_backoff}')


class TikuLike(Tiku):
    # LIKE知识库实现 参考 https://www.datam.site/
    def __init__(self) -> None:
        """初始化LIKE知识库实例."""
        super().__init__()
        self.name = 'LIKE知识库'
        self.query_api = 'https://app.datam.site/api/v1/query'
        self.balance_api = 'https://app.datam.site/api/v1/balance'
        self._model = None
        self._timeout = 300
        self._retry = True
        self._retry_times = 3
        self._tokens = []
        self._balance = {}
        self._search = False
        self._vision = True
        self._count = 0
        self._headers = {"Content-Type": "application/json"}

    def _query(self, q_info: dict = None):
        if not q_info:
            logger.error("当前无题目信息，请检查")
            return ""

        q_info_map = {"single": "【单选题】", "multiple": "【多选题】", "completion": "【填空题】", "judgement": "【判断题】"}
        q_info_prefix = q_info_map.get(q_info['type'], "【其他类型题目】")
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n"

        if q_info['type'] in ['single', 'multiple']:
            question += f"选项为: {options}\n"

        # 随机选择一个token进行查询
        token = random.choice(self._tokens)

        # 检查该token是否有余额
        if self._balance.get(token, 0) <= 0:
            logger.error(f'{self.name}当前Token查询次数不足: [已隐藏]')
            # 尝试选择其他有余额的token
            available_tokens = [t for t in self._tokens if self._balance.get(t, 0) > 0]
            if available_tokens:
                token = random.choice(available_tokens)
            else:
                self.last_query_status, self.last_query_reason = 'error', '所有 Token 次数不足'
                logger.error(f'{self.name}所有Token查询次数都不足')
                return None

        ans = None
        try_times = 0

        # 尝试查询，直到成功或达到重试次数
        attempts = 1 + (self._retry_times if self._retry else 0)
        while not ans and try_times < attempts:
            ans = self._query_single(token, question)
            try_times += 1
            if not ans and (vars(self).get('query_http_status') in (400, 401, 403) or vars(self).get('query_transport_status') in ('protocol_error', 'auth_error', 'quota_error', 'budget_exhausted')):
                break
            if try_times > 1:
                self.query_retries = vars(self).get('query_retries', 0) + 1
            if ans:  # 如果查询成功，减少余额
                self._balance[token] -= 1
                logger.info(f'使用Token [已隐藏] 查询成功，剩余次数: {self._balance[token]}')
                break
            elif try_times < attempts:
                logger.warning(f'使用Token [已隐藏] 查询失败，进行第 {try_times + 1} 次重试...')

        # 10次查询后更新余额
        self._count = (self._count + 1) % 10
        if self._count == 0:
            self.update_times()

        return ans

    def _query_single(self, token: str = "", query: str = "") -> str:
        """
        查询单个问题的答案
        
        Args:
            token: API访问令牌
            query: 查询的问题内容
            
        Returns:
            查询到的答案，如果失败则返回None
        """
        # 验证输入参数
        if not token:
            logger.error(f'{self.name}查询失败: 未提供有效的token')
            return None

        if not query:
            logger.error(f'{self.name}查询失败: 查询内容为空')
            return None

        # 设置请求头
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'

        # 准备请求数据
        request_data = {
            'query': query,
            'model': self._model if self._model else '',
            'search': self._search,
            'vision': self._vision
        }

        # 发送API请求
        try:
            self._wait_for_query_request()
            self._before_request()
            res = service_request(
                self, 'post', self.query_api,
                json=request_data,
                headers=temp_headers,
                verify=True,
                timeout=self._timeout  # 添加超时设置
            )
        except requests.exceptions.Timeout:
            self.query_transport_status = 'timeout'
            logger.error(f'{self.name}查询超时: 请求超过{self._timeout}秒')
            return None
        except requests.exceptions.ConnectionError:
            self.query_transport_status = 'network_error'
            logger.error(f'{self.name}网络连接错误: 无法连接到API服务器')
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常: \n{e}')
            return None
        except Exception as e:
            logger.error(f'{self.name}查询发生未知错误: \n{e}')
            return None

        self._observe_response(res)
        # 处理HTTP响应
        if res.status_code == 200:
            return self._parse_response(res)
        elif res.status_code == 401:
            logger.error(f'{self.name}认证失败: 请检查Token是否正确或已过期')
        elif res.status_code == 429:
            logger.error(f'{self.name}请求过于频繁: 已达到API速率限制')
        elif res.status_code == 500:
            logger.error(f'{self.name}服务器内部错误: API服务暂时不可用')
        elif res.status_code == 400:
            logger.error(f'{self.name}请求参数错误: 请检查查询内容格式')
        elif res.status_code == 403:
            logger.error(f'{self.name}访问被拒绝: 可能是Token权限不足')
        else:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应内容: \n{res.text}')

        return None

    def _parse_response(self, response):
        """
        解析API响应
        
        Args:
            response: HTTP响应对象
            
        Returns:
            解析后的答案，如果解析失败则返回None
        """
        try:
            res_json = response.json()
        except json.JSONDecodeError:
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}响应解析失败: 响应不是有效的JSON格式')
            return None
        except Exception as e:
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}响应解析异常: {e}')
            return None

        if not isinstance(res_json, dict):
            self.query_transport_status = 'protocol_error'
            return None
        # 记录响应消息
        msg = res_json.get('message', '')
        if msg:
            logger.info(f'{self.name}响应消息: {msg}')
            from chaoxing_desktop.query_result import diagnostic_status
            status = diagnostic_status(msg)
            if status != 'service_error':
                self.query_transport_status = status
                self.last_query_reason = str(msg)
                return None

        results = res_json.get('results', {})
        if not results or not isinstance(results, dict):
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}查询结果格式错误: API返回结果中results字段格式不正确')
            return None

        output = results.get('output', None)
        if output is None or not isinstance(output, dict):
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}查询结果中output字段格式错误或不存在')
            return None

        q_type = output.get('questionType', None)
        if q_type is None:
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}查询结果中questionType字段不存在')
            return None

        answer = output.get('answer', None)
        if answer is None:
            self.query_transport_status = 'protocol_error'
            logger.error(f'{self.name}查询结果中answer字段不存在')
            return None

        # 根据题目类型提取答案
        return self._extract_answer_by_type(q_type, answer)

    def _extract_answer_by_type(self, q_type: str, answer: dict) -> str:
        """
        根据题目类型提取答案
        
        Args:
            q_type: 题目类型
            answer: 答案字典
            
        Returns:
            提取的答案文本
        """
        if not isinstance(answer, dict):
            logger.error(f'{self.name}答案格式错误: 不是有效的字典格式')
            return None

        if q_type == "CHOICE":
            selected_options = answer.get('selectedOptions', None)
            if selected_options is not None:
                if isinstance(selected_options, list) and selected_options:
                    # 过滤掉None和空字符串
                    valid_options = [opt for opt in selected_options if opt is not None and str(opt).strip()]
                    if valid_options:
                        return '\n'.join(str(opt) for opt in valid_options)
                    else:
                        logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
                else:
                    logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
            else:
                logger.error(f'{self.name}CHOICE类型题目缺少selectedOptions字段')
        elif q_type == "FILL_IN_BLANK":
            blanks = answer.get('blanks', None)
            if blanks is not None:
                if isinstance(blanks, list) and blanks:
                    # 过滤掉None和空字符串
                    valid_blanks = [blank for blank in blanks if blank is not None and str(blank).strip()]
                    if valid_blanks:
                        return json.dumps(blanks, ensure_ascii=False) if all(isinstance(blank, str) for blank in blanks) else None
                    else:
                        logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
                else:
                    logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
            else:
                logger.error(f'{self.name}FILL_IN_BLANK类型题目缺少blanks字段')
        elif q_type == "JUDGMENT":
            is_correct = answer.get('isCorrect', None)
            if is_correct is not None:
                return "正确" if is_correct else "错误"
            else:
                logger.error(f'{self.name}JUDGMENT类型题目缺少isCorrect字段')
        else:
            otherText = answer.get('otherText', None)
            if otherText is not None:
                return str(otherText)
            else:
                logger.error(f'{self.name}未知题目类型{q_type}且缺少otherText字段')

        return None

    def get_api_balance(self, token: str = ""):
        if not token:
            logger.error(f'{self.name}获取余额失败: 未提供有效的token')
            return 0

        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        try:
            res = service_request(
                self, 'get', self.balance_api,
                headers=temp_headers,
                verify=True,
                timeout=self._timeout
            )
            if res.status_code == 200:
                res_json = res.json()
                return int(res_json.get("balance", 0))
            else:
                logger.error(f'{self.name}请求余额接口失败，状态码: {res.status_code}')
                return 0
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}获取余额超时: 请求超过30秒')
            return 0
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到余额查询API服务器')
            return 0
        except ValueError:  # json解析错误或int转换错误
            logger.error(f'{self.name}余额响应解析失败: 响应格式不正确')
            return 0
        except Exception as e:
            logger.error(f'{self.name}Token余额查询过程中出现错误: {e}')
            return 0

    def update_times(self) -> None:
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何Token, 无法更新余额')
            return
        for token in self._tokens:
            balance = self.get_api_balance(token)
            self._balance[token] = balance
            logger.info(
                f"当前LIKE知识库Token: [已隐藏] 的剩余查询次数为: {balance} (仅供参考, 实际次数以查询结果为准)")

    def load_tokens(self) -> None:
        tokens_str = self._conf.get('tokens')
        if not tokens_str:
            logger.error(f'{self.name}配置中未找到tokens')
            self._tokens = []
            return
        if ',' in tokens_str:
            tokens = [token.strip() for token in tokens_str.split(',') if token.strip()]
        else:
            tokens = [tokens_str.strip()] if tokens_str.strip() else []
        self._tokens = tokens
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何有效的Token')

    def load_config(self) -> None:
        self.query_api = self._conf.get('request_endpoint', self.query_api).strip()
        self._timeout = int(self._conf.get('request_timeout', 300))
        # 从配置中获取参数，提供默认值
        self._search = str(self._conf.get('likeapi_search', 'false')).lower() == 'true'
        self._model = self._conf.get('likeapi_model', None)
        self._vision = str(self._conf.get('likeapi_vision', 'true')).lower() == 'true'
        self._retry = str(self._conf.get('likeapi_retry', 'true')).lower() == 'true'
        self._retry_times = int(self._conf.get("likeapi_retry_times", 3))

    def _init_tiku(self) -> None:
        self.load_config()
        self.load_tokens()
        if self._tokens:
            self.update_times()
        else:
            logger.error(f'{self.name}初始化失败: 未加载任何有效的Token')
            self.DISABLE = True


class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self) -> None:
        """初始化TikuAdapter题库实例."""
        super().__init__()
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = self.request('post',
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
        )
        if res.status_code == 200:
            res_json = res.json()
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['bestAnswer']):
                from chaoxing_desktop.query_result import diagnostic_status
                diagnosis = diagnostic_status(res_json.get('message') or res_json.get('msg') or '')
                if diagnosis != 'service_error':
                    self.query_transport_status = diagnosis
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            values = res_json['answer']['bestAnswer']
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError('Adapter 答案格式错误')
            return json.dumps(values, ensure_ascii=False) if q_info.get('type') == 'completion' else sep.join(values).strip()
        return None

    def _init_tiku(self):
        self.api = self._conf['url']



class DummyTiku(Tiku):
    DISABLE = True


PROVIDER_REGISTRY = {
    "TikuYanxi": TikuYanxi, "TikuGo": TikuGo,
    "TikuLike": TikuLike, "TikuAdapter": TikuAdapter,
}
