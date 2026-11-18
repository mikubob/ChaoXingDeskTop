from enum import Enum
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaoxing_desktop.worker import run_tasks


class Result(Enum):
    SUCCESS = 0
    ERROR = 1
    NOT_OPEN = 2


class OrderedChaptersTest(unittest.TestCase):
    def test_next_chapter_starts_after_prerequisite_without_waiting_for_other_course(self):
        unrelated_started, next_started = Event(), Event()
        confirmed = set()
        tasks = [dict(id='first', serial_course=('course', 'class')),
                 dict(id='next', serial_course=('course', 'class')),
                 dict(id='unrelated')]

        def process(task):
            key = task['id']
            if key == 'first' and not unrelated_started.wait(2):
                return Result.ERROR
            if key == 'next':
                if 'first' not in confirmed:
                    return Result.NOT_OPEN
                next_started.set()
            if key == 'unrelated':
                unrelated_started.set()
                if not next_started.wait(2):
                    return Result.ERROR
            return Result.SUCCESS

        def verify(task):
            confirmed.add(task['id'])
            return 'success'

        config = dict(jobs=2, notopen_action='retry', retry_interval=1)
        channel = SimpleNamespace(send=lambda *_args, **_values: None)
        with patch('chaoxing_desktop.worker.controlled_wait') as retry_wait:
            statuses = run_tasks(channel, tasks, process, config, Result, verify)
        self.assertEqual(statuses, dict(first='success', next='success', unrelated='success'))
        retry_wait.assert_not_called()

    def test_unopened_chapter_respects_retry_limit(self):
        channel = SimpleNamespace(send=lambda *_args, **_values: None)
        config = dict(jobs=2, notopen_action='retry', retry_interval=1)
        with patch('chaoxing_desktop.worker.controlled_wait') as retry_wait:
            statuses = run_tasks(channel, [dict(id='locked', serial_course=('course', 'class'))],
                                 lambda _: Result.NOT_OPEN, config, Result)
        self.assertEqual(statuses, dict(locked='skipped'))
        self.assertEqual(retry_wait.call_count, 4)
