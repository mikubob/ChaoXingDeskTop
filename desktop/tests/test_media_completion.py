"""Real media loop, simulated clock: ended events and delayed acknowledgments."""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[1] / 'vendor/chaoxing')]
from api.base import Chaoxing, SessionManager, StudyResult
from chaoxing_desktop.control import Cancelled


class MediaClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.now > 2000:
            raise AssertionError('Media loop exceeded its confirmation budget')


class MediaCompletionTests(unittest.TestCase):
    def study(self, duration=152, resume=0, interval=60, speed=1, kind='Video',
              reply=None, request_seconds=0, wait=None, recover=None):
        clock = MediaClock()
        client = Chaoxing()
        client.desktop_media_progress = Mock()
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.text = '{}'
        session.get.return_value.url = 'https://mooc1.chaoxing.com/ananas/status/o'
        session.get.return_value.json.return_value = dict(status='success', dtoken='t', duration=duration)
        reports = []

        def progress(*args, **kwargs):
            report = dict(time=clock.now, position=args[6], event=kwargs['_isdrag'], kind=args[7])
            reports.append(report)
            result = reply(report, reports) if reply else (report['event'] == 4, 200)
            clock.sleep(request_seconds)
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(SessionManager, 'get_session', return_value=session))
            stack.enter_context(patch.object(client, 'get_fid', return_value='f'))
            stack.enter_context(patch.object(client, 'video_progress_log', side_effect=progress))
            stack.enter_context(patch('api.base.time.monotonic', side_effect=clock.monotonic))
            stack.enter_context(patch('api.base.time.sleep', side_effect=clock.sleep))
            stack.enter_context(patch('api.base.controlled_wait',
                                      side_effect=lambda owner, seconds, **kw: wait() if wait else clock.sleep(seconds)))
            if recover:
                stack.enter_context(patch.object(client, '_recover_after_forbidden', side_effect=recover))
            result = client.study_video({}, dict(objectid='o', playTime=resume, name='media'),
                                        dict(reportTimeInterval=interval), _speed=speed, _type=kind)
        return result, reports, client, clock

    def test_natural_end_sends_ended_event_for_video_and_audio(self):
        for kind in ('Video', 'Audio'):
            with self.subTest(kind=kind):
                result, reports, client, _ = self.study(kind=kind)
                self.assertEqual(result, StudyResult.SUCCESS)
                self.assertEqual([(r['time'], r['position'], r['event']) for r in reports],
                                 [(0, 0, 3), (60, 60, 3), (120, 120, 3), (152, 152, 4)])
                self.assertTrue(all(r['kind'] == kind for r in reports))
                self.assertTrue(client.desktop_media_progress.call_args.kwargs['verified'])

    def test_delayed_platform_confirmation_gets_full_intervals(self):
        def delayed(report, reports):
            ended = [r for r in reports if r['event'] == 4]
            return bool(ended and report['time'] >= ended[0]['time'] + 120), 200
        result, reports, _, _ = self.study(reply=delayed)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual([r['time'] for r in reports if r['event'] == 4], [152, 212, 272])

    def test_full_resume_has_bounded_spaced_confirmations_even_at_high_speed(self):
        result, reports, client, clock = self.study(resume=152000, speed=16,
                                                   reply=lambda *args: (False, 200))
        self.assertEqual(result, StudyResult.TIMEOUT)
        self.assertEqual([r['time'] for r in reports], [0, 60, 120, 180, 240, 300])
        self.assertTrue(all(r['position'] == 152 and r['event'] == 4 for r in reports))
        self.assertEqual(clock.now, 300)
        self.assertFalse(any(c.kwargs['verified'] for c in client.desktop_media_progress.call_args_list))

    def test_card_interval_uses_real_time_not_scaled_playback_time(self):
        result, reports, _, _ = self.study(duration=400, interval='20', speed=4)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual([r['time'] for r in reports], [0, 20, 40, 60, 80, 100])
        self.assertEqual([r['position'] for r in reports], [0, 80, 160, 240, 320, 400])

    def test_end_is_reported_without_waiting_for_next_heartbeat(self):
        result, reports, _, _ = self.study(duration=10, interval=60, speed=4)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual([(r['time'], r['event']) for r in reports], [(0, 3), (3, 4)])

    def test_invalid_interval_falls_back_to_sixty_seconds(self):
        for interval in (None, '', 'bad', 0, -1, 'NaN', 'inf', 301):
            with self.subTest(interval=interval):
                result, reports, _, _ = self.study(duration=1, resume=1000, interval=interval,
                                                  reply=lambda *args: (False, 200))
                self.assertEqual(result, StudyResult.TIMEOUT)
                self.assertEqual([r['time'] for r in reports], [0, 60, 120, 180, 240, 300])

    def test_request_time_is_counted_without_advancing_first_recorded_position(self):
        result, reports, _, _ = self.study(duration=10, resume=2000, request_seconds=4)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual([(r['time'], r['position']) for r in reports], [(0, 2), (8, 10)])

    def test_initial_invalid_response_or_verification_stops_immediately(self):
        for status, expected in ((502, StudyResult.ERROR), (428, StudyResult.VERIFICATION_REQUIRED)):
            with self.subTest(status=status):
                result, reports, _, clock = self.study(reply=lambda *args: (False, status))
                self.assertEqual(result, expected)
                self.assertEqual(len(reports), 1)
                self.assertEqual(clock.now, 0)

    def test_initial_forbidden_uses_bounded_recovery(self):
        recover = Mock(return_value=dict(dtoken='new', duration=152))
        result, reports, _, _ = self.study(
            reply=lambda report, reports: (len(reports) > 1, 403 if len(reports) == 1 else 200),
            recover=recover)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(len(reports), 2)
        recover.assert_called_once()

    def test_confirmation_wait_remains_cancellable(self):
        with self.assertRaises(Cancelled):
            self.study(resume=152000, reply=lambda *args: (False, 200), wait=Mock(side_effect=Cancelled))


if __name__ == '__main__':
    unittest.main()
