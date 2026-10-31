"""Declarative field metadata shared by the QML settings pages."""
from .config import BANK_REVIEW_POLICIES, DISCUSSION_LEVELS, ACTIVITY_CHOICES, ACTIVITY_TEXTS


def field(title, section, key, kind="text", low=0, high=100, choices=(), hint="", depends=None):
    return dict(title=title, section=section, key=key, kind=kind, low=low, high=high,
                choices=[dict(label=label, value=value) for label, value in choices], hint=hint,
                depends=depends or {})


def group(title, description, fields):
    return dict(title=title, description=description, fields=fields)


SCHEMA = {
    "activity": [group("投票／问卷", "独立的活动处理与提交开关，默认均关闭。仅处理章节中已核实的必做活动。", [
        field("启用投票／问卷处理", "activity", "enabled", "bool"),
        field("选择题策略", "activity", "choice_strategy", "choice", choices=ACTIVITY_CHOICES,
              hint="随机完全本地；仅显式选择 AI 失败后随机时允许选择题降级。主观选择不计为正确率。"),
        field("文本题策略", "activity", "text_strategy", "choice", choices=ACTIVITY_TEXTS,
              hint="固定好精确输出一个字，不补字、不调用 AI。"),
        field("AI 回复水平", "activity", "reply_level", "choice", choices=DISCUSSION_LEVELS[:3],
              hint="仅影响 AI 文本正文。平台字数限制优先，固定好模式不使用此档位。"),
        field("生成后自动提交投票／问卷", "activity", "auto_submit", "bool",
              hint="独立于测验与讨论。整卷校验通过才提交；关闭时仅保存本地草稿。"),
    ])],
    "discussion": [group("讨论任务", "仅处理所选章节中明确关联的必做讨论；发布、任务完成与成绩分别记录。", [
        field("启用讨论任务处理", "discussion", "enabled", "bool"),
        field("回复水平", "discussion", "reply_level", "choice", choices=DISCUSSION_LEVELS,
              hint="前三档使用上方 AI 服务；固定好完全本地生成，不调用 AI。"),
        field("生成后自动提交讨论回复", "discussion", "auto_submit", "bool",
              hint="关闭时仅保存本地草稿。与测验自动提交分别控制；发布未知时不重复提交。"),
    ])],
    "learning": [
        group("学习与任务调度", "调整本轮学习节奏，设置用于下一次任务。", [
            field("视频倍速", "common", "speed", "number", 1, 2, hint="1–2 倍"),
            field("同时处理的章节数", "common", "jobs", "integer", 1, 16),
            field("未开放章节", "common", "notopen_action", "choice", choices=[("重试（最多 5 轮）", "retry"), ("询问后跳过或停止", "ask"), ("直接跳过", "continue")]),
            field("重试间隔 / 秒", "common", "retry_interval", "number", .1, 3600,
                  depends=dict(section='common', key='notopen_action', value='retry', reason='选择重试未开放章节后生效')),
        ]),
        group("章节检测与学习次数", "仅允许一次作答的课程请保持重做关闭。", [
            field("忽略完成缓存重新检查", "common", "ignore_completion_cache", "bool",
                  hint="默认关闭。开启后按平台状态重新检查所选任务，不跳过本地已完成项；检查结果更新完成缓存。关闭后恢复缓存续跑。"),
            field("答错后允许重做", "common", "work_redo_enabled", "bool"),
            field("最多重做次数", "common", "work_max_retries", "integer", 0, 20,
                  depends=dict(section='common', key='work_redo_enabled', value='true', reason='开启答错后允许重做后生效')),
            field("任务结束后追加学习次数", "common", "add_learning_count", "bool"),
            field("目标总次数", "common", "target_count", "integer", 1, 100000,
                  depends=dict(section='common', key='add_learning_count', value='true', reason='开启追加学习次数后生效')),
        ]),
    ],
    "ai": [group("AI 补全", "题库查询后，只补全仍未解决的题目。", [
        field("启用 AI 补全", "ai", "enabled", "bool"),
        field("服务类型", "ai", "provider", "choice", choices=[("OpenAI 兼容服务", "AI"), ("硅基流动", "SiliconFlow")]),
        field("服务地址", "ai", "endpoint", hint="https://服务地址/v1"),
        field("API Key", "ai", "key", "secret"),
        field("模型名称", "ai", "model"),
        field("模型支持图片识别", "ai", "vision", "bool", hint="关闭后，图片题留待人工处理。"),
        field("首次使用前检查连接", "ai", "check_connection", "bool"),
        field("每次操作 AI 请求硬上限", "ai", "request_limit", "integer", 0, 100000,
              hint="0 表示不设整轮上限；课程运行共享测验、讨论和活动预算，连接检查和预览分别计为一次操作。重试也计数。"),
        field("服务返回 token 累计告警", "ai", "token_warning", "integer", 0, 1000000000,
              hint="0 关闭。仅按服务实际 usage 告警，不阻断请求；用量未知的请求无法纳入总量，不等于账单。"),
    ])],
    "answers": [group("答案处理策略", "题库 → AI → 人工 → 随机，按固定顺序补全。", [
        field("题库答案审核", "matching", "bank_review_policy", "choice", choices=BANK_REVIEW_POLICIES,
              hint="跟随 AI 状态：AI 开启时严格核验，关闭时按选项采用。选项优先会放宽题干审核，可能采用近似题答案；仍须匹配当前选项。"),
        field("人工补全", "desktop", "manual_fallback", "bool", hint="显示题干、图片和选项，供人工填写。"),
        field("随机补全", "desktop", "random_fallback", "bool", hint="默认关闭；随机填写剩余选择题和判断题，不保证正确。"),
        field("全部题目填完后自动提交", "tiku", "submit", "bool", hint="默认只保存。开启后可包含随机答案；仍有空白题目时只保存。"),
    ])],
    "notification": [group("结束通知", "关闭通知不会清空已填写的参数。", [
        field("通知服务", "notification", "provider", "choice", choices=[("关闭通知", ""), ("Server 酱", "ServerChan"), ("Qmsg", "Qmsg"), ("Bark", "Bark"), ("Telegram", "Telegram")]),
        field("通知 URL", "notification", "url", "secret"),
        field("Telegram Chat ID", "notification", "tg_chat_id", "secret"),
    ])],
    "advanced": [
        group("请求间隔与恢复", "网络参数用于后续任务；填写代理不会自动启用 AI。", [
            field("题库查询间隔 / 秒", "tiku", "delay", "number", 0, 3600),
            field("AI 请求间隔 / 秒", "ai", "min_interval_seconds", "number", 0, 3600),
            field("AI 请求超时 / 秒", "ai", "timeout", "integer", 5, 300),
            field("AI 失败重试次数", "ai", "retries", "integer", 0, 5),
            field("AI HTTP 代理（可选）", "ai", "http_proxy", "secret"),
        ]),
        group("GO 题库", "请求频率与恢复策略。", [
            field("最小请求间隔 / 秒", "question_bank", "go_min_interval", "number", 0, 3600),
            field("最多请求次数", "question_bank", "go_retry_times", "integer", 1, 20),
            field("重试退避倍数", "question_bank", "go_retry_backoff", "number", 1, 10),
        ]),
        group("LIKE 知识库", "图片识别独立于 AI 的图片识别选项。", [
            field("启用联网搜索", "question_bank", "likeapi_search", "bool"),
            field("启用图片识别", "question_bank", "likeapi_vision", "bool"),
            field("模型名称", "question_bank", "likeapi_model"),
            field("请求失败时重试", "question_bank", "likeapi_retry", "bool"),
            field("最大重试次数", "question_bank", "likeapi_retry_times", "integer", 0, 20),
        ]),
        group("判断题答案识别", "多个词用英文逗号分隔。", [
            field("表示正确的词", "tiku", "true_list"),
            field("表示错误的词", "tiku", "false_list"),
        ]),
    ],
}

