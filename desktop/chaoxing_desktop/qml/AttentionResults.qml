import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    readonly property var workbenchSummary: bridge.summary
    id: pane
    property var selected: ({})
    Connections {
        target: bridge
        function onQuestionsReset() { search.text = ""; category.currentIndex = 0; bridge.filterAttention("", ""); detail.close(); pane.selected = ({}) }
    }
    RowLayout {
        Layout.fillWidth: true
        ActionButton { objectName: "verifyPending"; text: "只读核验章节"; enabled: !workbenchSummary.busy; onClicked: bridge.verifyPending() }
    }
    Text {
        property var progress: workbenchSummary.verificationProgress || ({})
        visible: !!progress.phase
        text: "核验已处理 " + (progress.completed || 0) + "/" + (progress.total || 0) + " 章 · 章节／卡片读取 " + (progress.reads || 0) + " · 失败 " + (progress.failures || 0) + " · " + Math.round((progress.elapsed_ms || 0) / 1000) + " 秒"
        color: "#627086"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true
    }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: search; objectName: "attentionSearch"; Layout.fillWidth: true; placeholderText: "搜索标题、课程或原因"; Accessible.name: "筛选待处理事项"; onTextEdited: bridge.filterAttention(text, category.currentValue || "") }
        ChoiceBox {
            id: category; textRole: "label"; valueRole: "value"; Accessible.name: "待处理原因类别"
            model: [{label: "全部需处理", value: ""}, {label: "平台待确认", value: "platform"}, {label: "提交未知", value: "unknown"}, {label: "失败／跳过", value: "failed"}, {label: "草稿／人工", value: "manual"}]
            onActivated: bridge.filterAttention(search.text, currentValue || "")
        }
    }
    Text { text: "汇总本轮待处理原因（最多 2000 项 / 8 MiB" + (workbenchSummary.attentionEvicted ? "，已淘汰 " + workbenchSummary.attentionEvicted + " 项显示记录" : "") + "）。可到各结果标签检查其保留记录；未决意图保持。"; color: "#627086"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
    ListView {
        id: issues; objectName: "attentionList"
        model: bridge.attentionModel
        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6
        activeFocusOnTab: true
        ScrollBar.vertical: ScrollBar {}
        Keys.onReturnPressed: if (currentItem) currentItem.clicked()
        delegate: ItemDelegate {
            required property var modelData
            required property int index
            width: issues.width - 12
            text: modelData.kind + " · " + modelData.status + " · " + modelData.title
            Accessible.name: text
            highlighted: ListView.isCurrentItem
            onClicked: { issues.currentIndex = index; pane.selected = modelData; detail.open() }
            contentItem: Text { text: parent.text; elide: Text.ElideRight; color: "#34435a"; font.pixelSize: 13 }
        }
        Text { visible: issues.count === 0; anchors.centerIn: parent; text: "当前保留记录中没有待处理事项"; color: "#627086" }
    }
    ReadOnlyDialog {
        id: detail; objectName: "attentionDetail"; parent: Overlay.overlay; anchors.centerIn: parent
        title: "待处理原因与下一步"; modal: true; width: Math.min(680, pane.width); height: Math.min(500, parent.height - 60)
        onClosed: issues.forceActiveFocus()
        bodyText: (pane.selected.course || "") + "\n" + (pane.selected.title || "") + "\n\n状态：" + (pane.selected.status || "") + "\n原因：" + (pane.selected.reason || "") + "\n\n下一步：" + (pane.selected.next || "")
    }
}
