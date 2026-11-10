# 离线样本治理索引

维护日期：2026-10-24。此目录和测试生成器只允许合成、公开协议形状的样本；不得放入真实 Cookie、UID、签名地址、个人题目／答案或服务密钥。

| 样本／生成器 | 来源与变体 | 预期与回归 |
| --- | --- | --- |
| `ocs_protocol_cases.json` | 合成受限题库响应 | `test_bank_protocols.py` / `test_ocs_improvements.py`，不执行脚本 |
| `verification_cases.json` | synthetic 章节证据，2026-10-24 建立 | `test_workbench_upgrade.py`，完成／待定／未知／登录／业务失败；非布尔完成值不得转为成功 |
| `test_discussion.cards_html`、`topic`、`reply` | 合成 insertbbs → groupweb v3，协议边界见 vendor 的 DISCUSSION_PROTOCOL.md | 同名测试中的 UID、话题／班级、分页与一次写入恢复矩阵 |
| `test_activity.card_html`、`native_questions`、`context` | 合成 insertvote-v2 → questionnaire 网页合同 | 同名测试中的结构、原生 ID、本人参与、未知意图、单次发送和读回 |
| `test_workbench_continuation` | 合成空任务卡／未知／身份错位／本人有无或未知记录；草稿与 AI 档案故障注入 | 只读路径不调用 get_job_list 的空页完成副作用；编辑不碰生产意图；容量、冲突、磁盘／DPAPI 失败保留原件 |
| `workbench_ui_smoke.py` | 合成中文长文本、正常混合卷、100 题 × 100 短选项 | 980／1280、软件缩放、输入与弹窗；超过事件 512 KiB 的长选项组合应拒绝，不绕过容量 |
| `retired-runtime-0.4.2-evidence.json` | 已发布 v0.4.2 安装器的证据（名称、字节数、SHA-256、profile、条目数）；在隔离目录静默安装后枚举实际写入的文件，并用 7-Zip 交叉核对，不含任何载荷文件 | `test_bundle_policy.py`：退役清单条目数与证据一致、只覆盖 `_internal/` 旧运行时、无通配符与用户数据 |

以上日期是样本维护日期，不是假称抓取真实页面的日期。新样本应在名称或元数据注明 synthetic、协议变体及预期；写入接口必须由 mock／失败哨兵替换。新增真实协议变体需另有授权和脱敏审查，不能自动访问课程补样本。GUI 产物只写入忽略的 build 目录或临时目录，不能进入源码白名单。

本次未修改 vendor 协议源码。固定上游基线与桌面改编来源见 THIRD_PARTY_NOTICES.md；未重新联网比较上游最新代码，因此不声称已同步最新协议。只读扩展在 `chaoxing_desktop/verification.py`，不把 `base.get_job_list` 当作只读函数复用。
