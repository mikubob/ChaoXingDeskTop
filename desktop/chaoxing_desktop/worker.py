from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import sys
import threading
import queue
import uuid
from .control import RunControl, Cancelled, ResumeFailed, checkpoint, wait as controlled_wait
from .query_result import safe_record

from .config import Redactor, resource_root, select_courses


class Channel:
    def __init__(self, output, input_stream, redactor):
        self.output, self.input_stream, self.redactor = output, input_stream, redactor
        self.lock = threading.Lock()
        self.input_lock = threading.Lock()
        self.run_id = uuid.uuid4().hex
        self.control = None

    def send(self, event_type: str, **values):
        if "message" in values:
            values["message"] = self.redactor(str(values["message"]))
            if event_type == "log" and len(values["message"]) > 4096:
                values["message"] = values["message"][:4075] + " …[日志过长，已截断]"
        if event_type == 'question_result':
            values = safe_record(values, self.redactor)
        if event_type == 'discussion_result':
            from .discussion_model import safe_record as safe_discussion
            values = dict(record=safe_discussion(values['record'], self.redactor))
        if event_type in ('activity_result', 'activity_preview'):
            from .activity_model import safe_record as safe_activity
            values = dict(record=safe_activity(values['record'], self.redactor))
        values.setdefault('run_id', self.run_id)
        with self.lock:
            self.output.write(json.dumps({"type": event_type, **values}, ensure_ascii=False) + "\n")
            self.output.flush()

    def enable_control(self):
        self.control = RunControl(self.send)
        threading.Thread(target=self.control.read, args=(self.input_stream,), daemon=True).start()

    def ask(self, prompt="", **context):
        with self.input_lock:
            request_id = uuid.uuid4().hex
            self.send("input", message=prompt, request_id=request_id, **context)
            if self.control:
                while True:
                    self.control.checkpoint()
                    try:
                        reply = self.control.replies.get(timeout=.1)
                    except queue.Empty:
                        if self.control.eof:
                            raise Cancelled()
                        continue
                    if reply.get('request_id') == request_id:
                        return str(reply.get('answer', ''))
            line = self.input_stream.readline()
            if not line:
                raise EOFError("交互已取消")
            reply = json.loads(line)
            if reply.get("cancel"):
                # Exit the whole worker: upstream catches EOFError in some manual modes.
                self.send("cancelled", message="用户取消交互，任务停止。")
                self.output.flush()
                os._exit(2)
            return str(reply.get("answer", ""))


class EventStream:
    encoding = "utf-8"
    def __init__(self, channel):
        self.channel = channel

    def write(self, text):
        if text.strip():
            self.channel.send("log", message=text.strip(), level="INFO")
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def load_core(channel):
    core_path = resource_root() / "vendor/chaoxing"
    sys.path.insert(0, str(core_path))
    # Imports and upstream print calls must never corrupt the JSON protocol.
    sys.stdout = EventStream(channel)
    sys.stderr = EventStream(channel)
    from api.logger import logger
    logger.remove()
    logger.add(lambda msg: channel.send("log", message=msg.record["message"], level=msg.record["level"].name),
               level="INFO", colorize=False, backtrace=False, diagnose=False)
    from api.cxsecret_font import validate_font_resources
    validate_font_resources()
    from . import runtime
    return runtime


