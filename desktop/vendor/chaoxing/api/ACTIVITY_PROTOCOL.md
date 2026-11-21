# 章节投票／问卷网页合同

核实日期：2026-10-21。此文描述当前已实现的网页变体，字段来自同一登录会话的学生章节页及其官方脚本。未使用讨论提交、作业上传或移动签到协议。

## 入口与任务关联

- 实际章节附件为 `type=vote`、`property.module=insertvote`；HTML 含 `iframe[module=insertvote]`，原始 iframe 可以没有 `src`。按 `mid` 与服务端附件唯一关联，不按标题或数组顺序匹配。
- 官方 `ueditor.parse.js` 将其渲染至 `/ananas/modules/zt/vote/index-pc-new.html`；旧 `index-pc.html` 入口只保留发现结果，没有套用新版提交合同。
- 克隆课程的原始 iframe `data.id` 与附件 `property.id` 可以不同。本实现保留原始 `entry_property`，采用当前章节服务端附件的原生活动 ID；不互相覆盖。
- 必做资格只接受附件明确的布尔 `job`。未匹配到附件、缺少 `mArg` 或缺少资格时保留发现记录，避免误入空章节成功路径。
- 官方 `documentJob.js/checkJobFinish` 在匹配同一 jobid 后使用服务端附件的 `!job` 判断已完成。本实现将这一事实保留为独立的 `server_finished`，不以 CSS 绿点或 `JC.completed` 当作提交证据。

## 进入答卷与身份

官方模块通过 `GET /mooc-ans/api/work` 进入答卷，使用 `api=1, workId, jobid, needRedirect=true, type=vote, knowledgeid, ktoken, clazzId, enc, courseid, isjob, ut=s, votetype=1, backBtn=1`。模块在当前观察场景允许 `utenc` 为空；没有制造签名或替代会话。

当前已核实的重定向终点位于 `mobilelearn.chaoxing.com`，路径为 `/page/questionnaire/answerQuestionChapter`；已参与／结束时可进入 `studentQuestionChapter` 或 `quesStatisticsChapter`。逐跳校验 HTTPS、主机和 URL 结构，禁止向白名单外发送 Cookie。白名单仅包含 `mooc1.chaoxing.com`、`mooc2-ans.chaoxing.com`、`mobilelearn.chaoxing.com`。

最终 URL 的 `activeId/courseId/classId` 必须与当前附件、课程、班级一致。页面存在 `input[name=puid]` 时核实与会话 UID 一致；该字段属于上传表单，不加入答卷表单。

官方入口可能使用占位 `fid=0`；详情接口返回实际账号单位。非零入口 fid 必须与接口一致；零值不覆盖真实单位空间。持久化隔离使用接口返回的实际 fid。

## 读取题目及本人参与

页面官方初始化方法 `getActiveDetail` 调用：

`GET https://mobilelearn.chaoxing.com/v2/apis/quiz/quizDetail?activeId=...&moreClassAttendEnc=...`

需 `result=1`，`data.puid` 与会话一致，`pptActive.id` 与活动一致，`activeType` 为 14（问卷）或 43（投票）。42 为随堂测验，不进入活动处理链路。`pptActive.status=1` 才允许新提交；页面展示的“手动结束”不用于判断状态。

- `pptUserAttend=null`：该接口已确认当前账号没有参与记录。
- `pptUserAttend.status=3`：官方页面以此判断本人已提交。返回记录如果带有作者／活动标识，必须一致；缺少顶层本人 UID 或参与字段时不推断未参与。
- 提交后重新读取此接口，确认本人参与标记；不使用总票数变化、HTTP 200 或前端跳转代替读回。

`questioncount` 必须等于实际 `questionlist` 长度且不为空。单活动最多 100 题，单题最多 100 选项。当前支持同页纯文本单选 `type=0`、多选 `type=1`、简答 `type=4`、论述 `type=6`。保留每题原生 `id`，题干为 `content`，`ismust=1` 为必答（与原生模板一致）。

选择题 `options` 为 JSON 数组；`name` 是官方表单的**原生提交值**，`content` 是选项内容。在当前样例中 name 恰为 A/B；本实现读取此值，不从显示序号生成 A/B，也不使用内容子串匹配。

多选的 `answerLimitMap.min/max` 均需合法整数；没有该映射时，官方模板不应用数量上限，最大值取可用选项数。填写时至少一项。固定好仅用于文本，精确为单字 `好`。未知 `jsonConfig/config`、分页、图片、媒体附件或扩展选项结构均保留未适配，整卷不自动提交。文本在没有额外格式合同时使用本地 1000 字上限，不声称这是平台通用限制。

## 一次提交与章节任务回调

官方 `answerQuestionChapter.js/submitVote` 先将 `preventsubmit` 设为 1，再序列化 `#answer_form`：

`POST https://mobilelearn.chaoxing.com/v2/apis/studentQuestion/doQuestion`

表单包含 `preventsubmit=1, courseId, classId, activeId`，每题重复的 `questionId`，以及 `answer<原生题目ID>`。单选提供一个原生选项 name，多选重复相同字段，文本保持原文。使用标准表单编码，不手工二次编码。没有附件时不发送上传表单的 puid、_token、file 等字段。

提交前重新读取页面和本人状态；身份／结构改变则保留草稿。持久化 `intent` 成功后，独立 `requests.Session` 发送一次，HTTPAdapter 重试为 0，禁止跟随重定向重发。`result!=1` 是明确业务拒绝；超时或未知结构保留意图，下轮只读核验。

本人参与确认后，官方页面通过父级 `callBackFinishJob` 调用 `/mooc-ans/job/vote`，参数包含 `jobid/knowledgeid/courseid/clazzid/jtoken/checkMicroTopic/microTopicId`。本实现仅在新答卷获得本人参与读回证据后执行一次此原生任务回调。回调成功仍不直接标记任务通过，而是重读同卡附件，匹配 mid/jobid 与服务端完成标记。章节完成由现有 worker 最终重读章节列表确认。

如果回调失败，已确认的参与状态不撤销，任务保持待核验；恢复流程不再次提交答卷。预览不进入以上接口或生产去重存储。

## 本地记录与能力边界

`activities/` 位于应用数据目录，沿用原子写入与跨进程锁。防重复键为账号 UID、课程、班级、实际单位命名空间、原生活动 ID，不含章节、轮次或策略。`intent/submitted/existing/submit_unknown/rejected` 不能因换策略解除保护。记录损坏时原样保留并阻止新提交。签名、Cookie、临时 URL 不写入草稿或桌面事件。

基础混合问卷协议与策略有离线合成样例覆盖；复杂跳转、矩阵、排序、文件上传、未核实的其他问卷平台和旧 questionnaire 模块仍保留待处理。真实验收范围与结果另见本地验收报告，不将一项单选样例扩展为全平台兼容声明。

## 官方来源

- <https://mooc1.chaoxing.com/ananas/ueditor/ueditor.parse.js>
- <https://mooc1.chaoxing.com/ananas/modules/zt/vote/index-pc-new.html>
- <https://mooc1.chaoxing.com/ananas/ueditor/documentJob.js>
- <https://mobilelearn-static.chaoxing.com/mobilelearn/xinfanya/js/questionnaire/answerQuestionChapter.js>

测试源码中的 native/student/course/class 等标识均为虚构替换值；原始登录态材料和真实课程资料不进入公共源码包。
