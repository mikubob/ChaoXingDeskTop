using System.Collections.ObjectModel;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
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
    }
    private sealed record FilterOption(string Label, string Value);

    private readonly Dictionary<string, TextBlock> chapterStatValues = new();
    private readonly Dictionary<string, ResultFilter> resultFilters = new();
    private readonly ObservableCollection<LogEntry> logEntries = new();
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
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var statGrid = new Grid { ColumnSpacing = 14 };
        string[] labels = ["本轮章节", "平台已确认完成", "待完成／未核验", "失败章节"];
        string[] keys = ["total", "success", "pending", "failed"];
        string[] colors = ["AccentBrush", "SuccessBrush", "WarningBrush", "ErrorBrush"];
        for (int i = 0; i < keys.Length; i++)
        {
            var key = keys[i];
            var value = Text("0", 26);
            var label = Text(labels[i], 12);
            value.Foreground = label.Foreground = ThemeBrush(colors[i]);
            chapterStatValues[key] = value;
            var stat = Column(value, label);
            stat.Spacing = 2;
            statGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            statGrid.Children.Add(stat);
            Grid.SetColumn(stat, i);
            refreshers.Add(() => value.Text = J.S(Summary, key, "0"));
        }
        var aiWarningText = Text("", 12);
        aiWarningText.Name = "AiWarningText";
        aiWarningText.Foreground = ThemeBrush("WarningBrush");
        aiWarningText.Margin = new Thickness(0, 0, 0, 12);
        aiWarningText.Visibility = Visibility.Collapsed;
        refreshers.Add(() =>
        {
            string warning = J.S(Summary, "aiWarning");
            aiWarningText.Text = warning;
            aiWarningText.Visibility = warning.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        });
        var taskList = ResultList("tasks", "TaskTemplate");
        var taskArea = ResultsArea("tasks", taskList, "暂无章节任务。请在「我的课程」选择课程并开始任务。");
        var progressLabel = Text("本轮处理进度 0%", 12);
        progressLabel.Name = "TaskProgressLabel";
        var progress = new ProgressBar { Height = 6 };
        AutomationProperties.SetName(progress, "本轮处理进度");
        refreshers.Add(() =>
        {
            int percent = J.I(Summary, "progress");
            progress.Value = percent;
            progressLabel.Text = $"本轮处理进度 {percent}%";
        });
        var taskProgress = Column(progressLabel, progress);
        taskProgress.Spacing = 6;
        var tasks = new Grid { RowSpacing = 12 };
        tasks.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        tasks.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var verifyChapters = Button("核验章节", async () => await Call("verifyPending"), true);
        verifyChapters.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        ToolTipService.SetToolTip(verifyChapters, "只读检查待完成章节的平台进度");
        var records = Row(Button("运行摘要", () => { EnqueueDialog(ShowHistory); return Task.CompletedTask; }),
            Button("人工副本", () => { EnqueueDialog(ShowSavedDrafts); return Task.CompletedTask; }));
        var resume = Button("继续上次任务", ResumeLastRunAsync);
        resume.Name = "ResumeButton";
        var resumeHint = Text("", 12, true);
        resumeHint.Name = "ResumeHint";
        var resumeCard = new Border
        {
            Background = ThemeBrush("BannerBrush"),
            BorderBrush = ThemeBrush("BorderBrush"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(14),
            Child = Column(resumeHint, resume),
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
        tasks.Children.Add(Column(Card(Column(statGrid, taskProgress)), StretchRow(verifyChapters, records), resumeCard));
        tasks.Children.Add(taskArea);
        Grid.SetRow(taskArea, 1);
        var tabs = Tabs(("章节任务", tasks), ("逐题结果", FilteredResults("questions")),
            ("讨论记录", FilteredResults("discussions")), ("投票／问卷", FilteredResults("activities")),
            ("需处理", FilteredResults("attention")), ("运行日志", BuildLogs()));
        resultTabs = (Pivot)tabs.Children[0];
        resultTabs.SelectionChanged += (_, _) =>
        {
            if (followLogs && resultTabs.SelectedIndex == resultTabs.Items.Count - 1)
                DispatcherQueue.TryEnqueue(ScrollLogsToEnd);
        };
        grid.Children.Add(aiWarningText);
        grid.Children.Add(tabs);
        Grid.SetRow(tabs, 1);
        return grid;
    }

    private UIElement BuildLogs()
    {
        var logSearch = new TextBox { PlaceholderText = "搜索日志", Width = 250 };
        AutomationProperties.SetName(logSearch, "搜索日志");
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
        logList.SizeChanged += (_, _) =>
        {
            if (followLogs) DispatcherQueue.TryEnqueue(ScrollLogsToEnd);
        };
        logEmpty = Text("暂无可显示的日志。", 12, true);
        logEmpty.Name = "LogEmpty";
        logEmpty.Margin = new Thickness(4);
        var logArea = new Grid();
        logArea.Children.Add(logList);
        logArea.Children.Add(logEmpty);
        var actions = Row(Button("清空显示", async () => await Call("clearLogs")),
            Button("删除日志记录…", async () =>
            {
                if (await Confirm("删除日志记录？", "将删除当前日志目录内的应用日志文件并清空显示。", "删除日志记录"))
                    await Call("deleteLogs");
            }),
            Button("导出保留日志", () => FileAction("fileAction", ["export_logs"], true, "log", "chaoxing-desktop")),
            Button("容量…", async () => await ShowText("显示与写盘容量", J.S(backend.State, "capacityInfo"))));
        var logPanel = new Grid { RowSpacing = 10 };
        logPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        logPanel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        logPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        logPanel.Children.Add(Row(logSearch, level, logFollow, bottom));
        logPanel.Children.Add(logArea);
        logPanel.Children.Add(actions);
        Grid.SetRow(logArea, 1);
        Grid.SetRow(actions, 2);
        return logPanel;
    }

    private ListView ResultList(string name, string template)
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
        var list = ResultList(name, name == "questions" ? "QuestionTemplate" : "RecordTemplate");
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
            var scope = Text("", 12, true);
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
        refreshers.Add(() =>
        {
            stats.Text = ResultStatsText(name);
            stats.Visibility = stats.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        });
        var grid = new Grid { RowSpacing = 8 };
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(tools);
        grid.Children.Add(stats);
        Grid.SetRow(stats, 1);
        var resultArea = ResultsArea(name, list, "当前筛选下没有记录。");
        grid.Children.Add(resultArea);
        Grid.SetRow(resultArea, 2);
        resultFilters[name] = filter;
        return grid;
    }

    private Grid ResultsArea(string name, ListView list, string emptyMessage)
    {
        var empty = Text(emptyMessage, 13, true);
        empty.HorizontalAlignment = HorizontalAlignment.Center;
        empty.VerticalAlignment = VerticalAlignment.Center;
        empty.IsHitTestVisible = false;
        refreshers.Add(() =>
        {
            empty.Visibility = backend.State[name] is JsonArray { Count: > 0 } ? Visibility.Collapsed : Visibility.Visible;
            if (name == "questions")
                empty.Text = J.I(Summary, "questionCount") > 0 ? emptyMessage : "本轮暂无逐题记录。开始处理章节测验后，题目会显示在这里。";
        });
        return new Grid { Children = { list, empty } };
    }

    private string ResultStatsText(string name)
    {
        var summary = Summary;
        if (name == "questions")
        {
            int evicted = J.I(summary, "questionEvicted");
            return evicted > 0 ? $"已淘汰 {evicted} 条旧记录。" : "";
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
            filter.Scope.Text = questionTask.Length > 0 ? "已筛选章节" : "";
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
        if (logList == null || resultTabs == null || resultTabs.SelectedIndex != resultTabs.Items.Count - 1 ||
            logList.ActualHeight <= 0) return;
        if (logScroller == null)
        {
            logScroller = FindLogScroller(logList);
            if (logScroller == null) return;
            logScroller.ViewChanged += LogScrolled;
        }
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
    private static ScrollViewer? FindLogScroller(DependencyObject root)
    {
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is ScrollViewer scroller) return scroller;
            if (FindLogScroller(child) is { } nested) return nested;
        }
        return null;
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
        SyncRows(name, list, J.A(backend.State[name]).Select(n => new RecordRow(n, question: name == "questions")));
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
        string title = J.S(row, "title");
        UIElement detailBody = ReadOnlyBody(text);
        if (kind == "questions")
        {
            var imageUris = J.A(record["images"])
                .Select(node => Uri.TryCreate(node.ToString(), UriKind.Absolute, out var uri) && uri.Scheme == "file" ? uri : null)
                .Where(uri => uri != null)
                .ToArray();
            if (imageUris.Length > 0)
            {
                var panel = Column(Text(text), Text("题目图片（本机缓存）：", 12, true));
                foreach (var uri in imageUris)
                {
                    var image = new Image { Source = new BitmapImage(uri!), MaxHeight = 220, Stretch = Stretch.Uniform };
                    var failed = Text("图片加载失败，请在学习平台查看原题。", 12, true);
                    failed.Visibility = Visibility.Collapsed;
                    image.ImageFailed += (_, _) => failed.Visibility = Visibility.Visible;
                    panel.Children.Add(image);
                    panel.Children.Add(failed);
                }
                detailBody = Scroll(panel);
            }
        }
        var dialog = Dialog(kind == "questions" ? "逐题详情" : string.IsNullOrWhiteSpace(title) ? "记录详情" : title, detailBody);
        if (kind == "questions")
        {
            dialog.SecondaryButtonText = "复制详情";
            dialog.SecondaryButtonClick += (_, args) =>
            {
                args.Cancel = true;
                var package = new DataPackage();
                package.SetText(text);
                Clipboard.SetContent(package);
                dialog.SecondaryButtonText = "已复制";
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
        string answer = RecordRow.QuestionAnswer(detail);
        string source = J.S(detail, "sourceText");
        if (source.Length == 0) source = J.S(detail, "source");
        return (string.IsNullOrWhiteSpace(title) ? "题干无可显示文字" : title) +
            "\n\n选项：\n" + J.S(detail, "options") +
            "\n\n采用答案：\n" + (answer.Length > 0 ? answer : J.S(detail, "stage") == "resolved" ? "已采用" : "未采用") +
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
        var items = J.A(await Call("listAiProfiles"));
        var name = new TextBox { Text = "默认档案", Header = "档案名称（必填，最多 40 字符）", MaxLength = 40 };
        var select = new ComboBox { Header = "已保存的档案", PlaceholderText = "暂无档案", DisplayMemberPath = "Label" };
        var notice = new InfoBar { IsOpen = false, IsClosable = false };
        var panel = Column(Text("档案包含服务配置与凭据，保存在本机并加密。", 12, true), name, select, notice);
        var dialog = Dialog("AI 档案", Scroll(panel));
        dialog.PrimaryButtonText = "保存为档案";
        dialog.SecondaryButtonText = "加载所选档案";
        var delete = Button("删除所选档案…", async () =>
        {
            if (select.SelectedItem is not ChoiceItem item) return;
            dialog.Hide();
            if (await Confirm("删除 AI 档案？", "将删除「" + item.Label + "」的本机加密档案，无法撤销。", "删除此档案"))
                await Call("aiProfileAction", "delete", item.Value);
        });
        panel.Children.Add(delete);
        void Report(bool ok, string message)
        {
            notice.Message = ok ? message : J.S(backend.State["uiFeedback"]?["profiles"], "message", message);
            notice.Severity = ok ? InfoBarSeverity.Success : InfoBarSeverity.Error;
            notice.IsOpen = true;
        }
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            args.Cancel = true;
            var deferral = args.GetDeferral();
            try
            {
                bool ok = (await Call("aiProfileAction", "save", name.Text))?.ToString() == "true";
                Report(ok, ok ? "档案已保存。" : "保存档案失败，请检查名称和 AI 设置。");
                if (ok) LoadChoices(J.A(await Call("listAiProfiles")));
            }
            finally { deferral.Complete(); }
        };
        dialog.SecondaryButtonClick += async (_, args) =>
        {
            args.Cancel = true;
            if (select.SelectedItem is not ChoiceItem item) return;
            var deferral = args.GetDeferral();
            try
            {
                bool ok = (await Call("aiProfileAction", "load", item.Value))?.ToString() == "true";
                Report(ok, ok ? $"已加载「{item.Label}」的 AI 设置。" : $"无法加载「{item.Label}」的 AI 设置。");
            }
            finally { deferral.Complete(); }
        };
        void LoadChoices(IEnumerable<JsonNode> rows)
        {
            var choices = rows.Select(n => new ChoiceItem(J.S(n, "name"), J.S(n, "id"))).ToArray();
            select.ItemsSource = choices;
            select.SelectedIndex = choices.Length > 0 ? 0 : -1;
            dialog.IsSecondaryButtonEnabled = delete.IsEnabled = choices.Length > 0;
        }
        name.TextChanged += (_, _) => dialog.IsPrimaryButtonEnabled = !string.IsNullOrWhiteSpace(name.Text);
        LoadChoices(items);
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
                Button("删除副本…", async () => { currentDialog?.Hide(); currentDialog = null; if (await Confirm("删除人工副本？", "将删除本机副本「" + J.S(row, "title", id) + "」，无法撤销。", "删除此副本")) await Call("deleteSavedDraft", id, J.I(row, "version")); })))));
        }
        if (rows.Count == 0) panel.Children.Add(Text("暂无人工副本。请从讨论或活动详情打开「编辑本地副本」。"));
        var dialog = Dialog("已保存人工副本", Scroll(panel)); await ShowModal(dialog);
    }
}