def demo(channel, command):
    courses = [dict(courseId="demo-101", clazzId="01", cpi="demo", title="Python 程序设计 · 演示"),
               dict(courseId="demo-102", clazzId="02", cpi="demo", title="大学英语 · 演示"),
               dict(courseId="demo-103", clazzId="03", cpi="demo", title="信息素养 · 演示")]
    channel.send("log", message="离线演示模式：所有课程与任务均为示例，没有连接超星。", level="INFO")
    if command["action"] == "courses":
        controlled_wait(channel, .2)
        channel.send("courses", courses=courses, demo=True)
        return
    chosen = select_courses(courses, command.get("selected", []))
    tasks = [dict(id=f"{c['courseId']}:{i}", course=c["title"], title=t)
             for c in chosen for i, t in enumerate(("01 · 视频学习", "02 · 阅读材料", "03 · 章节练习"))]
    channel.send("tasks", tasks=tasks)
    for task in tasks:
        checkpoint(channel)
        channel.send("task", id=task["id"], status="running")
        channel.send("log", message=f"正在处理 {task['course']} / {task['title']}", level="INFO")
        if '视频' in task['title']:
            for position in (0, 40, 80, 120):
                channel.send('media', id=task['id'], name='离线演示视频', kind='Video',
                             duration=120, position=position, verified=position == 120)
                controlled_wait(channel, .15)
        else:
            controlled_wait(channel, .6)
        if '练习' in task['title']:
            from types import SimpleNamespace
            from .answers import AnswerRouter
            router = AnswerRouter(command['config'], channel, [('演示题库', SimpleNamespace(_query=lambda q: '第二项'))])
            router.set_context(task_id=task['id'], work_id='demo-work', attempt=0)
            router.query_all([dict(id='demo-question', title='以下哪项是演示答案？', type='single', options='A.第一项\nB.第二项')])
            channel.send('discussion_result', record=dict(
                id=task['id']+':discussion', run_id=channel.run_id, task_id=task['id'],
                course=task['course'], chapter='章节讨论 · 离线演示',
                title='大学生如何在学习与实践中承担社会责任？', level='medium', source='ai',
                stage='draft', task_status='pending', elapsed_ms=0,
                reason='离线示例草稿，未调用 AI、未发布。开启真实讨论功能后按独立配置处理。',
                content='大学生可以通过扎实学习专业知识、参与社会实践与志愿服务，在具体行动中提升能力、承担责任。'))
            from .activity import ActivityGenerator, preview_context
            from .answers import AnswerRouter
            context = preview_context('你对本课程有什么建议？')
            local_cfg = dict(choice_strategy='random', text_strategy='fixed_good', reply_level='medium')
            answers = ActivityGenerator(AnswerRouter(command['config'], channel)).generate(context, local_cfg)
            channel.send('activity_result', record=dict(
                id=task['id']+':activity', run_id=channel.run_id, task_id=task['id'], course=task['course'],
                chapter='混合问卷 · 离线演示', title=context.title, kind='survey', stage='draft',
                submit_status='draft', task_status='pending', elapsed_ms=0,
                reason='离线示例：随机选择 + 固定好，AI 调用 0 次，未访问课程或提交。',
                questions=context.questions, answers=answers))
        channel.send("task", id=task["id"], status="success")
    channel.send("done", success=len(tasks), failed=0, skipped=0, pending=0, demo=True)


def run_tasks(channel, tasks, process, config, result_enum, verify=None):
    """Bounded chapter scheduling, retry in rounds so dependent chapters can unlock."""
    pending = list(tasks)
    statuses = {}
    for attempt in range(5):
        checkpoint(channel)
        retry = []
        with ThreadPoolExecutor(max_workers=config["jobs"]) as executor:
            def execute(task):
                checkpoint(channel)
                channel.send("task", id=task["id"], status="running")
                return process(task)
            remaining = iter(pending)
            futures = {}
            def fill_slots():
                while len(futures) < config["jobs"]:
                    checkpoint(channel)
                    task = next(remaining, None)
                    if task is None:
                        break
                    futures[executor.submit(execute, task)] = task
            fill_slots()
            while futures:
                checkpoint(channel)
                completed, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                # Pop completed futures promptly; do not retain every result or
                # exception traceback until thousands of chapters have finished.
                for future in completed:
                    task = futures.pop(future)
                    handle_result(future, task, attempt, retry, statuses, channel, config, result_enum)
                    if verify is not None and statuses[task['id']] in ('processed', 'pending'):
                        checkpoint(channel)
                        statuses[task['id']] = verify(task)
                        channel.send('task', id=task['id'], status=statuses[task['id']])
                fill_slots()
        if not retry:
            break
        channel.send("log", message=f"等待重试 {len(retry)} 个章节（第 {attempt + 2}/5 轮）。", level="WARNING")
        controlled_wait(channel, config["retry_interval"])
        pending = retry
    return statuses