# Keep each service's controls beside its credentials, including while disabled.
go, like = SCHEMA['advanced'][1:3]
SCHEMA['advanced'] = [SCHEMA['advanced'][0], SCHEMA['advanced'][3]]
SCHEMA['ai'][0]['fields'].extend(SCHEMA['advanced'][0]['fields'][1:])
SCHEMA['advanced'][0]['fields'] = [field('旧配置查询间隔 / 秒', 'tiku', 'delay', 'number', 0, 3600,
                                       hint='兼容旧配置；当前题库使用各自的请求间隔。')]
for key, title, prefix, existing in [('bank_go', 'GO 题库', 'go', go['fields']),
                                      ('bank_yanxi', '言溪题库', 'yanxi', []),
                                      ('bank_like', 'LIKE 知识库', 'like', like['fields']),
                                      ('bank_adapter', 'Adapter 题库', 'adapter', [])]:
    fields = []
    if prefix != 'adapter':
        fields.append(field('查询接口地址', 'question_bank', prefix + '_endpoint'))
    if prefix != 'go':
        fields.append(field('最小请求间隔 / 秒', 'question_bank', prefix + '_min_interval', 'number', 0, 3600))
    fields.append(field('请求超时 / 秒', 'question_bank', prefix + '_timeout', 'integer', 5, 300))
    if prefix in ('yanxi', 'adapter'):
        fields.append(field('失败重试次数', 'question_bank', prefix + '_retries', 'integer', 0, 5,
                            hint='仅重试网络异常、HTTP 429 和 5xx；0 表示只请求一次。'))
    SCHEMA[key] = [group(title, '该题库独立使用以下参数。', fields + existing)]
