import unittest
from chaoxing_desktop.models import TaskModel


class MediaDisplayTests(unittest.TestCase):
    def test_nearly_finished_video_still_shows_playing(self):
        model = TaskModel()
        model.reset([dict(id='chapter', status='running')])
        model.update_media('chapter', dict(name='first', duration=1000, position=999))
        media = model.rows[0]['media']
        self.assertEqual(media['percent'], 99)
        self.assertEqual(media['statusText'], '播放中')

    def test_next_media_has_its_own_number_and_progress(self):
        model = TaskModel()
        model.reset([dict(id='chapter', status='running')])
        model.update_media('chapter', dict(name='first', duration=100, position=100,
                                          verified=True, media_index=1, media_total=2))
        model.update_media('chapter', dict(name='second', duration=200, position=24,
                                          media_index=2, media_total=2))
        media = model.rows[0]['media']
        self.assertEqual((media['mediaIndex'], media['mediaTotal'], media['percent']), (2, 2, 12))
        self.assertFalse(media['verified'])
