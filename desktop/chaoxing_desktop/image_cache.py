"""按工作台数据目录和任务轮次管理本地题图缓存。"""
from contextlib import suppress
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile


def image_cache_root():
    return Path(tempfile.gettempdir()).resolve() / 'chaoxing_desktop_images'


def profile_image_cache(profile):
    identity = os.path.normcase(str(Path(profile).resolve()))
    return image_cache_root() / hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]


PAPER_IMAGE_ACTIONS = ('exam_open', 'homework_load')


def paper_image_cache(profile):
    return profile_image_cache(profile) / 'papers'


def remove_image_cache(directory):
    if directory is None:
        return
    root = image_cache_root()
    with suppress(OSError):
        if directory != root and directory.is_relative_to(root) and directory.resolve() == directory:
            shutil.rmtree(directory)


def clear_stale_image_cache(profile):
    remove_image_cache(profile_image_cache(profile))
    root = image_cache_root()
    # 旧版直接在公共临时目录中写入图片文件。
    with suppress(OSError):
        if root.resolve() == root:
            for path in root.iterdir():
                if re.fullmatch(r'[0-9a-f]{24}\.(png|jpg|gif|webp|bmp)', path.name):
                    with suppress(OSError):
                        path.unlink()