SCHEMA['advanced'].append(group('日志保存', '自动保存已脱敏日志；留空时使用本机数据目录下的 logs 文件夹。', [
    field('日志保存目录', 'logging', 'directory', 'directory', hint='留空使用默认目录'),
]))
SCHEMA['answers'].append(group('相似匹配', '选项先匹配原文，再比较格式和相似度；选项的数字、否定与方向词冲突时不采用。', [
    field('启用相似选项匹配', 'matching', 'fuzzy_options', 'bool'),
    field('选项最低相似度', 'matching', 'min_similarity', 'number', .85, .99, hint='默认 0.92；较高值更严格。'),
    field('最佳与次佳候选的最小差距', 'matching', 'min_margin', 'number', .05, .3, hint='默认 0.08；候选太接近则留待补全。'),
    field('复用本轮相似题库答案', 'matching', 'reuse_similar', 'bool', hint='只复用已匹配选项的题库答案；题型和选项内容须一致，换序时重新定位。'),
    field('复用题干最低相似度', 'matching', 'title_similarity', 'number', .94, .99),
]))
SCHEMA['bank_yanxi'][0]['fields'].extend([
    field('未命中后尝试格式等价题干', 'question_bank', 'yanxi_query_variants', 'bool', hint='仅调整全半角、引号和空白；额外查询可能消耗额度。'),
    field('每题最多额外查询次数', 'question_bank', 'yanxi_variant_limit', 'integer', 0, 2),
])

SCHEMA['advanced'].insert(0, group('请求控制', '单题限制与 AI 整轮上限分别生效，达到预算后留待处理。', [
    field('每题每服务请求预算', 'question_bank', 'request_budget', 'integer', 1, 100,
          hint='包括重试与格式重查；同轮同一章节题目重复处理共享预算。AI 整轮上限在 AI 设置中。'),
]))
SCHEMA['bank_declarative'] = [group('声明式 JSON 题库', '仅支持字段模板和响应路径，不执行脚本。', [
    field('JSON 题库定义', 'question_bank', 'declarative_definition', 'multiline', hint='可导入或导出不含 Token 的模板'),
    field('最小请求间隔 / 秒', 'question_bank', 'declarative_min_interval', 'number', 0, 3600),
    field('请求超时 / 秒', 'question_bank', 'declarative_timeout', 'integer', 5, 300),
    field('失败重试次数', 'question_bank', 'declarative_retries', 'integer', 0, 5),
])]