def handle_result(future, task, attempt, retry, statuses, channel, config, result_enum):
    try:
        result = future.result()
    except Exception as exc:
        channel.send("log", message=f"{task['title']}：{type(exc).__name__}: {exc}", level="ERROR")
        result = result_enum.ERROR
    if result == result_enum.SUCCESS:
        status = "processed"
    elif getattr(result_enum, 'PENDING', None) is not None and result == result_enum.PENDING:
        status = "pending"
    elif result == result_enum.NOT_OPEN:
        action = config["notopen_action"]
        if action == "ask":
            answer = channel.ask(f"章节「{task['title']}」未开放。", kind="choice",
                                 choices=[{"label": "跳过当前章节", "value": "y"},
                                          {"label": "停止任务", "value": "n"}])
            if answer.strip().lower() not in ("y", "yes"):
                channel.send("cancelled", message="用户停止了未开放章节的处理。")
                os._exit(2)
        if action in ("ask", "continue"):
            status = "skipped"
        elif attempt < 4:
            status = "retry"
            retry.append(task)
        else:
            status = "skipped"
    elif attempt < 4:
        status = "retry"
        retry.append(task)
    else:
        status = "failed"
    statuses[task["id"]] = status
    channel.send("task", id=task["id"], status=status)


def real(channel, command):
    checkpoint(channel)
    core = load_core(channel)
    checkpoint(channel)
    account_id = command.get('account_id')
    verifying = command['action'] in ('verify', 'course_progress')
    if verifying and not account_id:
        raise ValueError('只读核验需要明确的账号会话。')
    if account_id:
        from .accounts import AccountStore
        from api.cookies import configure_account
        store = AccountStore(Path(command['data_dir']))
        row = store.get(account_id)
        if not row or row['username'] != command['config']['common']['username']:
            raise ValueError('账号信息已改变，请重新选择账号。')
        if verifying and (not row['uid'] or command.get('account_uid') != row['uid']):
            raise ValueError('核验身份不完整或已变化，请重新读取课程。')
        if verifying and command.get('cookie_mtime_ns') != store.cookie_path(account_id).stat().st_mtime_ns:
            raise ValueError('核验会话文件已变化，请重新读取课程。')
        configure_account(store.cookie_path(account_id), row['uid'], command['config']['common']['use_cookies'])
    common, notification_config = (command["config"][s] for s in ("common", "notification"))
    # Listing courses must not initialize paid question-bank services.
    if verifying and (not common['use_cookies'] or common['password']):
        raise ValueError('只读核验仅使用已读取课程的 Cookie 会话，请先重新读课。')
    if verifying:
        channel.send('verification_progress', phase='session', message='正在校验账号会话…', completed=0, total=0, reads=0, failures=0, elapsed_ms=0)
    cx = core.create_client(command["config"], channel, courses_only=command["action"] in ('courses', 'verify', 'course_progress'))
    checkpoint(channel)
    login = cx.login(login_with_cookies=common["use_cookies"])
    if not login.get("status"):
        raise RuntimeError(login.get("msg", "登录失败"))
    if account_id:
        from .accounts import cookie_uid
        from api.base import SessionManager
        from api.cookies import save_cookies
        session = SessionManager.get_session()
        uid = cookie_uid(session.cookies.get_dict())
        if not uid or (row['uid'] and row['uid'] != uid):
            raise ValueError('平台会话与当前账号不一致，请重新登录或添加为其他账号。')
        if not verifying:
            save_cookies(session)
            channel.send('session_ready', account_id=account_id, uid=uid,
                         cookie_mtime_ns=store.cookie_path(account_id).stat().st_mtime_ns)
    checkpoint(channel)
    if verifying:
        channel.send('verification_progress', phase='courses', message='会话已校验，正在读取课程身份…', completed=0, total=0, reads=0, failures=0, elapsed_ms=0)
    courses = list({(str(c["courseId"]), str(c["clazzId"])): c for c in cx.get_course_list()}.values())
    if command["action"] == "courses":
        channel.send("courses", courses=courses, demo=False)
        return
    if command['action'] == 'course_progress':
        from .course_progress import refresh_progress
        if command.get('selected'):
            refresh_progress(cx, channel, select_courses(courses, command['selected']))
        return
    if verifying:
        from .verification import verify_chapters
        from .task_completion import TaskCompletionCache
        verify_chapters(cx, channel, courses, command.get('targets', []), records=command.get('verify_records') is True,
                        completion_cache=TaskCompletionCache(command['data_dir'], account_id, uid))
        return
    chosen = select_courses(courses, command.get("selected", []))
    selected_courses = list(chosen)
    from .course_progress import progress_identity, send_progress
    from .task_completion import TaskCompletionCache
    completion_cache = TaskCompletionCache(command["data_dir"], account_id, uid) if account_id else None
    ignore_completion_cache = common.get('ignore_completion_cache', False)
    cached_ids = set()
    if channel.control:
        def check_resumed_session():
            for course in chosen:
                result = cx.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
                if not isinstance(result, dict) or not isinstance(result.get('points'), list):
                    raise ValueError('无法复核课程会话')
        channel.control.resume_check = check_resumed_session
    tasks = []
    tasks_by_course = {}
    for course in chosen:
        checkpoint(channel)
        course_tasks = []
        tasks_by_course[(str(course["courseId"]), str(course["clazzId"]))] = course_tasks
        channel.send('course_progress_loading', **progress_identity(course))
        points = cx.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
        completed = completion_cache.read(course) if completion_cache else {}
        try:
            send_progress(channel, course, points)
        except ValueError:
            channel.send('course_progress_failed', **progress_identity(course))
        for i, point in enumerate(points):
            task = dict(id=f"{course['courseId']}:{course['clazzId']}:{point['id']}:{i}",
                        title=point["title"], course=course["title"], course_data=course, point=point)
            tasks.append(task)
            course_tasks.append(task)
            if not ignore_completion_cache and str(point['id']) in completed:
                cached_ids.add(task['id'])
    chosen = [course for course in chosen
              if any(t['id'] not in cached_ids
                     for t in tasks_by_course[(str(course['courseId']), str(course['clazzId']))])]
    channel.send("tasks", tasks=[dict({k: t[k] for k in ("id", "title", "course")},
                                    courseId=str(t['course_data']['courseId']),
                                    clazzId=str(t['course_data']['clazzId']), pointId=str(t['point']['id'])) for t in tasks])
    for task in tasks:
        if task['id'] in cached_ids:
            channel.send('task', id=task['id'], status='success')

    def remember_result(task, confirmed):
        if completion_cache:
            try:
                if confirmed:
                    completion_cache.save(task['course_data'], task['point'])
                elif ignore_completion_cache:
                    completion_cache.remove(task['course_data'], task['point']['id'])
            except (OSError, ValueError) as exc:
                channel.send('log', level='WARNING', message=f"无法更新任务完成缓存：{exc}")

    def verify_task(task):
        # Accept each chapter immediately after processing; saving answers alone
        # is not proof of completion, even when the runner reports success.
        course = task['course_data']
        channel.send('course_progress_loading', **progress_identity(course))
        try:
            points = cx.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
            try:
                send_progress(channel, course, points)
            except ValueError:
                channel.send('course_progress_failed', **progress_identity(course))
            confirmed = any(str(p['id']) == str(task['point']['id']) and p.get('has_finished') is True
                            for p in points)
            if not confirmed and ignore_completion_cache:
                remember_result(task, False)
        except Exception as exc:
            channel.send('course_progress_failed', **progress_identity(course))
            channel.send("log", message=f"无法核验章节进度：{exc}", level="WARNING")
            confirmed = False
        checkpoint(channel)
        if confirmed:
            remember_result(task, True)
        return 'success' if confirmed else 'pending'

    statuses = run_tasks(channel, [t for t in tasks if t['id'] not in cached_ids],
                         lambda t: core.process_chapter(cx, t["course_data"], t["point"], common["speed"], t['id']),
                         common, core.ChapterResult, verify=verify_task)
    statuses.update({task_id: 'success' for task_id in cached_ids})
    # Retain the final read for media whose platform confirmation arrives late.
    for course in chosen:
        checkpoint(channel)
        channel.send('course_progress_loading', **progress_identity(course))
        try:
            points = cx.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
            try:
                send_progress(channel, course, points)
            except ValueError:
                channel.send('course_progress_failed', **progress_identity(course))
            finished = {str(p["id"]): p.get("has_finished") is True for p in points}
            if ignore_completion_cache:
                for task in tasks_by_course[(str(course['courseId']), str(course['clazzId']))]:
                    if not finished.get(str(task['point']['id']), False):
                        remember_result(task, False)
        except Exception as exc:
            channel.send('course_progress_failed', **progress_identity(course))
            channel.send("log", message=f"无法核验章节进度：{exc}", level="WARNING")
            finished = {}
        for task in tasks_by_course[(str(course["courseId"]), str(course["clazzId"]))]:
            if statuses[task["id"]] in ("processed", "pending"):
                status = "success" if finished.get(str(task["point"]["id"]), False) else "pending"
                if status == 'success':
                    remember_result(task, True)
                statuses[task["id"]] = status
                channel.send("task", id=task["id"], status=status)
    if common["add_learning_count"]:
        channel.send("log", message="开始执行章节学习次数追加。", level="INFO")
        for course in selected_courses:
            core.increase_learning_count_for_course(cx, course, common)
    counts = {s: list(statuses.values()).count(s) for s in ("success", "failed", "skipped", "pending")}
    checkpoint(channel)
    if notification_config.get("provider"):
        notification = core.Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()
        try:
            notification.send(f"超星桌面版任务结束：{counts}")
        except Exception as exc:
            channel.send("log", message=f"外部通知发送失败：{exc}", level="WARNING")
    channel.send("done", **counts, demo=False)


