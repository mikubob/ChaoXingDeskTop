# -*- coding: utf-8 -*-
"""
超星学习通数据解析模块

该模块负责解析超星学习通平台的课程、章节、任务点等各种数据，
并转换为程序内部使用的结构化数据格式。
"""
import json
import re
from html import escape
from urllib.parse import unquote, urlsplit
from typing import List, Dict, Tuple, Any, Optional

from bs4 import BeautifulSoup, NavigableString

from api.font_decoder import FontDecoder
from api.logger import logger


def decode_course_list(html_text: str) -> List[Dict[str, str]]:
    """
    解析课程列表页面，提取课程信息
    
    Args:
        html_text: 课程列表页面的HTML内容
        
    Returns:
        课程信息列表，每个课程包含id、title、teacher等信息
    """
    logger.trace("开始解码课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("div.course")
    course_list = []

    for course in raw_courses:
        # 跳过未开放课程
        if course.select_one("a.not-open-tip") or course.select_one("div.not-open-tip"):
            continue

        course_detail = {
            "id": course.attrs["id"],
            "info": course.attrs["info"],
            "roleid": course.attrs["roleid"],
            "clazzId": course.select_one("input.clazzId").attrs["value"],
            "courseId": course.select_one("input.courseId").attrs["value"],
            "cpi": re.findall(r"cpi=(.*?)&", course.select_one("a").attrs["href"])[0],
            "title": course.select_one("span.course-name").attrs["title"],
            "desc": course.select_one("p.margint10").attrs["title"] if course.select_one("p.margint10") else "",
            "teacher": course.select_one("p.color3").attrs["title"]
        }
        course_list.append(course_detail)

    return course_list


def decode_course_folder(html_text: str) -> List[Dict[str, str]]:
    """
    解析二级课程列表页面，提取文件夹信息
    
    Args:
        html_text: 二级课程列表页面的HTML内容
        
    Returns:
        课程文件夹信息列表
    """
    logger.trace("开始解码二级课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("ul.file-list>li")
    course_folder_list = []

    for course in raw_courses:
        if not course.attrs.get("fileid"):
            continue

        course_folder_detail = {
            "id": course.attrs["fileid"],
            "rename": course.select_one("input.rename-input").attrs["value"]
        }
        course_folder_list.append(course_folder_detail)

    return course_folder_list


def decode_course_point(html_text: str) -> Dict[str, Any]:
    """
    解析章节列表页面，提取章节点信息
    
    Args:
        html_text: 章节列表页面的HTML内容
        
    Returns:
        章节信息字典，包含是否锁定状态和章节点列表
    """
    logger.trace("开始解码章节列表...")
    soup = BeautifulSoup(html_text, "lxml")
    course_point = {
        "hasLocked": False,  # 用于判断该课程任务是否是需要解锁
        "points": [],
    }

    for chapter_unit in soup.find_all("div", class_="chapter_unit"):
        points = _extract_points_from_chapter(chapter_unit)
        # 检查是否有锁定内容
        for point in points:
            if point.get("need_unlock", False):
                course_point["hasLocked"] = True

        course_point["points"].extend(points)

    return course_point


def _extract_points_from_chapter(chapter_unit) -> List[Dict[str, Any]]:
    """
    从章节单元中提取章节点信息
    
    Args:
        chapter_unit: BeautifulSoup对象，表示一个章节单元
        
    Returns:
        章节点信息列表
    """
    point_list = []
    raw_points = chapter_unit.find_all("li")

    for raw_point in raw_points:
        point = raw_point.div
        if "id" not in point.attrs:
            continue

        point_id = re.findall(r"^cur(\d{1,20})$", point.attrs["id"])[0]
        point_title = point.select_one("a.clicktitle").text.replace("\n", "").strip()

        count_field = point.select_one("input.knowledgeJobCount")
        job_count = count_field["value"] if count_field else 1
        hints = point.select("span.bntHoverTips")
        need_unlock = any("解锁" in hint.text for hint in hints)
        is_finished = any("已完成" in hint.text for hint in hints)

        point_detail = {
            "id": point_id,
            "title": point_title,
            "jobCount": job_count,
            "has_finished": is_finished,
            "need_unlock": need_unlock
        }
        point_list.append(point_detail)

    return point_list


def decode_course_card(html_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    解析任务点列表页面，提取任务点信息
    
    Args:
        html_text: 任务点列表页面的HTML内容
        
    Returns:
        任务点列表和任务信息的元组
    """
    logger.trace("开始解码任务点列表...")

    # 检查章节是否未开放
    if "章节未开放" in html_text:
        return [], {"notOpen": True}

    # 提取mArg参数
    assignment = re.search(r"\bmArg\s*=\s*(?=\{)", html_text)
    if not assignment:
        return _discussion_cards(html_text, []) + _activity_cards(html_text, []), {}

    # 解析JSON数据
    cards_data, _ = json.JSONDecoder().raw_decode(html_text[assignment.end():])

    if not cards_data:
        return _discussion_cards(html_text, []) + _activity_cards(html_text, []), {}

    # 提取任务信息
    job_info = _extract_job_info(cards_data)

    # 处理所有附件任务
    cards = cards_data.get("attachments", [])
    job_list = _process_attachment_cards(cards)
    # Discussion modules may have no job field, or an unknown attachment type.
    # Inspect the same cards HTML before allowing the empty-chapter path.
    discussions = _discussion_cards(html_text, cards)
    existing = {str(j.get('mid', '')) for j in discussions}
    job_list = [j for j in job_list if not (j['type'] == 'discussion' and str(j.get('mid', '')) in existing)]
    job_list.extend(discussions)
    activities = _activity_cards(html_text, cards)
    activity_mids = {j.get('mid') for j in activities if j.get('mid')}
    job_list = [j for j in job_list if not (j['type'] == 'activity' and j.get('mid') in activity_mids)]
    job_list.extend(activities)

    return job_list, job_info


def _activity_job(card, *, linked=True):
    prop = card.get('property') or {}
    required = card.get('job') if type(card.get('job')) is bool else None
    module = str(prop.get('module') or card.get('module') or '')
    return dict(type='activity', required=required, isPassed=card.get('isPassed') is True,
                server_finished=bool(linked and card.get('jobid') and not card.get('job')),
                page_variant='insertvote-v2' if module == 'insertvote' else 'unsupported',
                mid=str(prop.get('mid') or card.get('mid') or ''),
                jobid=str(card.get('jobid') or prop.get('jobid') or ''),
                name=prop.get('title') or '章节投票／问卷', property=dict(prop),
                enc=card.get('enc', ''), jtoken=card.get('jtoken', ''), microTopicId=card.get('microTopicId', ''))


def _activity_cards(html_text, cards):
    jobs = []
    for frame in BeautifulSoup(html_text, 'lxml').select('iframe'):
        source = urlsplit(frame.get('src', ''))
        module = frame.get('module', '')
        known_path = source.path in ('/ananas/modules/zt/vote/index-pc-new.html',
                                     '/ananas/modules/zt/vote/index-pc.html',
                                     '/ananas/modules/questionnaire/index.html')
        if module not in ('insertvote', 'questionnaire') and not known_path:
            continue
        trusted = (not source.netloc or source.hostname == 'mooc1.chaoxing.com') and source.scheme in ('', 'https')
        try:
            data = json.loads(frame.get('data', '{}'))
            if not isinstance(data, dict) or not trusted:
                raise ValueError
        except (ValueError, TypeError):
            jobs.append(_activity_job({}, linked=False))
            continue
        mid = str(data.get('mid') or '')
        matches = [c for c in cards if mid and str((c.get('property') or {}).get('mid') or c.get('mid') or '') == mid]
        if len(matches) == 1:
            card = dict(matches[0])
            # Clone iframe IDs and native attachment IDs can differ. Keep the
            # server attachment authoritative and retain source evidence.
            card['property'] = dict(data, **(card.get('property') or {}))
        else:
            card = dict(property=dict(data, module=module or 'questionnaire'))
        job = _activity_job(card, linked=len(matches) == 1)
        job['entry_property'] = data
        jobs.append(job)
    return jobs


def _discussion_job(card):
    prop = card.get('property') or {}
    required = card.get('job') if type(card.get('job')) is bool else None
    return dict(type='discussion', required=required, isPassed=card.get('isPassed') is True,
                mid=str(prop.get('mid') or card.get('mid') or ''),
                jobid=str(card.get('jobid') or prop.get('jobid') or ''),
                name=prop.get('title') or '章节讨论', property=dict(prop), enc=card.get('enc', ''))


def _discussion_cards(html_text, cards):
    jobs = []
    for frame in BeautifulSoup(html_text, 'lxml').select('iframe[src]'):
        if urlsplit(frame.get('src', '')).path != '/ananas/modules/insertbbs/index.html':
            continue
        try:
            data = json.loads(frame.get('data', '{}'))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, TypeError):
            jobs.append(dict(type='discussion', required=None, mid='', jobid='', name='讨论模块数据无法识别'))
            continue
        mid = str(data.get('mid') or '')
        matches = [c for c in cards if mid and str((c.get('property') or {}).get('mid') or c.get('mid') or '') == mid]
        if len(matches) == 1:
            card = dict(matches[0])
            card['property'] = dict(data, **(card.get('property') or {}))
        else:
            card = dict(property=data)  # No qualification by array index/title.
        jobs.append(_discussion_job(card))
    return jobs


def _extract_job_info(cards_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从卡片数据中提取任务基本信息
    
    Args:
        cards_data: 卡片数据字典
        
    Returns:
        任务基本信息字典
    """
    defaults = cards_data.get("defaults", {})
    if not defaults:
        return {}

    return {
        "ktoken": defaults.get("ktoken", ""),
        "mtEnc": defaults.get("mtEnc", ""),
        "reportTimeInterval": defaults.get("reportTimeInterval", 60),
        "defenc": defaults.get("defenc", ""),
        "cardid": defaults.get("cardid", ""),
        "cpi": defaults.get("cpi", ""),
        "qnenc": defaults.get("qnenc", ""),
        "knowledgeid": defaults.get("knowledgeid", "")
    }


def _process_attachment_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    处理所有附件任务卡片，强化直播任务识别逻辑
    
    Args:
        cards: 附件任务卡片列表
        
    Returns:
        处理后的任务列表
    """
    job_list = []

    for card in cards:
        if attachment_kind(card) == 'activity':
            job_list.append(_activity_job(card))
            continue
        if attachment_kind(card) == 'discussion':
            job_list.append(_discussion_job(card))
            continue
        # 跳过已通过的任务
        if card.get("isPassed", False):
            continue

        # 处理无job字段的特殊任务
        if card.get("job") is None:
            # 尝试识别阅读任务
            read_job = _process_read_task(card)
            if read_job:
                job_list.append(read_job)
            continue

        # 一开始就把超星api的屎山处理掉，不要用一个屎山行为掩盖另一个屎山 (指根据otherInfo中是否有courseId决定url拼接方式😂)
        # 清理otherInfo字段中的无效参数，这里优化了一下(保留了作者原来的注释TAT）
        if "otherInfo" in card:
            logger.trace("Fixing other info...")
            card["otherInfo"] = card["otherInfo"].split("&")[0]
            logger.trace(f"New info: {card['otherInfo']}")

        # 多维度判断是否为直播任务
        card_type = attachment_kind(card)
        property_data = card.get("property", {})
        prop_type = property_data.get("type", "").lower()
        resource_type = property_data.get("resourceType", "").lower()

        # 直播任务特征：包含liveId、streamName等字段，
        # 或类型标识包含live（因为live和video有点类似，怕超星又搞出什么幺蛾子就加了一些关键字识别）
        is_live = (
                "live" in card_type
                or "live" in prop_type
                or "live" in resource_type
                or "livestream" in card_type
                or property_data.get("liveId") is not None
                or property_data.get("streamName") is not None
                or property_data.get("vdoid") is not None
        )

        # 根据任务类型处理
        if is_live:
            live_job = _process_live_task(card)
            if live_job:
                job_list.append(live_job)
        elif card_type in ("video", "audio"):
            video_job = _process_video_task(card)
            if video_job:
                video_job['type'] = card_type
                job_list.append(video_job)
        elif card_type == "document":
            doc_job = _process_document_task(card)
            if doc_job:
                job_list.append(doc_job)
        elif card_type == 'read':
            read_job = _process_read_task(card)
            if read_job:
                job_list.append(read_job)
        elif card_type == "workid":
            work_job = _process_work_task(card)
            if work_job:
                job_list.append(work_job)
        else:
            logger.warning(f"Unknown card type: {card_type}")
            # Retain unsupported task points so they cannot become an empty page.
            job_list.append({'type': 'unsupported', 'jobid': card.get('jobid', ''),
                             'name': property_data.get('name') or property_data.get('title') or card_type})

    return job_list


def attachment_kind(card: Dict[str, Any]) -> str:
    """Resolve player module first; legacy cards can call MP3 resources video."""
    prop = card.get('property') or {}
    modules = {'insertaudio': 'audio', 'insertvideo': 'video', 'insertdoc': 'document', 'insertbbs': 'discussion',
               'insertvote': 'activity', 'questionnaire': 'activity'}
    module = str(prop.get('module') or card.get('module') or '').lower()
    if module in modules:
        return modules[module]
    kinds = [str(value or '').lower() for value in
             (card.get('type'), prop.get('type'), prop.get('resourceType'))]
    if 'audio' in kinds:
        return 'audio'
    kind = kinds[0] or next((value for value in kinds[1:]
                             if value in ('video', 'document', 'read', 'workid')), '')
    if kind in ('video', ''):
        name = prop.get('name') or card.get('name') or ''
        path = unquote(urlsplit(str(name)).path).lower()
        if path.endswith(('.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.oga',
                          '.opus', '.wma', '.amr', '.aiff')):
            return 'audio'
    return kind


def _process_live_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理直播类型任务，提取所有必要参数"""
    try:
        property_data = card.get("property", {})
        return {
            "type": "live",
            "jobid": card.get("jobid", str(card.get("id", ""))),  # 兼容不同格式的任务ID
            "name": property_data.get("title", property_data.get("name", "未知直播")),
            "otherinfo": card.get("otherInfo", ""),
            "property": property_data,  # 保留完整属性用于后续处理
            "mid": card.get("mid", ""),
            "objectid": card.get("objectId", ""),
            "aid": card.get("aid", ""),
            # 补充直播特有标识
            "liveId": property_data.get("liveId"),
            "streamName": property_data.get("streamName")
        }
    except Exception as e:
        logger.error(f"解析直播任务失败: {str(e)}, 任务数据: {str(card)[:200]}")
        return None


def _process_read_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理阅读类型任务"""
    if not (card.get("type") == "read" and not card.get("property", {}).get("read", False)):
        return None

    return {
        "title": card.get("property", {}).get("title", ""),
        "type": "read",
        "id": card.get("property", {}).get("id", ""),
        "jobid": card.get("jobid", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def _process_video_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """Extract media metadata; missing mid alone does not imply transcode failure."""
    prop = card.get('property', {})
    return {
        "type": attachment_kind(card),
        "module": prop.get('module') or card.get('module', ''),
        "jobid": card.get("jobid") or prop.get('_jobid') or prop.get('jobid', ''),
        "name": prop.get("name") or prop.get('title', ''),
        "otherinfo": card.get("otherInfo", ""),
        "mid": card.get("mid", ""),
        "objectid": card.get("objectId") or prop.get('objectid') or prop.get('objectId', ''),
        "aid": card.get("aid", ""),
        "playTime": card.get("playTime", 0),
        "rt": prop.get("rt", ""),
        "attDuration": card.get("attDuration", ""),
        "attDurationEnc": card.get("attDurationEnc", ""),
        "videoFaceCaptureEnc": card.get("videoFaceCaptureEnc", ""),
    }


def _process_document_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理文档类型任务"""
    prop = card.get('property', {})
    return {
        "type": "document",
        "jobid": card.get("jobid") or prop.get('_jobid') or prop.get('jobid', ''),
        "name": prop.get('name') or prop.get('title', ''),
        "otherinfo": card.get("otherInfo", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", ""),
        "objectid": card.get("property", {}).get("objectid", ""),
        "knowledgeid": card.get("knowledgeid", ""),
    }


def _process_work_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理作业类型任务"""
    return {
        "type": "workid",
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def decode_questions_info(html_content: str) -> Dict[str, Any]:
    """
    解析题目信息，提取表单数据和问题列表
    
    Args:
        html_content: 题目页面HTML内容
        
    Returns:
        包含表单数据和问题列表的字典
    """
    soup = BeautifulSoup(html_content, "lxml")
    form_data = _extract_form_data(soup)

    # 检查是否存在字体加密
    has_font_encryption = bool(soup.find("style", id="cxSecretStyle"))
    font_decoder = None

    if has_font_encryption:
        font_decoder = FontDecoder(html_content)
    else:
        logger.warning("未找到字体文件，可能是未加密的题目不进行解密")

    # 处理所有问题
    questions = []
    for div_tag in soup.find("form").find_all("div", class_="singleQuesId"):
        question = _process_question(div_tag, font_decoder)
        if question:
            questions.append(question)

    # 更新表单数据
    form_data["questions"] = questions
    form_data["answerwqbid"] = ",".join([q["id"] for q in questions]) + ","

    return form_data


def _extract_form_data(soup: BeautifulSoup) -> Dict[str, Any]:
    """从BeautifulSoup对象中提取表单数据"""
    form_data = {}
    form_tag = soup.find("form")

    if not form_tag:
        return form_data

    # 提取所有非答案字段的input
    for input_tag in form_tag.find_all("input"):
        name_attr = input_tag.attrs.get("name")
        if name_attr is None:
            continue

        if isinstance(name_attr, list):
            name_str = str(name_attr[0]) if name_attr else ""
        else:
            name_str = str(name_attr)

        if not name_str or "answer" in name_str:
            continue

        val_attr = input_tag.attrs.get("value", "")
        if isinstance(val_attr, list):
            val_str = "".join(str(v) for v in val_attr)
        else:
            val_str = str(val_attr)

        form_data[name_str] = val_str

    return form_data


def _process_question(div_tag, font_decoder=None) -> Dict[str, Any]:
    """处理单个问题"""
    # 提取问题ID和题目类型
    question_id = div_tag.attrs.get("data", "")
    q_type_code = div_tag.find("div", class_="TiMu").attrs.get("data", "")
    q_type = _get_question_type(q_type_code)

    # 提取题目内容和选项
    title_div = div_tag.find("div", class_="Zy_TItle")
    options_list = div_tag.find("ul").find_all("li") if div_tag.find("ul") else []

    # 解析题目和选项
    q_title = _extract_title(title_div, font_decoder)
    # Extracting all DOM descendants can concatenate the separate number and
    # type spans with a year in the body. Remove only an explicit type header;
    # never guess by trimming digits from a year or mathematical expression.
    q_title = re.sub(
        r'^\s*(?:\d+\s*[.、。]?\s*)?[\[(【（]\s*'
        r'(?:单选题|多选题|判断题|填空题|简答题|名词解释|完形填空|阅读理解)'
        r'(?:\s*[,，]\s*\d+(?:\.\d+)?\s*分)?\s*[\])】）]\s*',
        '', q_title,
    )
    q_options = []
    for li in options_list:
        q_options.append(_extract_choices(li, font_decoder))
    # 排序选项
    q_options.sort()
    q_options = '\n'.join(q_options)

    completion_fields = []
    layout = dict(question_id=question_id, protocol='unsupported', supported=False,
                  blanks=[], reason='未发现带真实 name 的填空字段；仅 id／编辑器容器不能证明提交映射')
    if q_type == 'completion' and question_id:
        unsupported = False
        for field in div_tag.find_all(['textarea', 'input']):
            name = field.get('name', '')
            if (not field.has_attr('disabled') and (field.name == 'textarea' or field.get('type', 'text').lower() == 'text')
                    and re.fullmatch(r'answer' + re.escape(question_id) + r'(?:[_-]?\d+)?', name)):
                if name in completion_fields:
                    layout['reason'] = '同名多空字段需要重复键协议证据，当前不支持'
                    unsupported = True
                    break
                completion_fields.append(name)
                layout['blanks'].append(dict(order=len(completion_fields), editor_id=field.get('id', ''),
                                              field=name, existing=field.get_text() if field.name == 'textarea' else field.get('value', '')))
        # An unmapped editor next to mapped fields can represent additional
        # blanks or an aggregate protocol. Do not silently count only a subset.
        editors = div_tag.select('[contenteditable="true"], script[type="text/plain"], iframe')
        unknown_fields = [f for f in div_tag.find_all(['textarea', 'input'])
                          if not f.has_attr('disabled') and (f.name == 'textarea' or f.get('type', 'text').lower() == 'text')
                          and f.get('name', '') not in completion_fields]
        hidden = [f for f in div_tag.select('input[type="hidden"][name]')
                  if re.fullmatch(r'answer' + re.escape(question_id) + r'(?:[_-]?\d+)?', f['name'])]
        if editors or unknown_fields or hidden:
            unsupported = True
            layout['reason'] = '编辑器／隐藏答案／未映射输入的提交关联未知，未将部分字段当作完整空位'
        if completion_fields and not unsupported and len(completion_fields) <= 100:
            layout.update(protocol='named_inputs', supported=True, reason='')
        else:
            completion_fields = []
    return {
        'completion_layout': layout if q_type == 'completion' else None,
        'blank_count': len(completion_fields) or None,
        'completion_fields': completion_fields,
        "id": question_id,
        "title": q_title,
        "options": q_options,
        "type": q_type,
        "answerField": {
            f"answer{question_id}": "",
            f"answertype{question_id}": q_type_code,
        },
    }


def _get_question_type(type_code: str) -> str:
    """根据题型代码返回题型名称"""
    type_map = {
        "0": "single",  # 单选题
        "1": "multiple",  # 多选题
        "2": "completion",  # 填空题
        "3": "judgement",  # 判断题
        "4": "shortanswer",  # 简答题
    }

    if type_code in type_map:
        return type_map[type_code]

    logger.info(f"未知题型代码 -> {type_code}")
    return "unknown"


def _extract_title(element, font_decoder=None) -> str:
    """提取标题内容，支持解码加密字体"""
    if not element:
        return ""

    # 收集元素中的所有文本和图片
    content = []
    for item in element.descendants:
        if isinstance(item, NavigableString):
            content.append(item.string or "")
        elif item.name == "img":
            img_url = item.get("src", "")
            content.append(f'<img src="{escape(img_url, quote=True)}">')

    raw_content = "".join(content)
    cleaned_content = raw_content.replace("\r", "").replace("\t", "").replace("\n", "")

    # 如果有字体解码器，进行解码
    if font_decoder:
        return font_decoder.decode(cleaned_content)

    return cleaned_content


def _extract_choices(element, font_decoder=None) -> str:
    """提取选项内容，支持解码加密字体"""
    if not element:
        return ""

    # 纯文字沿用 aria-label；含图时保留 DOM 中的图文顺序。
    choice = element.get("aria-label")
    if element.find("img"):
        visible = _extract_title(element).strip()
        label = re.match(r"^\s*([A-Za-z])(?:[.、．:：\s]|$)", choice or "")
        if label and not re.match(rf"^{label[1]}(?:[.、．:：\s<]|$)", visible, re.I):
            visible = f"{label[1]}. " + visible
        choice = visible
    elif not choice:
        choice = _extract_title(element)
    if not choice:
        return ""

    cleaned_content = re.sub(r"[\r\t\n]", "", choice)

    if font_decoder:
        cleaned_content = font_decoder.decode(cleaned_content)

    cleaned_content = cleaned_content.strip()
    if cleaned_content.endswith("选择"):
        cleaned_content = cleaned_content[:-2].rstrip()

    return cleaned_content
