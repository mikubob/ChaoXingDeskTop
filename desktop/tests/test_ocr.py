"""Real captcha inference must work without importing OpenCV or ddddocr."""
import io
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor/chaoxing'))
from api.ocr import CaptchaOCR
from PIL import Image, ImageDraw, ImageFont


class OCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ocr = CaptchaOCR()

    def test_ctc_repeated_characters_separated_by_blank_survive(self):
        one = self.ocr.charset.index('1')
        two = self.ocr.charset.index('2')
        self.assertEqual(self.ocr.decode([0, one, one, 0, one, two, two, 0]), '112')

    def test_invalid_input_is_rejected_before_inference(self):
        for value in (b'', b'x' * (2 * 1024 * 1024 + 1), 'not bytes'):
            with self.assertRaises(ValueError):
                self.ocr.classification(value)

    def test_actual_inference_does_not_import_opencv_or_ddddocr(self):
        code = '''
import sys, importlib.abc, io
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('cv2', 'ddddocr'):
            raise AssertionError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, Block())
from api.ocr import CaptchaOCR
from PIL import Image, ImageDraw, ImageFont
image = Image.new('RGB', (160, 54), 'white')
ImageDraw.Draw(image).text((8, 6), '1234', font=ImageFont.load_default(size=32), fill='black')
data = io.BytesIO()
image.save(data, format='PNG')
assert CaptchaOCR().classification(data.getvalue()) == '1234'
assert not any(n.split('.')[0] in ('cv2', 'ddddocr') for n in sys.modules)
'''
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c', "import sys; sys.path.insert(0, 'vendor/chaoxing');\n" + code],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_matches_previous_engine_on_varied_images(self):
        # Reference implementation is used only in this source-environment test.
        from ddddocr import DdddOcr
        reference = DdddOcr(show_ad=False, use_gpu=False)
        for text, mode, fmt, size in [('1234', 'RGB', 'PNG', 32), ('1122', 'L', 'PNG', 28),
                                      ('Ab12', 'RGB', 'JPEG', 30), ('9087', 'RGBA', 'PNG', 36)]:
            image = Image.new(mode, (180, 60), 'white')
            ImageDraw.Draw(image).text((8, 6), text, font=ImageFont.load_default(size=size), fill='black')
            data = io.BytesIO()
            image.save(data, format=fmt)
            with self.subTest(text=text, mode=mode, fmt=fmt):
                self.assertEqual(self.ocr.classification(data.getvalue()), reference.classification(data.getvalue()))