def main() -> int:
    output, input_stream = sys.stdout, sys.stdin
    if output is None or input_stream is None:
        return 1
    for stream in (output, input_stream):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    command = json.loads(input_stream.readline())
    channel = Channel(output, input_stream, Redactor(command.get("config", {})))
    channel.run_id = str(command.get("run_id") or channel.run_id)
    channel.enable_control()
    try:
        path = Path(command["data_dir"]).resolve()
        path.mkdir(parents=True, exist_ok=True)
        os.chdir(path)
        if command.get("action") == "probe":
            load_core(channel)
            from api.cxsecret_font import fonthash_dao
            # Exercise the packaged v2 helper and Qt image codec without any
            # credential or network request (the worker has no QApplication).
            import base64
            from PySide6.QtCore import QBuffer, QIODevice
            from PySide6.QtGui import QImage
            from .bank_question import ImageEncoder, yanxi_environment
            picture = QImage(1, 1, QImage.Format.Format_RGB32)
            picture.fill(0xFFFFFF)
            stream = QBuffer()
            stream.open(QIODevice.OpenModeFlag.WriteOnly)
            if not picture.save(stream, 'PNG'):
                raise RuntimeError('PNG 编码器不可用')
            source = 'data:image/png;base64,' + base64.b64encode(bytes(stream.data())).decode('ascii')
            encoder = ImageEncoder()
            env = yanxi_environment(dict(title='1+1=?', options='a.1 b.2'), encoder)
            if env['options'] != '1\n2' or env['suggestion_title'] or not encoder.encode(source).startswith('data:image/png;base64,'):
                raise RuntimeError('题库 v2 参数或图片编码检查失败')
            from .matching import match_choices
            match = match_choices('需求牵引#\n改革攻坚#', [('A', '需求牵引'), ('B', '改革攻坚')], 'multiple')
            if match is None or {key for key, _ in match.pairs} != {'A', 'B'}:
                raise RuntimeError('相似匹配模块检查失败')
            quantity = match_choices('1万亿元', [('A', '10000亿元'), ('B', '8000亿元')], 'single')
            if quantity is None or quantity.pairs[0][0] != 'A':
                raise RuntimeError('数值单位匹配检查失败')
            from types import SimpleNamespace
            from .answers import AnswerRouter
            from .config import defaults, to_payload
            review_config = to_payload(defaults())
            review_config['matching']['bank_review_policy'] = 'ai_dependent'
            policy_router = AnswerRouter(review_config, SimpleNamespace(send=lambda *args, **kwargs: None))
            review_question = dict(title='2026年产业占比', type='single', options='A.70%\nB.80%')
            review_bank = SimpleNamespace(last_result_question='2025年产业占比')
            if policy_router.normalize_bank_answer('80%', review_question, review_bank) != '80%':
                raise RuntimeError('AI 关闭时的题库审核策略检查失败')
            review_config['ai']['enabled'] = 'true'
            if policy_router.normalize_bank_answer('80%', review_question, review_bank) is not None:
                raise RuntimeError('AI 开启时的题库审核策略检查失败')
            # Exercise the actual frozen OCR model and native ONNX runtime,
            # not just import availability or developer-environment packages.
            import io
            from PIL import Image, ImageDraw, ImageFont
            from api.captcha import ocr_init
            captcha = Image.new('RGB', (160, 54), 'white')
            ImageDraw.Draw(captcha).text((8, 6), '1234', font=ImageFont.load_default(size=32), fill='black')
            captcha_bytes = io.BytesIO()
            captcha.save(captcha_bytes, format='PNG')
            ocr = ocr_init()
            if ocr is None or ocr.classification(captcha_bytes.getvalue()).strip() != '1234':
                raise RuntimeError('验证码识别模型或运行库检查失败')
            from .diagnostics import offline_check
            offline_check()
            from api.discussion import DiscussionAdapter
            from .discussion import DiscussionGenerator
            from .discussion_store import topic_key
            if (DiscussionGenerator(policy_router).generate('离线讨论检查', '', 'fixed_good', ()) != '好'
                    or len(topic_key('probe', 'course', 'class', 'space', 'topic')) != 64
                    or not callable(DiscussionAdapter.publish_once)):
                raise RuntimeError('讨论生成、存储或平台适配模块检查失败')
            from api.activity import ActivityAdapter
            from .activity import ActivityGenerator, preview_context
            from .activity_store import activity_key
            local_answers = ActivityGenerator(policy_router).generate(preview_context('离线问卷检查'),
                dict(choice_strategy='random', text_strategy='fixed_good', reply_level='medium'))
            if (local_answers[-1]['text'] != '好' or not all(a['valid'] for a in local_answers)
                    or len(activity_key('probe', 'course', 'class', 'space', 'activity')) != 64
                    or not callable(ActivityAdapter.submit_once)):
                raise RuntimeError('活动生成、存储或平台适配模块检查失败')
            channel.send("probe", ocs_ready=True, message="核心、字体资源、题库 v2 参数、图片编码及验证码识别正常",
                         font_entries=len(fonthash_dao.char_map), bank_protocol='v2-text-and-images',
                         matching_ready=True, captcha_ready=True, review_policy_ready=True, discussion_ready=True, activity_ready=True)
        elif command.get('action') == 'check_bank':
            load_core(channel)
            from .answers import build_answers
            from .query_result import query_bank, STATE_TEXT
            router = build_answers(command['config'], channel)
            if not router.banks:
                raise ValueError('题库初始化失败，请检查配置')
            name, bank = router.banks[0]
            bank.control = channel.control
            bank.budget_check = lambda: router.consume_budget(('connection-check', name))
            result = query_bank(name, bank, dict(title='1+1=？', type='single', options='A.1\nB.2'))
            channel.send('bank_check', ok=result.status in ('hit', 'miss'),
                         message='真实连接检查：' + STATE_TEXT[result.status] + '；不代表后续命中率或答案正确率。')
        elif command.get("action") == "check_ai":
            from .answers import AIClient
            ai = AIClient(command['config']['ai'])
            ai.attach_channel(channel)
            ai.check_connection()
            channel.send("ai_check")
        elif command.get('action') == 'discussion_preview':
            from .answers import AnswerRouter, AIClient
            from .discussion import DiscussionGenerator
            cfg = command['config']
            ai = AIClient(cfg['ai']) if cfg['ai']['enabled'] == 'true' else None
            router = AnswerRouter(cfg, channel, ai=ai)
            content = DiscussionGenerator(router).generate(str(command.get('topic', '')),
                '', cfg['discussion']['reply_level'], ('preview',))
            channel.send('discussion_preview', content=channel.redactor(content),
                         level=cfg['discussion']['reply_level'])
        elif command.get('action') == 'activity_preview':
            load_core(channel)
            from .answers import AnswerRouter, AIClient
            from .activity import ActivityGenerator, preview_context
            cfg = command['config']
            local = cfg['activity']['choice_strategy'] == 'random' and cfg['activity']['text_strategy'] == 'fixed_good'
            ai = AIClient(cfg['ai']) if not local and cfg['ai']['enabled'] == 'true' else None
            context = preview_context(str(command.get('topic', '')))
            answers = ActivityGenerator(AnswerRouter(cfg, channel, ai=ai)).generate(context, cfg['activity'])
            channel.send('activity_preview', record=dict(id='preview', run_id=channel.run_id, task_id='', course='', chapter='',
                         title=context.title, kind='survey', stage='draft', submit_status='draft', task_status='pending',
                         reason='示例预览，不读取课程、不提交、不写入生产去重记录。', elapsed_ms=0,
                         questions=context.questions, answers=answers))
        elif command.get("demo"):
            demo(channel, command)
        else:
            real(channel, command)
        return 0
    except ResumeFailed:
        channel.send("error", message="恢复前无法核验平台会话，任务已停止；请重新登录后再开始。")
        return 1
    except Cancelled:
        if channel.control and channel.control.resume_failed:
            channel.send("error", message="恢复前无法核验平台会话，任务已停止；请重新登录后再开始。")
            return 1
        channel.send("cancelled", message="任务已停止；已送达平台的请求不会撤销。")
        return 2
    except Exception as exc:
        channel.send("error", message=f"{type(exc).__name__}: {exc}")
        return 1
