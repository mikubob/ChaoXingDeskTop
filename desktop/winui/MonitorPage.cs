using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private readonly Dictionary<string, TextBlock> chapterStatValues = new();
    private FrameworkElement BuildMonitor()
    {
        var grid = new Grid { RowSpacing = 12 };
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }); grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var statGrid = new Grid { ColumnSpacing = 14 };
        string[] labels = ["本轮章节", "已核验章节", "待完成 / 未核验章节", "失败章节"];
        string[] keys = ["total", "success", "pending", "failed"];
        string[] colors = ["AccentBrush", "SuccessBrush", "WarningBrush", "ErrorBrush"];
        for (int i = 0; i < keys.Length; i++)
        {
            var key = keys[i]; var value = Text("0", 26); var label = Text(labels[i], 12);
            value.Foreground = label.Foreground = ThemeBrush(colors[i]);
            chapterStatValues[key] = value;
            var card = Card(Column(value, label));
            statGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }); statGrid.Children.Add(card); Grid.SetColumn(card, i);
            refreshers.Add(() => value.Text = J.S(Summary, key, "0"));
        }
        var taskList = ResultList("tasks");
        var progress = new ProgressBar { Height = 6 };
        refreshers.Add(() => progress.Value = J.I(Summary, "progress"));
        var tasks = new Grid { RowSpacing = 12 };
        tasks.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); tasks.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); tasks.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var verifyChapters = Button("核验章节", async () => await Call("verifyPending"), true);
        verifyChapters.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        ToolTipService.SetToolTip(verifyChapters, "只读检查待完成章节的平台进度");
        var verifyRecords = Button("核验讨论与活动", async () => await Call("verifyRecords"), true);
        var discover = new MenuFlyoutItem { Text = "全量发现核验…" };
        discover.Click += (_, _) => EnqueueDialog(async () =>
        {
            if (await Confirm("全量发现核验", "将读取当前课程的全部关联任务卡和本人证据，仅用于只读核验。", "开始只读核验"))
                await Call("verifyAllRecords");
        });
        var more = new DropDownButton { Content = "更多核验", Flyout = new MenuFlyout { Items = { discover } } };
        idleControls.Add(more);
        var verification = Row(verifyChapters, verifyRecords, more);
        var records = Row(Button("运行摘要", () => { EnqueueDialog(ShowHistory); return Task.CompletedTask; }),
            Button("人工副本", () => { EnqueueDialog(ShowSavedDrafts); return Task.CompletedTask; }));
        var tools = new Grid { ColumnSpacing = 24, RowSpacing = 10, ChildrenTransitions = LayoutTransitions() };
        tools.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        tools.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        tools.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tools.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tools.Children.Add(verification); tools.Children.Add(records);
        void ArrangeTools()
        {
            bool narrow = tools.ActualWidth < 680;
            Grid.SetColumnSpan(verification, narrow ? 2 : 1);
            Grid.SetRow(records, narrow ? 1 : 0);
            Grid.SetColumn(records, narrow ? 0 : 1);
            Grid.SetColumnSpan(records, narrow ? 2 : 1);
            records.HorizontalAlignment = narrow ? HorizontalAlignment.Left : HorizontalAlignment.Right;
        }
        tools.SizeChanged += (_, _) => ArrangeTools(); ArrangeTools();
        tasks.Children.Add(tools); tasks.Children.Add(progress); Grid.SetRow(progress, 1); tasks.Children.Add(taskList); Grid.SetRow(taskList, 2);
        var tabs = Tabs(("章节任务", tasks), ("逐题结果", FilteredResults("questions")), ("讨论记录", FilteredResults("discussions")), ("投票／问卷", FilteredResults("activities")), ("需处理", FilteredResults("attention")));
        var logSearch = new TextBox { PlaceholderText = "搜索当前保留日志", Width = 250 };
        var level = new ComboBox { ItemsSource = new[] { "全部级别", "警告及错误", "仅错误" }, SelectedIndex = 0, Width = 145 };
        async Task Filter() => await Call("filterLogs", level.SelectedIndex == 1 ? "WARNING" : level.SelectedIndex == 2 ? "ERROR" : "", logSearch.Text);
        logSearch.TextChanged += async (_, _) => { if (!refreshing) await Filter(); }; level.SelectionChanged += async (_, _) => { if (!refreshing) await Filter(); };
        logText = new TextBox { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = 180, FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"), FontSize = 12 };
        var logPanel = new Expander { Header = "运行日志", HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, Content = Column(Row(logSearch, level), logText,
            Row(Button("清空显示", async () => await Call("clearLogs")), Button("删除日志记录…", async () => { if (await Confirm("删除日志记录？", "将删除当前日志目录内的应用日志文件并清空显示。", "删除日志记录")) await Call("deleteLogs"); }),
                Button("导出保留日志", () => FileAction("fileAction", ["export_logs"], true, "log", "chaoxing-desktop")), Button("容量…", async () => await ShowText("显示与写盘容量", J.S(backend.State, "capacityInfo"))))) };
        grid.Children.Add(statGrid); grid.Children.Add(tabs); Grid.SetRow(tabs, 1); grid.Children.Add(logPanel); Grid.SetRow(logPanel, 2);
        return grid;
    }

    private ListView ResultList(string name)
    {
        var list = new ListView { ItemTemplate = (DataTemplate)Root.Resources["RecordTemplate"], SelectionMode = ListViewSelectionMode.None, IsItemClickEnabled = true, HorizontalContentAlignment = HorizontalAlignment.Stretch, ItemContainerStyle = StretchListStyle() };
        list.ItemClick += (_, args) => { if (args.ClickedItem is RecordRow row) EnqueueDialog(() => ShowRecord(name, row.Data)); };
        resultLists.Add(name, list); return list;
    }
    private UIElement FilteredResults(string name)
    {
        var list = ResultList(name);
        var search = new TextBox { PlaceholderText = "搜索当前保留记录", Width = 260 };
        var pending = new CheckBox { Content = "仅待处理" };
        var stages = new ComboBox { ItemsSource = name == "attention" ? new[] { "全部类别", "platform", "manual", "unknown", "failed" } : name == "discussions" ? new[] { "全部状态", "draft", "published", "existing", "publish_unknown", "pending", "skipped" } : new[] { "全部状态", "draft", "submitted", "existing", "submit_unknown", "pending", "rejected" }, SelectedIndex = 0, Width = 160 };
        async Task Filter()
        {
            if (name == "questions") await Call("filterQuestions", pending.IsChecked == true, search.Text);
            else await Call(name == "attention" ? "filterAttention" : name == "discussions" ? "filterDiscussions" : "filterActivities", search.Text, stages.SelectedIndex <= 0 ? "" : stages.SelectedItem?.ToString());
        }
        search.TextChanged += async (_, _) => { if (!refreshing) await Filter(); }; pending.Click += async (_, _) => await Filter(); stages.SelectionChanged += async (_, _) => { if (!refreshing) await Filter(); };
        var grid = new Grid { RowSpacing = 10 }; grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        if (name == "questions") { search.PlaceholderText = "按章节任务 ID 筛选"; grid.Children.Add(Row(search, pending)); }
        else grid.Children.Add(Row(search, stages));
        grid.Children.Add(list); Grid.SetRow(list, 1); return grid;
    }
    private void RefreshRecords(string name, ListView list)
    {
        string signature = backend.State[name]?.ToJsonString() ?? "[]";
        if (listSignatures.GetValueOrDefault(name) == signature) return;
        listSignatures[name] = signature;
        SyncRows(name, list, J.A(backend.State[name]).Select(n => new RecordRow(n)));
    }
    private void SyncRows(string name, ListView list, IEnumerable<RecordRow> incoming)
    {
        if (!rowCollections.TryGetValue(name, out var rows)) { rows = new(); rowCollections[name] = rows; list.ItemsSource = rows; }
        var next = incoming.ToArray();
        for (int i = 0; i < next.Length; i++)
        {
            if (i == rows.Count) rows.Add(next[i]);
            else if (rows[i].Identity == next[i].Identity) rows[i].UpdateFrom(next[i]);
            else rows[i] = next[i];
        }
        while (rows.Count > next.Length) rows.RemoveAt(rows.Count - 1);
    }
    private async Task ShowRecord(string kind, JsonNode row)
    {
        if (kind == "tasks" || kind == "attention") { await ShowText(J.S(row, "title"), ChapterDetailText(row)); return; }
        var method = kind == "questions" ? "questionDetail" : kind == "discussions" ? "discussionDetail" : "activityDetail";
        var record = await Call(method, J.S(row, "id")) ?? row;
        var text = J.S(record, "detailText"); if (text.Length == 0) text = DetailText(record);
        var body = ReadOnlyBody(text);
        var dialog = Dialog(J.S(row, "title", "记录详情"), body);
        if (kind != "questions")
        {
            dialog.PrimaryButtonText = "编辑本地副本";
            dialog.SecondaryButtonText = "复制正文";
            dialog.SecondaryButtonClick += async (_, args) => { args.Cancel = true; await Call(kind == "discussions" ? "copyDiscussion" : "copyActivity", J.S(row, "id")); };
        }
        currentDialog = dialog;
        try { if (await dialog.ShowAsync() == ContentDialogResult.Primary) await Call("openDraftCopy", kind == "discussions" ? "discussion" : "activity", J.S(row, "id")); }
        finally { currentDialog = null; }
    }
    private static string ChapterDetailText(JsonNode row)
    {
        var lines = new List<string>();
        void Add(string label, string value) { if (!string.IsNullOrWhiteSpace(value)) lines.Add(label + "：" + value); }
        Add("课程", J.S(row, "course"));
        string kind = J.S(row, "kind");
        Add("记录类型", kind);
        Add(kind.Length == 0 || kind == "章节" ? "章节" : "标题", J.S(row, "title"));
        Add("状态", J.S(row, "statusText", J.S(row, "status")));
        Add("处理说明", J.S(row, "reason"));
        if (row["media"] is JsonObject media)
        {
            Add(J.S(media, "kind") == "Audio" ? "音频" : "视频", J.S(media, "name"));
            Add("播放进度", J.I(media, "percent") + "%");
            Add("媒体状态", J.S(media, "statusText"));
        }
        Add("最近核验", J.S(row, "checked_at"));
        Add("平台核验结果", J.S(row, "evidence"));
        Add("下一步", J.S(row, "next"));
        // Platform identifiers remain available for support, with readable labels.
        Add("课程编号", J.S(row, "courseId"));
        Add("班级编号", J.S(row, "clazzId"));
        Add("章节编号", J.S(row, "pointId"));
        return string.Join("\n\n", lines);
    }
    private static string DetailText(JsonNode node)
    {
        if (node is JsonObject obj) return string.Join("\n\n", obj.Select(p => (DetailLabels.GetValueOrDefault(p.Key) ?? p.Key) + "：" + (p.Value is JsonObject or JsonArray ? p.Value.ToJsonString(new System.Text.Json.JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }) : p.Value?.ToString())));
        return node.ToString();
    }
    private static readonly Dictionary<string, string> DetailLabels = new() { ["title"] = "题目 / 标题", ["course"] = "课程", ["chapter"] = "章节", ["reason"] = "原因", ["status"] = "状态", ["stage"] = "处理阶段", ["stageText"] = "处理阶段", ["content"] = "正文", ["answer"] = "答案", ["options"] = "选项", ["candidates"] = "候选来源", ["task_status"] = "任务点状态", ["submit_status"] = "提交状态", ["evidence"] = "平台证据", ["next"] = "下一步" };

    private async Task ShowProfiles()
    {
        var items = J.A(await Call("listAiProfiles")).ToList();
        var name = new TextBox { Text = "默认档案", PlaceholderText = "档案名称", MaxLength = 40 };
        var select = new ComboBox { ItemsSource = items.Select(n => new ChoiceItem(J.S(n, "name"), J.S(n, "id"))).ToArray(), DisplayMemberPath = "Label", SelectedIndex = items.Count > 0 ? 0 : -1 };
        var notice = new InfoBar { IsOpen = true, IsClosable = false, Message = "档案包含当前服务配置与凭据，在本机加密保存。" };
        async Task Reload() { items = J.A(await Call("listAiProfiles")).ToList(); select.ItemsSource = items.Select(n => new ChoiceItem(J.S(n, "name"), J.S(n, "id"))).ToArray(); select.SelectedIndex = items.Count > 0 ? 0 : -1; }
        var dialog = Dialog("命名 AI 档案（本机加密）", Column(notice, name, select));
        dialog.PrimaryButtonText = "保存当前设置"; dialog.SecondaryButtonText = "切换所选档案";
        dialog.PrimaryButtonClick += async (_, args) => { args.Cancel = true; var deferral = args.GetDeferral(); try { bool ok = (await Call("aiProfileAction", "save", name.Text))?.ToString() == "true"; notice.Message = ok ? "档案已保存。" : J.S(Summary, "message", "保存失败，请检查名称和设置。"); await Reload(); } finally { deferral.Complete(); } };
        dialog.SecondaryButtonClick += async (_, args) => { args.Cancel = true; if (select.SelectedItem is ChoiceItem item) { await Call("aiProfileAction", "load", item.Value); notice.Message = "请检查当前加载的配置。"; } };
        var delete = Button("删除所选档案…", async () =>
        {
            if (select.SelectedItem is not ChoiceItem item) return;
            // Hide the parent before showing another ContentDialog.
            dialog.Hide(); currentDialog = null;
            if (await Confirm("删除 AI 档案？", "将删除「" + item.Label + "」的本机加密档案，无法撤销。", "删除此档案")) await Call("aiProfileAction", "delete", item.Value);
        });
        ((StackPanel)dialog.Content).Children.Add(delete);
        currentDialog = dialog; try { await dialog.ShowAsync(); } finally { currentDialog = null; }
    }

    private async Task ShowHistory()
    {
        var rows = J.A(await Call("loadRunHistory")).ToList();
        var panel = Column(Text(J.S(backend.State, "historyError"), 12, true));
        foreach (var row in rows)
        {
            var id = J.S(row, "id");
            panel.Children.Add(Card(Column(Text(J.S(row, "started_at", J.S(row, "time", id))), Text(DetailText(row), 12, true), Button("从摘要恢复选课", async () => { currentDialog?.Hide(); await Call("prepareRecovery", id); }, true))));
        }
        if (rows.Count == 0) panel.Children.Add(Text("暂无运行摘要。任务运行后将在本机保存。"));
        var dialog = Dialog("运行摘要", Scroll(panel)); currentDialog = dialog; try { await dialog.ShowAsync(); } finally { currentDialog = null; }
    }
    private async Task ShowSavedDrafts()
    {
        var rows = J.A(await Call("listDraftCopies")).ToList(); var panel = Column();
        foreach (var row in rows)
        {
            var id = J.S(row, "id");
            panel.Children.Add(Card(Column(Text(J.S(row, "title", id)), Row(Button("打开副本", async () => { currentDialog?.Hide(); await Call("openSavedDraft", id); }),
                Button("删除副本…", async () => { currentDialog?.Hide(); currentDialog = null; if (await Confirm("删除人工副本？", "只删除独立的本机副本，无法撤销。", "删除此副本")) await Call("deleteSavedDraft", id, J.I(row, "version")); })))));
        }
        if (rows.Count == 0) panel.Children.Add(Text("暂无人工副本。请从讨论或活动详情打开「编辑本地副本」。"));
        var dialog = Dialog("已保存人工副本", Scroll(panel)); currentDialog = dialog; try { await dialog.ShowAsync(); } finally { currentDialog = null; }
    }
}
