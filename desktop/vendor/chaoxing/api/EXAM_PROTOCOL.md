# 独立考试 HTTP 协议合同

核实日期：2026-11-23。此文记录在获授权模拟考试上实测核实的超星独立考试（`exam-ans` / `mooc2`）网页协议、字段语义、0 重试保存／交卷与只读核验合同。未核实的监考场次、客户端考试、人脸识别、未知题型均采用 fail-closed 策略，不开放原生答题。

## 受信主机白名单

仅允许向以下 HTTPS 主机发送请求与会话 Cookie：
- `mooc1.chaoxing.com`
- `mooc1-api.chaoxing.com`
- `mooc2-ans.chaoxing.com`

逐跳禁止非 HTTPS、含凭据 URL 及跳往白名单外域名；若重定向至 `passport2.chaoxing.com`，立即判定为会话失效（`auth_error`）。

## 1. 课程考试列表

1. 对每门课程（`courseId`, `clazzId`, `cpi`）访问：
   `GET https://mooc1.chaoxing.com/visit/stucoursemiddle?courseid={courseId}&clazzid={clazzId}&cpi={cpi}&ismooc2=1&v=2`
   跟随受信重定向至 `mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu`，从隐藏域提取 `#examEnc`、`#openc`、`#t`。
2. 读取课程考试列表：
   `GET https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list?courseid={courseId}&clazzid={clazzId}&cpi={cpi}&ut=s&t={t}&enc={examEnc}&openc={openc}`
   （注：`courseid`、`clazzid` 必须为小写；否则服务端返回 `参数传递不正确！`。）
3. 列表项 `li` 结构：
   - 标题：`p.overHidden2`
   - 状态：`p.status`（如 `待做`、`进行中`、`已完成`、`已过期`、`未开始`）
   - 限制提示：`.span_tip span`（如 `完成100%任务点可参加考试`）
   - 剩余/截止时间：`.time`
   - 入口参数：`div[onclick^="goTest("]`，参数依次为 `(courseId, tId, id, endTime, paperId, isRetest, lookpaperEnc, stopExam)`，其中 `tId` 为考试 ID，`id` 为答卷尝试 ID（未领卷时为 `0`）。
   - 官方入口：`https://mooc1.chaoxing.com/exam-ans/exam/test/examcode/examnotes?courseId={courseId}&classId={clazzId}&examId={tId}&cpi={cpi}`

## 2. 进入须知与开卷前置检查（只读）

`GET https://mooc1.chaoxing.com/exam-ans/exam/test/examcode/examnotes?courseId={courseId}&classId={classId}&examId={examId}&cpi={cpi}`

关键隐藏字段与判定：
- `testPaperId`、`testUserRelationId`（`attempt_id`）、`limitTime`（分钟）、`reset`（是否重考）、`expiredAutoSubmit`
- `#startBtn` 的 `data` 属性：开卷入口 `reVersionTestStartNew` 相对路径。
- **不支持标志**（任一满足即 `native_supported=False`，仅提供官方入口）：
  - `isChaoxingExamPc` 非空／为 `1`／`true`（要求专用考试客户端）
  - `needFaceRecognition == "1"` 或 `webNeedFaceRecognition == "1"`（人脸识别）
  - `snapshotMonitor == "1"`、`webSnapshotMonitor == "1"` 或 `electronSnapshotMonitor == "1"`（抓拍监考）
  - `checkSecondDeviceLive == "1"` 或 `liveMonitor == "1"`（二机位／直播监考）
  - `needCode == "1"`（需考试码）
  - `captchaCheck == "1"` 且 `#startBtn[data]` 中 `enc=?`（进入前需人机验证码；不绕过验证码，若用户已在官方入口领卷并传入有效开卷链接除外）

## 3. 开卷与题目解析

`GET https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?...`

