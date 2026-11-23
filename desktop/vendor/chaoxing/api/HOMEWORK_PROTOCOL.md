# 独立作业网页合同（只读解析、本机草稿与 0 重试暂存）

核实日期：2026-11-23。此文记录在获授权课程作业链接上核实的超星新版独立作业（`mooc2/work/dowork`）页面结构、链接校验与 0 重试平台暂存（`pyFlag=1`）合同。本模块**不包含最终提交（交卷）接口**，仅支持本机草稿与可选的平台暂存。

## 链接白名单校验（`validate_homework_url`）

- 仅允许 `https://` 协议、`*.chaoxing.com` 域名、标准端口（`None` 或 `443`），且 URL 中不得包含明文账号密码（`username`/`password` 或 `password`/`pwd`/`cookie` 等凭据查询参数）。
- 已核实的作业路径：
  - `/mooc-ans/mooc2/work/dowork`
  - `/mooc2-ans/work/dowork`
  - `/mooc-ans/mooc2/work/task`（302 跳转至 `dowork`）
  - `/mooc2-ans/work/task`
  - `/work/doHomeWorkNew`
  - `/mooc-ans/work/doHomeWorkNew`
- 不符合上述条件时立即在本地拒绝并给出中文原因，不发起网络请求。

## 课程作业列表（`list_homeworks`）

1. 访问 `https://mooc1.chaoxing.com/visit/stucoursemiddle?courseid={courseId}&clazzid={clazzId}&cpi={cpi}&ismooc2=1&v=2`，从隐藏域提取 `#workEnc`、`#openc`、`#t`、`#enc`（`stuenc`）。
2. 请求 `GET https://mooc1.chaoxing.com/mooc2/work/list?courseId={courseId}&classId={clazzId}&cpi={cpi}&ut=s&openc={openc}&enc={workEnc}&t={t}&stuenc={stuenc}&isdisplaytable=2`。
3. 解析 `div.bottomList ul li[data]`（`data` 为 `work/task` 直链，`p.overHidden2` 为标题，`p.status` 为状态，`.time` 为剩余时间）。

## 页面结构（`mooc2/work/dowork`）

与旧版章节测验（`.TiMu` + `#workLibraryId`，由 `decode_questions_info()` 解析）不同，新版独立作业使用 `.questionLi` 结构：
- 作业标题：`h2.mark_title` 或 `.mark_title`
- 题目容器：`div.questionLi[data]`（`data` 为原生题目 ID，`typename` 为中文题型名）
- 题干位置：`<h3 class="mark_name">` 之后的兄弟 `<p>` / `<div>` 节点（截至 `<div class="clear">` 或 `<input>`/`<div class="stem_answer">`），题图位于其中的 `img[src]`。
- 题型与选项：
  - 隐藏域 `input[name="answertype{qid}"]`：`0` 单选、`1` 多选、`3` 判断、`2`/`9`/`10` 填空、`4`/`5`/`6`/`7`/`8` 简答。
  - 单选／判断：`.stem_answer .answerBg[onclick*="addChoice"]` 内 `span.choice{qid}[data]` 为选项标识，`.answer_p` 为选项文本。
  - 多选：`.stem_answer .answerBg[onclick*="addMultipleChoice"]` 内 `span.choice{qid}[data]`。
  - 填空：`.stem_answer textarea[name^="answerEditor{qid}"]` 或文本 `input[name^="answer{qid}"]`。
  - 简答：`.stem_answer textarea[name="answer{qid}"]`。
- 加密字体：若页面含 `.font-cxsecret`，复用 `api/font_decoder.py` 解密。
- 不支持题型（如连线、文件上传、录音等）：保留 `id`、`no`、`type="unsupported"`、`type_name`、`title` 摘要与 `supported=False` 及中文原因，**不阻塞**同份作业其余题目的本地编辑与参考答案生成。

## 0 重试平台暂存（`save_once`，`pyFlag=1`）

对应官方 `saveWork()` / `saveAnswer()`：
- 表单：`form#submitForm[action]`（`/mooc-ans/work/addStudentWorkNewWeb?...`），查询参数附加 `pyFlag=1&ua=pc&formType=post&saveStatus=1&version=1`。
- 表单体：保留 `#submitForm` 隐藏域，设置 `pyFlag="1"`、`answerwqbid`（各支持题 ID 以逗号拼接并以逗号结尾），以及各题字段：
  - 单选／判断：`answertype{qid}` + `answer{qid}`（单选为选项 ID；判断为原值 `true`/`false` 等）
  - 多选：`answertype{qid}` + `answer{qid}`（多选字母拼接，如 `"BC"`）
  - 填空：`answertype{qid}` + `tiankongsize{qid}` + `answerEditor{qid}1`..`answerEditor{qid}{n}`
  - 简答：`answertype{qid}` + `answer{qid}`
- 使用独立 `requests.Session`、`HTTPAdapter(max_retries=0)`、`allow_redirects=False` 与原作业页 `Referer`，不自动重发；收到 `status == true` 后重新 `GET` 原作业页核对选项与填空。
