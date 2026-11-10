"""CPU captcha inference using ddddocr's MIT-licensed model and charset.

Only the classification path is supported: Pillow preprocessing, ONNX inference
and greedy CTC decoding. No ddddocr package import or OpenCV is needed.
"""
import io
import json
from importlib.metadata import distribution
from pathlib import Path
import sys

import numpy as np
import onnxruntime as ort
from PIL import Image


class CaptchaOCR:
    def __init__(self):
        resource = (Path(sys._MEIPASS) / 'vendor/chaoxing/resource'
                    if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1] / 'resource')
        self.charset = json.loads((resource / 'ocr_charset.json').read_text(encoding='utf-8'))
        # The wheel supplies the original model and its license; locating its
        # metadata does not execute ddddocr/__init__.py or import cv2.
        model = distribution('ddddocr').locate_file('ddddocr/common_old.onnx')
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(model), sess_options=options,
                                           providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name

    def classification(self, data):
        if not isinstance(data, bytes) or not data or len(data) > 2 * 1024 * 1024:
            raise ValueError('验证码图片为空或过大')
        with Image.open(io.BytesIO(data)) as image:
            if image.width <= 0 or image.height <= 0 or max(image.size) > 4096:
                raise ValueError('验证码图片尺寸无效')
            width = max(1, int(image.width * (64 / image.height)))
            if width > 4096:
                raise ValueError('验证码图片比例无效')
            # Preserve the existing model's preprocessing, including resize
            # before grayscale conversion and normalization to [0, 1].
            image = image.resize((width, 64), Image.Resampling.LANCZOS).convert('L')
            tensor = np.asarray(image, dtype=np.float32)[None, None, :, :] / 255.0
        output = self.session.run(None, {self.input_name: tensor})[0]
        if output.ndim != 3 or output.shape[1] != 1 or output.shape[2] != len(self.charset):
            raise RuntimeError('验证码模型输出格式不匹配')
        return self.decode(np.argmax(output[:, 0, :], axis=1))

    def decode(self, indices):
        result, previous = [], None
        for value in indices:
            index = int(value)
            if not 0 <= index < len(self.charset):
                raise ValueError('验证码字符索引无效')
            if index != previous and index != 0:
                result.append(self.charset[index])
            previous = index
        return ''.join(result)
