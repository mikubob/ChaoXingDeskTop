import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

ColumnLayout {
    readonly property var workbenchSummary: bridge.summary
    id: page
    property bool logsOpen: false
    property bool followLogs: true
    function mediaTime(seconds) {
        let value = Math.max(0, Math.floor(seconds || 0))
        return Math.floor(value / 60) + ":" + (value % 60 < 10 ? "0" : "") + value % 60
    }
    spacing: page.height < 540 ? 10 : 16
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Repeater {
            model: [{label: "本轮章节", key: "total", color: "#2563eb"}, {label: "已核验章节", key: "success", color: "#15803d"}, {label: "待完成 / 未核验章节", key: "pending", color: "#b45309"}, {label: "失败章节", key: "failed", color: "#dc2626"}]
            Rectangle {
                required property var modelData
                Layout.fillWidth: true; Layout.preferredHeight: 80; radius: 10; color: "white"; border.color: "#e2e7ef"
                Column {
                    anchors { left: parent.left; top: parent.top; margins: 16 } spacing: 8
                    Text { text: modelData.label; font.pixelSize: 12; color: "#627086" }
                    Text { text: workbenchSummary[modelData.key]; font.pixelSize: 28; font.bold: true; color: modelData.color }
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: "本轮处理进度"; color: "#34435a"; font.pixelSize: 13 }
        Basic.ProgressBar {
            id: totalBar; Layout.fillWidth: true; Layout.preferredHeight: 10; value: workbenchSummary.progress / 100
            background: Rectangle { color: "#cbd5e1"; radius: 5 }
            contentItem: Item { Rectangle { width: parent.width * totalBar.visualPosition; height: parent.height; color: "#2563eb"; radius: 5 } }
        }
        Text { text: workbenchSummary.progress + "%"; font.bold: true; color: "#2563eb" }
    }
    Text { text: "按章节统计，同一章节可含多个任务点；处理进度 100% 表示本轮结束，待完成 / 未核验章节请在平台确认。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#627086"; font.pixelSize: 12 }
    Text { text: "本次操作 AI 请求 " + bridge.aiUsage.requests + " / " + (bridge.aiUsage.limit || "不限") + " · 服务返回 token " + bridge.aiUsage.tokens + "（" + bridge.aiUsage.known + " 次有 usage，" + bridge.aiUsage.unknown + " 次用量未知）· 不等于账单或正确率"; color: "#627086"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Text { visible: !!bridge.aiWarning; text: bridge.aiWarning; color: "#995a0d"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
    SplitView {
        Layout.fillWidth: true; Layout.fillHeight: true; orientation: Qt.Vertical
        Rectangle {
            SplitView.fillHeight: true; SplitView.minimumHeight: page.height < 540 ? 140 : 190; color: "white"; radius: 12; border.color: "#e2e7ef"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16; spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    TabBar {
                        id: resultTabs; objectName: "resultTabs"; Layout.fillWidth: true
                        TabButton { text: "章节任务" }
                        TabButton { text: "逐题结果（" + workbenchSummary.questionCount + "）" }
                        TabButton { text: "讨论记录（" + workbenchSummary.discussionCount + "）" }
                        TabButton { text: "投票／问卷（" + workbenchSummary.activityCount + "）" }
                        TabButton { objectName: "attentionTab"; text: "需处理" }
                    }
                    Text { text: workbenchSummary.busy ? "运行中" : "当前任务"; font.pixelSize: 12; color: "#627086" }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { objectName: "resume_button"; text: "继续上次任务"; visible: !!workbenchSummary.resumeScoped; enabled: !!workbenchSummary.resumeScoped && workbenchSummary.resume.enabled && !workbenchSummary.busy; onClicked: bridge.resumeTasks() }
                    Text { visible: !!workbenchSummary.resumeScoped; text: workbenchSummary.resume.reason || ""; color: "#627086"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                    ActionButton { objectName: "history_button"; text: "运行摘要"; onClicked: { bridge.loadRunHistory(); history.open() } }
                    ActionButton { objectName: "recovery_button"; text: "从摘要选课…"; enabled: !workbenchSummary.busy; onClicked: { bridge.clearUiFeedback("recovery"); bridge.loadRunHistory(); recovery.open() } }
                    ActionButton { objectName: "saved_drafts_button"; text: "本地副本…"; enabled: !workbenchSummary.busy; onClicked: { bridge.clearUiFeedback("savedDrafts"); savedDrafts.rows = bridge.listDraftCopies(); savedDrafts.open() } }
                    ActionButton { objectName: "capacity_button"; text: "容量…"; onClicked: capacity.open() }
                    Text { visible: !!bridge.historyError; text: bridge.historyError; color: "#b42323"; wrapMode: Text.Wrap; font.pixelSize: 12; Layout.fillWidth: true }
                }
                ListView {
                    visible: resultTabs.currentIndex === 0
                    id: taskList; objectName: "taskList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.taskModel
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        property var media: modelData.media || ({})
                        width: taskList.width - 10; height: media.name ? 96 : 52; color: index % 2 ? "#f8f9fc" : "white"
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 6
                            RowLayout {
                            Layout.fillWidth: true; spacing: 18
                            Text { text: modelData.course; Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; font.pixelSize: 13; color: "#34435a" }
                            ActionButton { text: "题目"; subtle: true; onClicked: { questions.taskId = modelData.id; resultTabs.currentIndex = 1 } }
                            Text { text: modelData.title; Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; font.pixelSize: 13; color: "#34435a" }
                            Rectangle {
                                Layout.preferredWidth: 136; Layout.preferredHeight: 28; radius: 6
                                color: modelData.status === "success" ? "#e3f4e9" : modelData.status === "failed" ? "#ffe9e9" : modelData.status === "running" ? "#e4edff" : modelData.status === "pending" || modelData.status === "processed" || modelData.status === "retry" ? "#fff0d8" : "#edf0f5"
                                Text { anchors.centerIn: parent; text: modelData.statusText; color: modelData.status === "success" ? "#15803d" : modelData.status === "failed" ? "#dc2626" : modelData.status === "running" ? "#2563eb" : "#627086"; font.pixelSize: 12 }
                            }
                            }
                            RowLayout {
                                visible: !!media.name; Layout.fillWidth: true; spacing: 10
                                Text { text: (media.kind === "Audio" ? "音频 · " : "视频 · ") + (media.name || ""); Layout.fillWidth: true; elide: Text.ElideRight; color: "#17243a"; font.pixelSize: 13 }
                                Text { text: page.mediaTime(media.position) + " / " + page.mediaTime(media.duration) + " · " + (media.percent || 0) + "%"; font.bold: true; color: "#1d4ed8" }
                                Text { objectName: "media_status_" + modelData.id; text: media.statusText || ""; color: "#92400e"; font.pixelSize: 12 }
                            }
                            Basic.ProgressBar {
                                id: mediaBar
                                objectName: "media_" + modelData.id
                                visible: !!media.name; Layout.fillWidth: true; Layout.preferredHeight: 8
                                value: media.duration > 0 ? media.position / media.duration : 0
                                background: Rectangle { color: "#cbd5e1"; radius: 4 }
                                contentItem: Item { Rectangle { width: parent.width * mediaBar.visualPosition; height: parent.height; radius: 4; color: "#2563eb" } }
                            }
                        }
                    }
                    Text { visible: taskList.count === 0; anchors.centerIn: parent; text: "选择课程后，章节任务将在这里显示"; color: "#627086"; font.pixelSize: 14 }
                }
                QuestionResults { id: questions; visible: resultTabs.currentIndex === 1; Layout.fillWidth: true; Layout.fillHeight: true }
                DiscussionResults { visible: resultTabs.currentIndex === 2; Layout.fillWidth: true; Layout.fillHeight: true }
                ActivityResults { visible: resultTabs.currentIndex === 3; Layout.fillWidth: true; Layout.fillHeight: true }
                AttentionResults { visible: resultTabs.currentIndex === 4; Layout.fillWidth: true; Layout.fillHeight: true }
            }
        }
        Rectangle {
            SplitView.preferredHeight: page.logsOpen ? 240 : 56
            SplitView.minimumHeight: page.logsOpen ? 160 : 56
            SplitView.maximumHeight: page.logsOpen ? 10000 : 56
            color: "#152339"; radius: 10
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "运行日志"; color: "white"; font.bold: true; font.pixelSize: 14; Layout.fillWidth: true }
                    ActionButton { text: "清空显示"; onClicked: bridge.clearLogs() }
                    ActionButton { text: "删除日志记录"; tone: "danger"; onClicked: deleteLogsDialog.open() }
                    ActionButton { text: "导出保留日志"; tone: "save"; subtle: true; onClicked: bridge.fileAction("export_logs") }
                    ActionButton { text: page.logsOpen ? "收起" : "展开"; onClicked: page.logsOpen = !page.logsOpen }
                }
                Text { visible: page.logsOpen; text: workbenchSummary.logSaveError || ("保存目录：" + workbenchSummary.logDirectory); color: workbenchSummary.logSaveError ? "#fca5a5" : "#e2e8f0"; Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: 12 }
                RowLayout {
                    visible: page.logsOpen; Layout.fillWidth: true
                    Basic.TextField { id: logSearch; objectName: "logSearch"; Layout.fillWidth: true; placeholderText: "搜索当前保留日志"; Accessible.name: "搜索日志"; onTextEdited: logFilterTimer.restart() }
                    ChoiceBox { id: logLevel; model: ["全部级别", "警告及错误", "仅错误"]; Accessible.name: "日志级别"; onActivated: logFilterTimer.restart() }
                    Basic.CheckBox {
                        id: followCheck; text: "跟随最新"; checked: page.followLogs; onToggled: page.followLogs = checked
                        contentItem: Text { text: followCheck.text; color: "#e2e8f0"; leftPadding: followCheck.indicator.width + followCheck.spacing; verticalAlignment: Text.AlignVCenter }
                    }
                    ActionButton { text: "到底部"; onClicked: { page.followLogs = true; logList.positionViewAtEnd() } }
                }
                Timer { id: logFilterTimer; interval: 150; onTriggered: bridge.filterLogs(logLevel.currentIndex === 1 ? "WARNING" : logLevel.currentIndex === 2 ? "ERROR" : "", logSearch.text) }
                ListView {
                    id: logList; objectName: "logList"
                    visible: page.logsOpen; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: bridge.logModel
                    ScrollBar.vertical: ScrollBar {}
                    onMovementStarted: page.followLogs = false
                    onCountChanged: Qt.callLater(function() { if (page.logsOpen && page.followLogs) logList.positionViewAtEnd() })
                    onVisibleChanged: if (visible && page.followLogs) Qt.callLater(positionViewAtEnd)
                    delegate: TextEdit {
                        required property string modelData
                        width: logList.width - 14; text: modelData
                        readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText
                        color: modelData.indexOf("ERROR") >= 0 ? "#fca5a5" : modelData.indexOf("WARNING") >= 0 ? "#fde68a" : "#e2e8f0"; font.family: "Consolas"; font.pixelSize: 13; wrapMode: TextEdit.Wrap
                    }
                    Text {
                        visible: logList.count === 0; width: parent.width
                        text: "暂无日志。离线演示和真实任务的记录将在这里显示。"
                        color: "#c7d4e8"; font.pixelSize: 12; wrapMode: Text.Wrap
                    }
                }
            }
        }
    }
    ReadOnlyDialog {
        id: capacity; objectName: "capacityDialog"
        title: "显示与写盘容量"; width: Math.min(720, page.width); height: Math.min(580, parent.height - 50)
        bodyText: bridge.capacityInfo
    }
    ActionDialog {
        id: savedDrafts; objectName: "savedDraftsDialog"; property var rows: []
        parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "按账号保存的人工副本"; width: Math.min(600, page.width); height: Math.min(440, parent.height - 50)
        padding: 18
        background: Rectangle { color: "white"; radius: 12; border.color: "#cdd7e6" }
        primaryText: "打开所选副本"; cancelText: "关闭"; autoAccept: false
        primaryEnabled: savedDraft.currentIndex >= 0 && rows.length > 0 && !workbenchSummary.busy && (bridge.uiFeedback.savedDrafts || {}).tone !== "danger"
        contentItem: ColumnLayout {
            Label { text: "最多 20 项 / 2 MiB，容量满不自动丢弃人工编辑。来源未在当前轮确认时只读，可复制或导出。这里不会改变提交去重记录。"; Layout.fillWidth: true; wrapMode: Text.Wrap }
            ChoiceBox { id: savedDraft; Layout.fillWidth: true; enabled: savedDrafts.rows.length > 0; model: savedDrafts.rows; textRole: "title"; valueRole: "id"; Accessible.name: "选择人工副本" }
            ActionButton { objectName: "deleteSavedDraft"; text: "删除所选人工副本…"; tone: "danger"; subtle: true; enabled: savedDraft.currentIndex >= 0 && savedDrafts.rows.length > 0 && !workbenchSummary.busy && (bridge.uiFeedback.savedDrafts || {}).tone !== "danger"; onClicked: { deleteDraft.copyId = savedDraft.currentValue; deleteDraft.copyVersion = savedDrafts.rows[savedDraft.currentIndex].version; deleteDraft.copyTitle = savedDrafts.rows[savedDraft.currentIndex].title; deleteDraft.open() } }
            NoticeBanner { visible: !savedDrafts.rows.length && (bridge.uiFeedback.savedDrafts || {}).tone !== "danger"; message: "当前账号没有保存的编辑副本。请先从讨论或问卷草稿中创建并保存本地副本。"; Layout.fillWidth: true }
            OperationNotice { objectName: "savedDraftsNotice"; scope: "savedDrafts"; Layout.fillWidth: true }
            Item { Layout.fillHeight: true }
        }
        onPrimaryTriggered: if (bridge.openSavedDraft(savedDraft.currentValue || "")) accept()
    }
    ActionDialog {
        id: deleteDraft; objectName: "deleteDraftDialog"; property string copyId: ""; property int copyVersion: 0; property string copyTitle: ""
        parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "删除人工编辑副本？"; width: Math.min(550, page.width); height: Math.min(370, parent.height - 50); padding: 18
        background: Rectangle { color: "white"; radius: 12; border.color: "#cdd7e6" }
        primaryText: "删除此副本"; primaryTone: "danger"; autoAccept: false
        contentItem: ColumnLayout {
            NoticeBanner { tone: "warning"; message: "将删除「" + deleteDraft.copyTitle + "」的人工副本（版本 " + deleteDraft.copyVersion + "），无法撤销。请先复制或导出正文。原始草稿、提交意图与平台状态保留。"; Layout.fillWidth: true }
            OperationNotice { scope: "savedDrafts"; Layout.fillWidth: true }
            Item { Layout.fillHeight: true }
        }
        onPrimaryTriggered: if (bridge.deleteSavedDraft(copyId, copyVersion)) { savedDrafts.rows = bridge.listDraftCopies(); accept() }
    }
    ActionDialog {
        id: recovery; objectName: "recoveryDialog"; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "重新读课后选择未完成课程"; width: Math.min(620, page.width); height: Math.min(460, parent.height - 50)
        padding: 18
        background: Rectangle { color: "white"; radius: 12; border.color: "#cdd7e6" }
        primaryText: "重新读课并选课"; autoAccept: false
        primaryEnabled: recoveryRun.currentIndex >= 0 && bridge.runHistory.length > 0 && !bridge.historyError && !workbenchSummary.busy
        contentItem: ColumnLayout {
            Label { text: "先连接原账号，再重新读取身份与课程。仅选择摘要中可定位的未完成课程；不会启动任务或重放旧写请求。请检查策略后手动开始新轮。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            ChoiceBox { id: recoveryRun; objectName: "recoveryRun"; Layout.fillWidth: true; enabled: bridge.runHistory.length > 0; model: bridge.runHistory; textRole: "time"; valueRole: "id"; Accessible.name: "选择运行摘要" }
            NoticeBanner { message: bridge.historyError || (!bridge.runHistory.length ? "当前账号暂无摘要，请先完成一轮任务后再使用此入口。" : "仅保存前 1000 个未完成章节定位；旧摘要没有定位时需手动选课。"); tone: bridge.historyError ? "danger" : "info"; Layout.fillWidth: true }
            OperationNotice { objectName: "recoveryNotice"; scope: "recovery"; Layout.fillWidth: true }
            Item { Layout.fillHeight: true }
        }
        onPrimaryTriggered: if (bridge.prepareRecovery(recoveryRun.currentValue || "")) accept()
    }
    ReadOnlyDialog {
        id: history; objectName: "historyDialog"; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "当前账号的运行摘要（只读）"; width: Math.min(720, page.width); height: Math.min(560, parent.height - 50)
        bodyText: {
            let lines = ["摘要不是平台提交证据。再次处理需重新读取课程；不会重放旧请求。"]
            let names = {waiting: "等待", running: "处理中", processed: "待核验", success: "已核验", pending: "待完成", failed: "失败", skipped: "跳过", stopped: "停止"}
            for (let run of bridge.runHistory) {
                let counts = Object.keys(run.counts).map(function(key) { return (names[key] || "其他状态") + " " + run.counts[key] }).join(" · ")
                lines.push("\n" + run.time + " · " + run.state + "\n章节状态：" + counts)
                if (run.courses && run.courses.length) lines.push("所选课程：" + run.courses.map(function(course) { return course.title }).join("；"))
                if (run.ai) lines.push("AI 请求 " + run.ai.requests + " · 服务返回 token " + run.ai.tokens + " · 用量未知 " + run.ai.unknown + " 次")
                for (let task of run.tasks) lines.push(task.course + " / " + task.title + " · " + (names[task.status] || "其他状态"))
                if (run.total > 1000) lines.push("章节较多：仅保留前 1000 个未完成章节标题，计数覆盖本轮。")
            }
            if (!bridge.runHistory.length) lines.push(bridge.historyError || "暂无摘要；新运行将在此保留有限快照。")
            return lines.join("\n")
        }
    }
    ActionDialog {
        id: deleteLogsDialog; objectName: "deleteLogsDialog"; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "删除当前目录的应用日志？"; width: Math.min(540, page.width); height: 260
        primaryText: "删除日志文件"; primaryTone: "danger"
        contentItem: NoticeBanner { tone: "warning"; message: "将删除下列目录中由本应用命名的日志文件，并清空当前显示。此操作无法撤销。\n\n" + workbenchSummary.logDirectory }
        onAccepted: bridge.deleteLogs()
    }
}
