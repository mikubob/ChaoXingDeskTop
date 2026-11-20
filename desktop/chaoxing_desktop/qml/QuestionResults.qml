import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    readonly property var workbenchSummary: bridge.summary
    id: pane
    property string taskId: ""
    property string selectedId: ""
    property var selected: {
        let revision = workbenchSummary.questionRevision
        return selectedId ? bridge.questionDetail(selectedId) : ({})
    }
    Connections {
        target: bridge
        function onQuestionsReset() {
            pending.checked = false
            pane.taskId = ""
            pane.selectedId = ""
            detail.close()
        }
    }
    spacing: 8
    function applyFilter() { bridge.filterQuestions(pending.checked, taskId) }
    onTaskIdChanged: applyFilter()
    RowLayout {
        Layout.fillWidth: true
        CheckBox { id: pending; objectName: "questionPending"; text: "只看待处理 / 拒绝 / 失败"; onToggled: pane.applyFilter() }
        Text { text: pane.taskId ? "已筛选章节" : "全部章节"; color: "#627086"; Layout.fillWidth: true }
        ActionButton { text: "清除章节筛选"; visible: !!pane.taskId; onClicked: pane.taskId = "" }
    }
    Text {
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#627086"; font.pixelSize: 12
        text: {
            let s = workbenchSummary.questionStats
            return "最近 " + workbenchSummary.questionCount + " 条 · 题库轮次 " + (s.query_attempts || 0) +
                   " · 题库请求 " + (s.requests || 0) + " · 题库候选 " + (s.candidates || 0) + " · 已采用 " + (s.resolved || 0) +
                   " · 缓存 " + (s.cache || 0) + " / AI " + (s.ai || 0) + " / 人工 " + (s.manual || 0) + " / 随机 " + (s.random || 0) +
                   (workbenchSummary.questionEvicted ? " · 已淘汰 " + workbenchSummary.questionEvicted + " 条旧记录" : "") + "。统计不代表答案正确率。"
        }
    }
    ListView {
        id: results; objectName: "questionResults"
        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.questionModel
        ScrollBar.vertical: ScrollBar {}
        delegate: ItemDelegate {
            required property var modelData
            required property int index
            width: results.width - 12; height: 76
            onClicked: { pane.selectedId = modelData.id; detail.open() }
            background: Rectangle { color: parent.hovered ? "#eaf1fd" : index % 2 ? "#f8f9fc" : "white"; radius: 6 }
            contentItem: ColumnLayout {
                spacing: 4
                RowLayout {
                    Text { text: modelData.title; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight; color: "#17243a"; font.pixelSize: 13 }
                    Text { text: modelData.stageText; color: modelData.stage === "resolved" ? "#15803d" : "#b45309"; font.bold: true; font.pixelSize: 12 }
                }
                Text { text: (modelData.sourceText ? modelData.sourceText + " · " : "") + modelData.reason; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight; color: "#627086"; font.pixelSize: 12 }
                Text { text: "题目 " + modelData.question_id + " · 轮次 " + modelData.attempt + " · 题库 " + modelData.elapsed_ms + " ms / AI " + (modelData.ai_elapsed_ms || 0) + " ms · 点击查看详情"; color: "#627086"; font.pixelSize: 11 }
            }
        }
        Text { visible: results.count === 0; anchors.centerIn: parent; text: "暂无逐题记录；离线演示也可体验此功能"; color: "#627086" }
    }
    ActionDialog {
        id: detail; objectName: "questionDetail"
        cancelText: "关闭"
        title: "题目处理详情"; modal: true; anchors.centerIn: parent
        width: Math.min(pane.width, 760); height: Math.min(560, pane.Window.height - 100)
        background: Rectangle { color: "white"; radius: 12; border.color: "#dbe3ef" }
        contentItem: ScrollView {
            clip: true; contentWidth: availableWidth
            TextArea {
                width: parent.width; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
                color: "#17243a"; font.pixelSize: 13
                text: (pane.selected.title || "该记录已被清除或淘汰") + "\n\n选项：\n" + (pane.selected.options || "") +
                      "\n\n采用答案：\n" + (pane.selected.answer_letters ? pane.selected.answer_letters.split("").join("、") + " · " : "") +
                      (pane.selected.answer || (pane.selected.stage === "resolved" ? "已采用" : "未采用")) + "\n原因：" + (pane.selected.reason || "") +
                      "\n来源：" + (pane.selected.sourceText || "待处理") + "\n匹配：" + (({exact: "原文匹配", format: "格式等价", fuzzy: "相似选项", memory: "本轮缓存", letter: "选项编号"})[pane.selected.match_mode] || "答案格式核验") +
                      "\n耗时：题库 " + (pane.selected.elapsed_ms || 0) + " ms；AI " + (pane.selected.ai_elapsed_ms || 0) + " ms；人工 " + (pane.selected.manual_elapsed_ms || 0) + " ms" +
                      "\nAI 请求 " + (pane.selected.ai_requests || 0) + " 次，重试 " + (pane.selected.ai_retries || 0) + " 次" +
                      "\n\n服务记录：\n" + (pane.selected.attempts || []).map(function(a) {
                          return a.provider + " · " + a.elapsed_ms + " ms\n" + a.reason +
                                 (a.returned_title ? "\n返回题干：" + a.returned_title : "") +
                                 (a.candidate ? "\n候选答案：" + a.candidate : "")
                      }).join("\n\n")
            }
        }
    }
}
