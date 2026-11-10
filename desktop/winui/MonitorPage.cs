using System.Collections.ObjectModel;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private sealed class ResultFilter
    {
        public TextBox Search = null!;
        public CheckBox? Pending;
        public ComboBox? Stages;
        public TextBlock? Scope;
        public Button? ClearScope;
        public TextBlock? Stats;
    }
    private sealed record FilterOption(string Label, string Value);

    private readonly Dictionary<string, TextBlock> chapterStatValues = new();
    private readonly Dictionary<string, ResultFilter> resultFilters = new();
    private readonly ObservableCollection<LogEntry> logEntries = new();
    private TextBlock? aiUsageText, aiWarningText, verificationText;
    private ListView? logList;
    private TextBlock? logEmpty;
    private CheckBox? logFollow;
    private ScrollViewer? logScroller;
    private long logScrollTicks;
    private double lastLogOffset;
    private bool followLogs = true;
    private bool resettingFilters;
    private Pivot? resultTabs;
    private int questionsTabIndex = 1;
    private string questionTask = "";
    private bool syncingQuestionSearch;
    private ContentDialog? recordDialog;
    // Bumped on every scope reset. A detail RPC that was in flight when the
    // reset arrived belongs to the discarded scope and must not open after it.
    private int resetGeneration;

    private FrameworkElement BuildMonitor()
    {
        var grid = new Grid { RowSpacing = 12 };
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var statGrid = new Grid { ColumnSpacing = 14 };
        string[] labels = ["本轮章节", "已核验章节", "待完成 / 未核验章节", "失败章节"];
        string[] keys = ["total", "success", "pending", "failed"];
        string[] colors = ["AccentBrush", "SuccessBrush", "WarningBrush", "ErrorBrush"];
        for (int i = 0; i < keys.Length; i++)
        {
            var key = keys[i];
            var value = Text("0", 26);
            var label = Text(labels[i], 12);
            value.Foreground = label.Foreground = ThemeBrush(colors[i]);
            chapterStatValues[key] = value;
            var card = Card(Column(value, label));
            statGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            statGrid.Children.Add(card);
            Grid.SetColumn(card, i);
            refreshers.Add(() => value.Text = J.S(Summary, key, "0"));
        }
        // The backend already tracks these numbers; the native page used to
        // drop them, so show exactly what the QML page showed.
        aiUsageText = Text("", 12, true);
        aiUsageText.Name = "AiUsageText";
        aiWarningText = Text("", 12);
        aiWarningText.Name = "AiWarningText";
        aiWarningText.Foreground = ThemeBrush("WarningBrush");
        aiWarningText.Visibility = Visibility.Collapsed;
        verificationText = Text("", 12, true);
        verificationText.Name = "VerificationText";
        verificationText.Visibility = Visibility.Collapsed;
        refreshers.Add(() =>
        {
            var usage = Summary?["aiUsage"];
            int limit = J.I(usage, "limit");
            aiUsageText.Text = "本次操作 AI 请求 " + J.I(usage, "requests") + " / " + (limit > 0 ? limit.ToString() : "不限") +
                " · 服务返回 token " + J.I(usage, "tokens") + "（" + J.I(usage, "known") + " 次有 usage，" +
                J.I(usage, "unknown") + " 次用量未知）· 不等于账单或正确率";
            string warning = J.S(Summary, "aiWarning");
            aiWarningText.Text = warning;
            aiWarningText.Visibility = warning.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            var progress = Summary?["verificationProgress"];
            string phase = J.S(progress, "phase");
            verificationText.Text = phase.Length == 0 ? "" :
                "核验已处理 " + J.I(progress, "completed") + "/" + J.I(progress, "total") + " 章 · 章节／卡片读取 " +
                J.I(progress, "reads") + " · 失败 " + J.I(progress, "failures") + " · " +
                (int)Math.Round(J.I(progress, "elapsed_ms") / 1000.0) + " 秒";
            verificationText.Visibility = verificationText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        });
        var header = Column(statGrid,
            Text("按章节统计，同一章节可含多个任务点；处理进度 100% 表示本轮结束，待完成 / 未核验章节请在平台确认。", 12, true),
            aiUsageText, aiWarningText, verificationText);
        var taskList = ResultList("tasks", "TaskTemplate");
        var progress = new ProgressBar { Height = 6 };
        refreshers.Add(() => progress.Value = J.I(Summary, "progress"));
        var tasks = new Grid { RowSpacing = 12 };
        tasks.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tasks.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tasks.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tasks.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
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
        var resume = Button("继续上次任务", ResumeLastRunAsync);
        resume.Name = "ResumeButton";
        AutomationProperties.SetName(resume, "继续上次任务");
        var resumeHint = Text("", 12, true);
        resumeHint.Name = "ResumeHint";
        var resumeCard = new Border
        {
            Background = ThemeBrush("BannerBrush"),
            BorderBrush = ThemeBrush("BorderBrush"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(14),
            Child = Column(Text("继续上次任务", 15), resumeHint, resume),
        };
        resumeCard.Name = "ResumeCard";
        refreshers.Add(() =>
        {
            var state = Summary?["resume"];
            bool available = J.B(Summary, "resumeScoped") && J.B(state, "available");
            resumeCard.Visibility = available ? Visibility.Visible : Visibility.Collapsed;
            resume.Visibility = resumeCard.Visibility;
            // Clickable whenever a continuation can be requested: a pending
            // write confirmation must stay reachable, otherwise that gate can
            // never open. Only busy/invalid states disable the control.
            resume.IsEnabled = available && J.B(state, "enabled");
            string scope = J.S(state, "scope");
            resumeHint.Text = available
                ? (scope.Length > 0 ? "原范围：" + scope + "\n" : "") + J.S(state, "reason")
                : "";
        });
        var tools = new Grid { ColumnSpacing = 24, RowSpacing = 10, ChildrenTransitions = LayoutTransitions() };
        tools.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        tools.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        tools.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tools.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tools.Children.Add(verification);
        tools.Children.Add(records);
        void ArrangeTools()
        {
            bool narrow = tools.ActualWidth < 680;
            Grid.SetColumnSpan(verification, narrow ? 2 : 1);
            Grid.SetRow(records, narrow ? 1 : 0);
            Grid.SetColumn(records, narrow ? 0 : 1);
            Grid.SetColumnSpan(records, narrow ? 2 : 1);
            records.HorizontalAlignment = narrow ? HorizontalAlignment.Left : HorizontalAlignment.Right;
        }
        tools.SizeChanged += (_, _) => ArrangeTools();
        ArrangeTools();
        var resumeRow = new Grid();
        resumeRow.Children.Add(resumeCard);
        tasks.Children.Add(tools);
        tasks.Children.Add(resumeRow);
        tasks.Children.Add(progress);
        Grid.SetRow(resumeRow, 1);
        Grid.SetRow(progress, 2);
        tasks.Children.Add(taskList);
        Grid.SetRow(taskList, 3);
        var tabs = Tabs(("章节任务", tasks), ("逐题结果", FilteredResults("questions")),
            ("讨论记录", FilteredResults("discussions")), ("投票／问卷", FilteredResults("activities")),
            ("需处理", FilteredResults("attention")));
        if (tabs.Children.Count > 0 && tabs.Children[0] is Pivot pivot) resultTabs = pivot;
        var logSearch = new TextBox { PlaceholderText = "搜索当前保留日志", Width = 250 };
        AutomationProperties.SetName(logSearch, "搜索当前保留日志");
        var level = new ComboBox { ItemsSource = new[] { "全部级别", "警告及错误", "仅错误" }, SelectedIndex = 0, Width = 145 };
        AutomationProperties.SetName(level, "日志级别");
        async Task Filter() => await Call("filterLogs", level.SelectedIndex == 1 ? "WARNING" : level.SelectedIndex == 2 ? "ERROR" : "", logSearch.Text);
        logSearch.TextChanged += async (_, _) => { if (!refreshing) await Filter(); };
        level.SelectionChanged += async (_, _) => { if (!refreshing) await Filter(); };
        logFollow = new CheckBox { Content = "跟随最新", IsChecked = followLogs };
        logFollow.Name = "LogFollow";
        AutomationProperties.SetName(logFollow, "跟随最新");
        logFollow.Click += (_, _) => SetFollowLogs(logFollow.IsChecked == true);
        var bottom = Button("到底部", () => { SetFollowLogs(true); ScrollLogsToEnd(); return Task.CompletedTask; });
        bottom.Name = "LogBottom";
        AutomationProperties.SetName(bottom, "到底部");
        logList = new ListView
        {
            ItemTemplate = (DataTemplate)Root.Resources["LogTemplate"],
            SelectionMode = ListViewSelectionMode.None,
            ItemsSource = logEntries,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
        };
        logList.Name = "LogList";
        logList.Loaded += (_, _) =>
        {
            logScroller ??= VisualChild<ScrollViewer>(logList);
            if (logScroller != null) logScroller.ViewChanged += LogScrolled;
        };
        logEmpty = Text("暂无日志。离线演示和真实任务的记录将在这里显示。", 12, true);
        logEmpty.Name = "LogEmpty";
        logEmpty.Margin = new Thickness(4);
        var logArea = new Grid { Height = 200 };
        logArea.Children.Add(logList);
        logArea.Children.Add(logEmpty);
        var logPanel = new Expander
        {
            Header = "运行日志",
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Content = Column(Row(logSearch, level, logFollow, bottom), logArea,
                Row(Button("清空显示", async () => await Call("clearLogs")),
                    Button("删除日志记录…", async () =>
                    {
                        if (await Confirm("删除日志记录？", "将删除当前日志目录内的应用日志文件并清空显示。", "删除日志记录"))
                            await Call("deleteLogs");
                    }),
                    Button("导出保留日志", () => FileAction("fileAction", ["export_logs"], true, "log", "chaoxing-desktop")),
                    Button("容量…", async () => await ShowText("显示与写盘容量", J.S(backend.State, "capacityInfo"))))),
        };
        grid.Children.Add(header);
        grid.Children.Add(tabs);
        Grid.SetRow(tabs, 1);
        grid.Children.Add(logPanel);
        Grid.SetRow(logPanel, 2);
        return grid;
    }

    private ListView ResultList(string name, string template = "RecordTemplate")
    {
        var list = new ListView
        {
            ItemTemplate = (DataTemplate)Root.Resources[template],
            SelectionMode = ListViewSelectionMode.None,
            IsItemClickEnabled = true,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            ItemContainerStyle = StretchListStyle(),
        };
        // Capture the scope token on the click itself: the dialog queue may run
        // this much later, and a reset in between must not open the old row.
        list.ItemClick += (_, args) => { if (args.ClickedItem is RecordRow row) { int token = resetGeneration; EnqueueDialog(() => ShowRecord(name, row.Data, token)); } };
        resultLists.Add(name, list);
        return list;
    }

    private UIElement FilteredResults(string name)
    {
        var list = ResultList(name);
        var filter = new ResultFilter();
        var search = new TextBox
        {
            PlaceholderText = name switch
            {
                "questions" => "按章节任务 ID 筛选",
                "discussions" => "搜索讨论主题、课程或原因",
                "activities" => "搜索活动、课程、章节或处理原因",
                _ => "搜索标题、课程或原因",
            },
            Width = 280,
        };
        AutomationProperties.SetName(search, name switch
        {
            "questions" => "筛选章节任务 ID",
            "discussions" => "搜索讨论记录",
            "activities" => "搜索投票问卷记录",
            _ => "筛选待处理事项",
        });
        filter.Search = search;
        UIElement tools;
        if (name == "questions")
        {
            var pending = new CheckBox { Content = "只看待处理 / 拒绝 / 失败" };
            pending.Name = "QuestionPending";
            AutomationProperties.SetName(pending, "只看待处理 / 拒绝 / 失败");
            var scope = Text("全部章节", 12, true);
            scope.Name = "QuestionScope";
            var clear = Button("清除章节筛选", () => { SetQuestionScope(""); return Task.CompletedTask; });
            clear.Name = "QuestionScopeClear";
            AutomationProperties.SetName(clear, "清除章节筛选");
            clear.Visibility = Visibility.Collapsed;
            filter.Pending = pending;
            filter.Scope = scope;
            filter.ClearScope = clear;
            pending.Click += async (_, _) => { if (!refreshing && !resettingFilters) await ApplyQuestionFilter(); };
            // Typing a chapter task id is the same scope as the list button.
            search.TextChanged += async (_, _) =>
            {
                if (refreshing || resettingFilters || syncingQuestionSearch) return;
                questionTask = search.Text.Trim();
                UpdateQuestionScope();
                await ApplyQuestionFilter();
            };
            tools = Row(search, pending, scope, clear);
        }
        else
        {
            (string Label, string Value)[] options = name == "attention"
                ? [("全部需处理", ""), ("平台待确认", "platform"), ("提交未知", "unknown"), ("失败／跳过", "failed"), ("草稿／人工", "manual")]
                : name == "discussions"
                    ? [("全部记录", ""), ("本地草稿", "draft"), ("已发布", "published"), ("本人已回复", "existing"), ("发布未知", "publish_unknown"), ("待处理", "pending")]
                    : [("全部记录", ""), ("本地草稿", "draft"), ("已确认提交", "submitted"), ("本人已参与", "existing"), ("提交未知", "submit_unknown"), ("待处理", "pending"), ("平台拒绝", "rejected")];
            var stages = new ComboBox
            {
                // The label is what the user reads; the value stays the exact
                // internal key the backend filter expects.
                ItemsSource = options.Select(option => new FilterOption(option.Label, option.Value)).ToArray(),
                DisplayMemberPath = "Label",
                SelectedIndex = 0,
                Width = 160,
            };
            stages.Name = name + "Stages";
            AutomationProperties.SetName(stages, name == "attention" ? "待处理原因类别" : name == "discussions" ? "讨论发布状态" : "活动处理状态");
            filter.Stages = stages;
            stages.SelectionChanged += async (_, _) => { if (!refreshing && !resettingFilters) await Filter(); };
            tools = Row(search, stages);
        }
        async Task Filter()
        {
            if (name == "questions") { await ApplyQuestionFilter(); return; }
            await Call(name == "attention" ? "filterAttention" : name == "discussions" ? "filterDiscussions" : "filterActivities",
                search.Text, (filter.Stages?.SelectedItem as FilterOption)?.Value ?? "");
        }
        if (name != "questions") search.TextChanged += async (_, _) => { if (!refreshing && !resettingFilters) await Filter(); };
        var stats = Text("", 12, true);
        stats.Name = name + "Stats";
        filter.Stats = stats;
        refreshers.Add(() => stats.Text = ResultStatsText(name));
        var grid = new Grid { RowSpacing = 8 };
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(tools);
        grid.Children.Add(stats);
        Grid.SetRow(stats, 1);
        grid.Children.Add(list);
        Grid.SetRow(list, 2);
        resultFilters[name] = filter;
        return grid;
    }

    private string ResultStatsText(string name)
    {
        var summary = Summary;
        if (name == "questions")
        {
            var stats = summary?["questionStats"];
            string text = "最近 " + J.I(summary, "questionCount") + " 条 · 题库轮次 " + J.I(stats, "query_attempts") +
                " · 题库请求 " + J.I(stats, "requests") + " · 题库候选 " + J.I(stats, "candidates") +
                " · 已采用 " + J.I(stats, "resolved") + " · 缓存 " + J.I(stats, "cache") +
                " / AI " + J.I(stats, "ai") + " / 人工 " + J.I(stats, "manual") + " / 随机 " + J.I(stats, "random");
            int evicted = J.I(summary, "questionEvicted");
            return text + (evicted > 0 ? " · 已淘汰 " + evicted + " 条旧记录" : "") + "。统计不代表答案正确率。";
        }
        if (name == "discussions")
        {
            var stats = summary?["discussionStats"];
            string text = "记录 " + J.I(summary, "discussionCount") + " · 草稿 " + J.I(stats, "draft") +
                " · 本人已有回复 " + J.I(stats, "existing") + " · 本轮发布确认 " + J.I(stats, "published") +
                " · 发布未知 " + J.I(stats, "publish_unknown") + " · 任务点确认 " + J.I(summary, "discussionConfirmed") +
                " · 待处理 " + J.I(stats, "pending") + "。发布与任务完成分开；成绩未知。";
            int evicted = J.I(summary, "discussionEvicted");
            return text + (evicted > 0 ? " 已淘汰 " + evicted + " 条旧显示记录。" : "");
        }
        if (name == "activities")
        {
            var stats = summary?["activityStats"];
            var sources = summary?["activitySources"];
            string text = "记录 " + J.I(summary, "activityCount") + " · 草稿 " + J.I(stats, "draft") +
                " · 已提交 " + J.I(stats, "submitted") + " · 已参与 " + J.I(stats, "existing") +
                " · 提交未知 " + J.I(stats, "submit_unknown") + " · 待处理 " + J.I(stats, "pending") +
                " · 任务点确认 " + J.I(summary, "activityConfirmed") +
                "\n已解决答案来源：AI " + J.I(sources, "ai") + " · 随机 " + J.I(sources, "random") +
                " · 固定好 " + J.I(sources, "fixed") + "。答案生成、提交与任务完成分别记录。";
            int evicted = J.I(summary, "activityEvicted");
            return text + (evicted > 0 ? " 已淘汰 " + evicted + " 条旧显示记录。" : "");
        }
        int attentionEvicted = J.I(summary, "attentionEvicted");
        return "汇总本轮待处理原因（最多 2000 项 / 8 MiB" +
            (attentionEvicted > 0 ? "，已淘汰 " + attentionEvicted + " 项显示记录" : "") +
            "）。可到各结果标签检查其保留记录；未决意图保持。";
    }

    private async Task ApplyQuestionFilter()
    {
        if (!resultFilters.TryGetValue("questions", out var filter)) return;
        await Call("filterQuestions", filter.Pending?.IsChecked == true, questionTask);
    }
    /// <summary>Set the chapter scope from either the list button or the search box.</summary>
    private void SetQuestionScope(string task)
    {
        questionTask = task;
        UpdateQuestionScope();
        // Typing and the chapter jump share one value: keep the box in step so
        // the scope text, the clear button and the sent filter never disagree.
        if (resultFilters.TryGetValue("questions", out var filter) && filter.Search.Text != task)
        {
            syncingQuestionSearch = true;
            try { filter.Search.Text = task; }
            finally { syncingQuestionSearch = false; }
        }
        if (!closing) _ = ApplyQuestionFilter();
    }
    private void UpdateQuestionScope()
    {
        if (!resultFilters.TryGetValue("questions", out var filter)) return;
        if (filter.Scope != null)
        {
            filter.Scope.Text = questionTask.Length > 0 ? "已筛选章节" : "全部章节";
            ToolTipService.SetToolTip(filter.Scope, questionTask);
        }
        if (filter.ClearScope != null) filter.ClearScope.Visibility = questionTask.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    }
    private void TaskQuestionsClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not RecordRow row) return;
        SetQuestionScope(J.S(row.Data, "id"));
        if (resultTabs != null) resultTabs.SelectedIndex = questionsTabIndex;
    }

    private void ResetResultFilters()
    {
        // A scope switch clears the retained result filters only. A manual
        // answer or a start confirmation owns its own queued dialog and is
        // never dismissed here; only a stale result detail is closed.
        resetGeneration++;
        resettingFilters = true;
        try
        {
            foreach (var filter in resultFilters.Values)
            {
                if (filter.Search.Text.Length > 0) filter.Search.Text = "";
                if (filter.Pending != null) filter.Pending.IsChecked = false;
                if (filter.Stages != null && filter.Stages.SelectedIndex != 0) filter.Stages.SelectedIndex = 0;
            }
            questionTask = "";
            UpdateQuestionScope();
        }
        finally { resettingFilters = false; }
        recordDialog?.Hide();
        if (closing) return;
        // Re-apply the cleared filters so the controls and the lists agree;
        // the backend already dropped its rows, so only empty values are sent.
        if (resultFilters.ContainsKey("questions")) _ = Call("filterQuestions", false, "");
        if (resultFilters.ContainsKey("discussions")) _ = Call("filterDiscussions", "", "");
        if (resultFilters.ContainsKey("activities")) _ = Call("filterActivities", "", "");
        if (resultFilters.ContainsKey("attention")) _ = Call("filterAttention", "", "");
    }

    private void SyncLogs()
    {
        if (logList == null) return;
        var lines = J.A(backend.State["logs"]).Select(n => n.ToString()).ToArray();
        bool same = lines.Length == logEntries.Count;
        for (int i = 0; same && i < lines.Length; i++) same = logEntries[i].Text == lines[i];
        if (same) return;
        // A plain append keeps the reader's scroll position; a filter change
        // rewrites the retained tail and must rebuild the list.
        bool prefix = lines.Length >= logEntries.Count;
        for (int i = 0; prefix && i < logEntries.Count; i++) prefix = logEntries[i].Text == lines[i];
        if (!prefix)
        {
            // A capacity window rolls rather than rewrites: the shown tail is
            // the head of the new snapshot. Drop only the evicted head lines and
            // append the new tail, so retained rows and the reader's place stay.
            int overlap = Overlap(lines);
            if (overlap > 0)
            {
                // The bound is fixed up front: Count shrinks on every removal.
                for (int drop = logEntries.Count - overlap; drop > 0; drop--) logEntries.RemoveAt(0);
                for (int i = overlap; i < lines.Length; i++) logEntries.Add(new LogEntry(lines[i], LogBrush(lines[i])));
                if (logEmpty != null) logEmpty.Visibility = logEntries.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
                if (followLogs) DispatcherQueue.TryEnqueue(ScrollLogsToEnd);
                return;
            }
            // A real filter change rewrites the retained tail; rebuild it.
            logEntries.Clear();
        }
        for (int i = logEntries.Count; i < lines.Length; i++) logEntries.Add(new LogEntry(lines[i], LogBrush(lines[i])));
        if (logEmpty != null) logEmpty.Visibility = logEntries.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (followLogs) DispatcherQueue.TryEnqueue(ScrollLogsToEnd);
    }
    /// <summary>
    /// Length of the longest suffix of the shown lines that is also a prefix of
    /// the incoming snapshot: the part a rolling capacity window kept.
    /// </summary>
    private int Overlap(string[] lines)
    {
        for (int k = Math.Min(logEntries.Count, lines.Length); k > 0; k--)
        {
            bool match = true;
            for (int i = 0; i < k && match; i++)
                match = logEntries[logEntries.Count - k + i].Text == lines[i];
            if (match) return k;
        }
        return 0;
    }
    private SolidColorBrush LogBrush(string line) =>
        line.Contains("ERROR", StringComparison.Ordinal) ? ThemeBrush("ErrorBrush") :
        line.Contains("WARNING", StringComparison.Ordinal) ? ThemeBrush("WarningBrush") : ThemeBrush("InkBrush");
    private void SetFollowLogs(bool value)
    {
        followLogs = value;
        if (logFollow != null && logFollow.IsChecked != value) logFollow.IsChecked = value;
        if (value) ScrollLogsToEnd();
    }
    private void ScrollLogsToEnd()
    {
        if (logList == null) return;
        logScroller ??= VisualChild<ScrollViewer>(logList);
        if (logScroller == null) return;
        logScrollTicks = Environment.TickCount64;
        logList.UpdateLayout();
        lastLogOffset = logScroller.ScrollableHeight;
        logScroller.ChangeView(null, logScroller.ScrollableHeight, null, true);
        // Virtualised rows report their real extent one layout pass later, so a
        // single ChangeView stops short of the true end. Follow once more.
        DispatcherQueue.TryEnqueue(() =>
        {
            if (!followLogs || logList == null || logScroller == null) return;
            logScrollTicks = Environment.TickCount64;
            logList.UpdateLayout();
            lastLogOffset = logScroller.ScrollableHeight;
            logScroller.ChangeView(null, logScroller.ScrollableHeight, null, true);
        });
    }
    private void LogScrolled(object? sender, ScrollViewerViewChangedEventArgs e)
    {
        if (sender is not ScrollViewer scroller) return;
        double offset = scroller.VerticalOffset;
        // Reading upward must not be yanked back down by the next log line. A
        // real upward move wins even inside the auto-scroll window: a stream of
        // updates would otherwise keep resetting the window and swallow it.
        if (offset < lastLogOffset - 24)
        {
            if (followLogs) SetFollowLogs(false);
            lastLogOffset = offset;
            return;
        }
        if (Environment.TickCount64 - logScrollTicks < 300) { lastLogOffset = offset; return; }
        lastLogOffset = offset;
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
    internal async Task ShowRecord(string kind, JsonNode row, int generation = -1)
    {
        // generation is the scope token captured when the row was clicked (or
        // now, for an in-place caller). A reset since then discards this row.
        if (generation < 0) generation = resetGeneration;
        if (generation != resetGeneration) return;
        if (kind is "tasks" or "attention")
        {
            var chapter = Dialog(J.S(row, "title"), ReadOnlyBody(ChapterDetailText(row)));
            recordDialog = chapter;
            try { await ShowModal(chapter, stillWanted: () => generation == resetGeneration); }
            finally { if (ReferenceEquals(recordDialog, chapter)) recordDialog = null; }
            return;
        }
        var method = kind == "questions" ? "questionDetail" : kind == "discussions" ? "discussionDetail" : "activityDetail";
        var record = await Call(method, J.S(row, "id")) ?? row;
        // The scope was reset while this detail was being read: the row is gone
        // from the discarded scope, so opening it now would show stale data.
        if (generation != resetGeneration) return;
        string text;
        if (kind == "questions") text = QuestionDetailText(record);
        else
        {
            text = J.S(record, "detailText");
            if (text.Length == 0) text = kind == "discussions" ? DiscussionDetailText(record) : DetailText(record);
        }
        var dialog = Dialog(J.S(row, "title", "记录详情"), ReadOnlyBody(text));
        if (kind == "questions")
        {
            dialog.SecondaryButtonText = "复制详情";
            dialog.SecondaryButtonClick += (_, args) =>
            {
                args.Cancel = true;
                var package = new DataPackage();
                package.SetText(text);
                Clipboard.SetContent(package);
            };
        }
        else
        {
            dialog.PrimaryButtonText = "编辑本地副本";
            dialog.SecondaryButtonText = "复制正文";
            dialog.SecondaryButtonClick += async (_, args) =>
            {
                args.Cancel = true;
                await Call(kind == "discussions" ? "copyDiscussion" : "copyActivity", J.S(row, "id"));
            };
        }
        recordDialog = dialog;
        try
        {
            // Also covers the case where the dialog had to queue behind another
            // modal and the reset arrived during that wait.
            if (await ShowModal(dialog, stillWanted: () => generation == resetGeneration) == ContentDialogResult.Primary)
                await Call("openDraftCopy", kind == "discussions" ? "discussion" : "activity", J.S(row, "id"));
        }
        finally { if (ReferenceEquals(recordDialog, dialog)) recordDialog = null; }
    }

    private static string QuestionDetailText(JsonNode record)
    {
        if (record is not JsonObject detail || detail.Count == 0)
            return "该记录已被清除或淘汰。\n\n该条逐题记录已被容量上限淘汰，无法再读取详情。可重新运行该章节获得新的记录，或到「需处理」查看本轮未决原因。";
        string title = J.S(detail, "title");
        string answer = J.S(detail, "answer");
        string source = J.S(detail, "sourceText");
        if (source.Length == 0) source = J.S(detail, "source");
        return (title.Length > 0 ? title : "该记录已被清除或淘汰") +
            "\n\n选项：\n" + J.S(detail, "options") +
            "\n\n采用答案：\n" + (answer.Length > 0 ? answer : "未采用") +
            "\n原因：" + J.S(detail, "reason") +
            "\n来源：" + (source.Length > 0 ? source : "待处理") +
            "\n匹配：" + MatchText(J.S(detail, "match_mode")) +
            "\n耗时：题库 " + J.I(detail, "elapsed_ms") + " ms；AI " + J.I(detail, "ai_elapsed_ms") +
            " ms；人工 " + J.I(detail, "manual_elapsed_ms") + " ms" +
            "\nAI 请求 " + J.I(detail, "ai_requests") + " 次，重试 " + J.I(detail, "ai_retries") + " 次" +
            "\n\n服务记录：\n" + QuestionAttempts(detail);
    }
    private static string MatchText(string mode) => mode switch
    {
        "exact" => "原文匹配",
        "format" => "格式等价",
        "fuzzy" => "相似选项",
        "memory" => "本轮缓存",
        "letter" => "选项编号",
        _ => "答案格式核验",
    };
    private static string QuestionAttempts(JsonNode record)
    {
        var parts = new List<string>();
        foreach (var attempt in J.A(record["attempts"]))
        {
            var lines = new List<string> { J.S(attempt, "provider") + " · " + J.I(attempt, "elapsed_ms") + " ms" };
            string reason = J.S(attempt, "reason");
            if (reason.Length > 0) lines.Add(reason);
            string returned = J.S(attempt, "returned_title");
            if (returned.Length > 0) lines.Add("返回题干：" + returned);
            string candidate = J.S(attempt, "candidate");
            if (candidate.Length > 0) lines.Add("候选答案：" + candidate);
            parts.Add(string.Join("\n", lines));
        }
        return parts.Count > 0 ? string.Join("\n\n", parts) : "暂无服务尝试记录。";
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
        // Platform identifiers stay available for support, with readable labels.
        Add("课程编号", J.S(row, "courseId"));
        Add("班级编号", J.S(row, "clazzId"));
        Add("章节编号", J.S(row, "pointId"));
        return string.Join("\n\n", lines);
    }
    private static string DiscussionDetailText(JsonNode row)
    {
        // Older/partial backend responses must never fall back to raw identifiers.
        (string Key, string Label)[] fields =
        [
            ("course", "课程"), ("chapter", "章节"), ("title", "讨论主题"),
            ("stageText", "处理阶段"), ("taskText", "任务点状态"), ("levelText", "回复方式"),
            ("sourceText", "内容来源"), ("reason", "处理说明"), ("content", "回复正文"),
        ];
        return string.Join("\n\n", fields.Where(field => J.S(row, field.Key).Length > 0)
            .Select(field => field.Label + "：" + J.S(row, field.Key)));
    }
    private static string DetailText(JsonNode node)
    {
        if (node is JsonObject obj)
            return string.Join("\n\n", obj.Select(pair => (DetailLabels.GetValueOrDefault(pair.Key) ?? pair.Key) + "：" +
                (pair.Value is JsonObject or JsonArray
                    ? pair.Value.ToJsonString(new System.Text.Json.JsonSerializerOptions
                    {
                        WriteIndented = true,
                        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
                    })
                    : pair.Value?.ToString())));
        return node.ToString();
    }
    private static readonly Dictionary<string, string> DetailLabels = new()
    {
        ["title"] = "题目 / 标题", ["course"] = "课程", ["chapter"] = "章节", ["reason"] = "原因",
        ["status"] = "状态", ["stage"] = "处理阶段", ["stageText"] = "处理阶段", ["content"] = "正文",
        ["answer"] = "答案", ["options"] = "选项", ["candidates"] = "候选来源",
        ["task_status"] = "任务点状态", ["submit_status"] = "提交状态", ["evidence"] = "平台证据", ["next"] = "下一步",
    };

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
        await ShowModal(dialog);
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
        var dialog = Dialog("运行摘要", Scroll(panel)); await ShowModal(dialog);
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
        var dialog = Dialog("已保存人工副本", Scroll(panel)); await ShowModal(dialog);
    }
}