### 试卷级字段（`form#submitTest`）
- 表单动作：`/exam-ans/exam/test/reVersionSubmitTestNew`
- 关键隐藏域：`courseId`、`classId`、`paperId`、`testPaperId`、`testUserRelationId`、`cpi`、`openc`、`enc`、`remainTime`（服务端剩余秒数）、`enterPageTime`（服务端当前毫秒时间戳）、`limitTime`、`switchScreenControl`、`snapshotMonitor`、`webSnapshotMonitor`、`electronSnapshotMonitor`、`isChaoxingExamPc`、`liveMonitor`、`limitTimeType`、`groupLimitTimeType`、`questionId`、`type`、`start`。
- 服务端时间与截止时间：以 `enterPageTime` 换算 `server_now_utc`，`deadline_utc = server_now_utc + remainTime`（秒）。
- 若页面存在 `switchScreenControl == "1"`、任意监考标志或限时分卷/单题限时（`limitTimeType != "0"`、`groupLimitTimeType != "0"`），整卷标为 `native_supported=False`。

### 分页与整卷模式
- 若左侧题号导航 `#topicNumberScroll .topicNumber_list li` 数量 `N > 1` 且当前页仅渲染 1 个 `.questionLi`（`getTheNextQuestion=1` 单题模式），按 `start=0..N-1`（上限 200 题）逐页 GET `reVersionTestStartNew` 收集全部题目。
- 若当前页已包含全部 `.questionLi`，直接一次性解析。

### 题目与已保存答案解析
- 若页面含 `.font-cxsecret` 及 Base64 字体定义，复用 `api/font_decoder.py` 进行字符还原。
- 单选（`.singleoption` / `addChoice`）：选项 `span.addChoice[data]` 为提交值（如 `A`），`input[name="answer{qid}"]` 为当前已选值。
- 多选（`addMultipleChoice`）：选项 `span.addMultipleChoice[data]` 为提交值，`input[name="answers{qid}"]`（或 `answer{qid}`）为当前已选字符拼接（如 `"BC"`）。
- 判断（`.mark_name` 含 `判断题` 或选项为对/错、`true`/`false`、`ri`/`wr`）：映射为 `["正确"]` / `["错误"]`，提交时保留原表单值（`true`/`false` 或 `ri`/`wr` 或 `A`/`B`）。
- 填空（`textarea[name^="answerEditor{qid}"]` / `input[name^="answer{qid}"]`）：按输入框数量确定 `blank_count`。
- 简答（`textarea[name="answer{qid}"]`）：`shortanswer`。
- 出现未知题型（如连线 `type=11`、附件题、录音题）或无有效选项／输入框时，整卷标为 `native_supported=False`。

## 4. 0 重试保存与交卷

对应官方 `stu-do-exam.js` 的 `submitForm(tempSave, ...)`：
- `POST https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionSubmitTestNew?classId={classId}&courseId={courseId}&testPaperId={testPaperId}&testUserRelationId={testUserRelationId}&cpi={cpi}&tempSave={true|false}`
- 使用独立 `requests.Session`，`HTTPAdapter(max_retries=0)`，`allow_redirects=False`，不自动重发。
- 表单体包含 `#submitTest` 隐藏域与各题答案字段（`tempSave="true"` 时为临时保存；`tempSave="false"` 时为最终交卷）。
- 响应 JSON：
  - `status == "error"`：明确业务拒绝。
  - `status in ("submitted", "forceSubmitted")`：平台已交卷。
  - 保存后重新 GET `reVersionTestStartNew` 核对已保存答案；交卷后调用 `verify_submitted()` 只读核验。

## 5. 只读交卷结果核验

`GET https://mooc1-api.chaoxing.com/exam-ans/exam/test/look?courseId={courseId}&classId={classId}&examId={testPaperId}&examAnswerId={testUserRelationId}&cpi={cpi}`

- 若页面包含 `未提交考试，请提交后，再查看` 或无权限提示：返回 `confirmed=False`（保持 `submit_unknown`，**绝不**推断为未交卷）。
- 只有查看页包含试卷标题或答卷容器，并明确显示「已交卷」「待批阅」或「最终成绩」时，才返回 `confirmed=True`。仅有领取时间或题目容器时保留待核验状态。

---

测试夹具 [desktop/tests/fixtures/exam/](../../../tests/fixtures/exam/) 中的账号、姓名、课程、考试、尝试 ID 与签名参数均为脱敏替换值；真实凭据与原始抓取材料不进入仓库。
