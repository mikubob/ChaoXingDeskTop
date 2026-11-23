using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private Grid? examHost;
    private FrameworkElement[] examViews = [];
    private bool showingExamEditor;

    // 考试列表视图控件
    private TextBlock? examAccountLabel, examEmptyText;
    private InfoBar? examListNotice;
    private Expander? examCourseExpander;
    private StackPanel? examCourseChecklistPanel;
    private readonly List<CheckBox> examCourseChecks = [];
    private Button? examRefreshButton;
    private StackPanel? examListContainer;
    private string examCourseSignature = "";

    // 答题视图控件
    private Button? examBackButton, examPrevButton, examNextButton, examSaveButton, examSavePlatformButton, examVerifyButton, examSubmitButton;
    private Button? examAssistAllButton, examAssistOneButton, examAssistCancelButton, examAssistSettingsButton;
    private Button? examExportButton;
    private TextBlock? examEditorTitle, examEditorAccount, examTimerText, examStateText, examProgressText, examAssistStatusText;
    private InfoBar? examEditorNotice;
    private Grid? examBodyGrid, examQuestionNumberGrid;
    private Border? examQuestionNumberCard, examQuestionCard;
    private ScrollViewer? examQuestionNumberScroll, examQuestionDetailScroll;
    private StackPanel? examQuestionDetailPanel, examAssistActions;

    private DispatcherTimer? examCountdownTimer;
    private int examRemainingSeconds = -1;
    private long examCountdownEndsAt;
    private string examCountdownKey = "";
    private string examRenderedQuestionKey = "";
    private string examLastListSignature = "";
    private string examInputSignature = "";
    private ContentDialog? examInputDialog;
    private int examCurrentQuestionIndex;
    private bool examPopulatingQuestion;

    private static readonly string[] ExamEditableStates = ["local_draft", "platform_saved", "rejected"];

    private FrameworkElement BuildExamPage()
    {
        // 所有考试页控件都不加入 idleControls，保证刷课运行时仍可独立操作考试。
        examAccountLabel = Text("当前账号：尚未选择", 13, true);
        examRefreshButton = Button("读取考试", async () =>
        {
            var selectedCourses = SelectedCourseKeys(examCourseChecks);
            if (selectedCourses.Count == 0)
            {
                if (examCourseExpander != null && examCourseChecks.Count > 0) examCourseExpander.IsExpanded = true;
                if (examListNotice != null)
                {
                    examListNotice.Message = examCourseChecks.Count == 0
                        ? "请先在「课程与账号」读取课程。"
                        : "请勾选至少 1 门课程，再点击「读取考试」。";
                    examListNotice.Severity = InfoBarSeverity.Warning;
                    examListNotice.IsOpen = true;
                }
                return;
            }
            await Call("loadExams", string.Join(",", selectedCourses));
        }, color: "#2563EB");
        var headerRow = StretchRow(examAccountLabel, examRefreshButton);
        (examCourseExpander, examCourseChecklistPanel) = BuildCourseChecklistExpander(examCourseChecks, "选择要读取考试的课程");
        var listHeader = Column(headerRow, examCourseExpander);
        listHeader.Spacing = 8;

        examListNotice = new InfoBar { IsOpen = false, IsClosable = false };
        examListContainer = Column();
        examEmptyText = Text("点击右上角「读取考试」获取当前账号的考试安排。", 14, true);
        examEmptyText.HorizontalAlignment = HorizontalAlignment.Center;
        examEmptyText.Margin = new Thickness(0, 32, 0, 0);

        var listBody = new Grid();
        listBody.Children.Add(Scroll(examListContainer));
        listBody.Children.Add(examEmptyText);

        var listView = new Grid { RowSpacing = 12 };
        listView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        listView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        listView.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        listView.Children.Add(listHeader);
        listView.Children.Add(examListNotice);
        listView.Children.Add(listBody);
        Grid.SetRow(examListNotice, 1);
        Grid.SetRow(listBody, 2);

        // 答题视图顶部栏
        examBackButton = Button("← 返回列表", CloseExamViewAsync);
        examEditorTitle = Text("", 17);
        examEditorTitle.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        examEditorAccount = Text("", 12, true);
        examTimerText = Text("", 13);
        examTimerText.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        examStateText = Text("", 12, true);

        var headerLeft = Row(examBackButton, Column(examEditorTitle, examEditorAccount));
        ((StackPanel)headerLeft.Children[1]).Spacing = 2;
        var headerRight = Column(examTimerText, examStateText);
        headerRight.Spacing = 2;
        headerRight.HorizontalAlignment = HorizontalAlignment.Right;
        examTimerText.HorizontalAlignment = HorizontalAlignment.Right;
        examStateText.HorizontalAlignment = HorizontalAlignment.Right;

        var editorTopCard = Card(StretchRow(headerLeft, headerRight));
        editorTopCard.Padding = new Thickness(16, 12, 16, 12);

        examEditorNotice = new InfoBar { IsOpen = false, IsClosable = false };

        // 主体：左侧题号网格 + 右侧当前题卡片
        examQuestionNumberGrid = new Grid { ColumnSpacing = 8, RowSpacing = 8 };
        examProgressText = Text("", 12, true);
        examQuestionNumberScroll = Scroll(Column(Text("题目导航", 14), examProgressText, examQuestionNumberGrid));
        examQuestionNumberCard = Card(examQuestionNumberScroll);
        examQuestionNumberCard.Padding = new Thickness(14);

        examQuestionDetailPanel = Column();
        examQuestionDetailScroll = Scroll(examQuestionDetailPanel);
        examQuestionCard = Card(examQuestionDetailScroll);

        examBodyGrid = new Grid { ColumnSpacing = 14, RowSpacing = 14 };
        examBodyGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(250) });
        examBodyGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        examBodyGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        examBodyGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        examBodyGrid.Children.Add(examQuestionNumberCard);
        examBodyGrid.Children.Add(examQuestionCard);
        Grid.SetColumn(examQuestionCard, 1);

        void ArrangeExamBody()
        {
            if (examBodyGrid == null || examQuestionNumberCard == null || examQuestionCard == null) return;
            bool wide = examBodyGrid.ActualWidth > 860;
            if (wide)
            {
                examBodyGrid.ColumnDefinitions[0].Width = new GridLength(250);
                examBodyGrid.ColumnDefinitions[1].Width = new GridLength(1, GridUnitType.Star);
                examBodyGrid.RowDefinitions[0].Height = new GridLength(1, GridUnitType.Star);
                examBodyGrid.RowDefinitions[1].Height = GridLength.Auto;
                Grid.SetRow(examQuestionNumberCard, 0);
                Grid.SetColumn(examQuestionNumberCard, 0);
                Grid.SetColumnSpan(examQuestionNumberCard, 1);
                Grid.SetRow(examQuestionCard, 0);
                Grid.SetColumn(examQuestionCard, 1);
                Grid.SetColumnSpan(examQuestionCard, 1);
                examQuestionNumberCard.MaxHeight = double.PositiveInfinity;
            }
            else
            {
                examBodyGrid.RowDefinitions[0].Height = GridLength.Auto;
                examBodyGrid.RowDefinitions[1].Height = new GridLength(1, GridUnitType.Star);
                Grid.SetRow(examQuestionNumberCard, 0);
                Grid.SetColumn(examQuestionNumberCard, 0);
                Grid.SetColumnSpan(examQuestionNumberCard, 2);
                Grid.SetRow(examQuestionCard, 1);
                Grid.SetColumn(examQuestionCard, 0);
                Grid.SetColumnSpan(examQuestionCard, 2);
                examQuestionNumberCard.MaxHeight = 200;
            }
        }
        examBodyGrid.SizeChanged += (_, _) => ArrangeExamBody();

        // 底部栏
        examPrevButton = Button("上一题", () => { SelectExamQuestion(examCurrentQuestionIndex - 1); return Task.CompletedTask; });
        examNextButton = Button("下一题", () => { SelectExamQuestion(examCurrentQuestionIndex + 1); return Task.CompletedTask; });
        examAssistAllButton = Button("获取参考答案…", () => RequestExamSuggestionsAsync("all"));
        examAssistOneButton = Button("本题参考答案", () => RequestExamSuggestionsAsync("current"));
        examAssistCancelButton = Button("取消获取", async () => await Call("cancelExamTask"));
        examAssistCancelButton.Foreground = ThemeBrush("WarningBrush");
        examAssistSettingsButton = Button("答题服务设置", () =>
        {
            Navigation.SelectedIndex = 2;
            revealServiceGroup?.Invoke("exam_service");
            return Task.CompletedTask;
        });
        examAssistStatusText = Text("", 12, true);
        examAssistStatusText.VerticalAlignment = VerticalAlignment.Center;

        examSaveButton = Button("保存草稿", async () => await Call("saveExamDraft", false));
        examSavePlatformButton = Button("保存到平台…", async () =>
        {
            var current = backend.State["exam"]?["current"];
            if (await Confirm("保存考试答案到平台", $"将「{J.S(current, "title")}」的当前答案保存到平台，保存后仍可继续答题。", "保存到平台"))
                await Call("saveExamDraft", true);
        });
        examVerifyButton = Button("核验交卷结果", async () =>
        {
            var current = backend.State["exam"]?["current"];
            await Call("verifyExamSubmit", J.S(current, "examId"), J.S(current, "attemptId"));
        }, color: "#B45309");
        examSubmitButton = Button("交卷…", ConfirmAndSubmitExamAsync, color: "#2563EB");
        examExportButton = Button("导出答卷…", () => SaveAnswerTextAsync("exportExam", "考试答卷"));

        var navActions = Row(examPrevButton, examNextButton);
        examAssistActions = Row(examAssistAllButton, examAssistOneButton, examAssistCancelButton, examAssistSettingsButton, examAssistStatusText);
        var submitActions = Row(examSaveButton, examSavePlatformButton, examVerifyButton, examSubmitButton, examExportButton);
        var bottomBar = Card(Column(StretchRow(navActions, submitActions), examAssistActions));
        bottomBar.Padding = new Thickness(16, 12, 16, 12);

        var editorView = new Grid { RowSpacing = 12 };
        editorView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        editorView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        editorView.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        editorView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        editorView.Children.Add(editorTopCard);
        editorView.Children.Add(examEditorNotice);
        editorView.Children.Add(examBodyGrid);
        editorView.Children.Add(bottomBar);
        Grid.SetRow(examEditorNotice, 1);
        Grid.SetRow(examBodyGrid, 2);
        Grid.SetRow(bottomBar, 3);

        examCountdownTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        examCountdownTimer.Tick += (_, _) =>
        {
            UpdateExamCountdown();
            if (examRemainingSeconds == 0) RefreshExam();
        };

        examHost = new Grid();
        examViews = [listView, editorView];
        foreach (var view in examViews) examHost.Children.Add(view);
        ClipContent(examHost);
        SwitchContent(examHost, examViews, 0, animate: false);

        modeExamButton = Button("考试安排", () => { SwitchExamMode(false); return Task.CompletedTask; }, color: "#2563EB");
        modeHomeworkButton = Button("独立作业（本机草稿）", () => { SwitchExamMode(true); return Task.CompletedTask; });
        var modeBar = Row(modeExamButton, modeHomeworkButton);

        examModeHost = new Grid();
        examModeViews = [examHost, BuildHomeworkSubview()];
        foreach (var v in examModeViews) examModeHost.Children.Add(v);
        ClipContent(examModeHost);
        SwitchContent(examModeHost, examModeViews, 0, animate: false);

        var rootGrid = new Grid { RowSpacing = 12 };
        rootGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        rootGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        rootGrid.Children.Add(modeBar);
        rootGrid.Children.Add(examModeHost);
        Grid.SetRow(examModeHost, 1);
        return rootGrid;
    }

    private void ShowExamEditorView(bool showEditor, bool animate = true)
    {
        if (examHost == null || showingExamEditor == showEditor) return;
        showingExamEditor = showEditor;
        double distance = Math.Max(64, examHost.ActualWidth);
        SwitchContent(examHost, examViews, showEditor ? 1 : 0, showEditor ? -distance : distance, animate);
    }

    private (Expander, StackPanel) BuildCourseChecklistExpander(List<CheckBox> checks, string emptyHeader)
    {
        var listPanel = Column();
        listPanel.Spacing = 4;
        var expander = new Expander
        {
            Header = emptyHeader,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            IsExpanded = false,
        };
        var selectAllBtn = Button("全选", () =>
        {
            foreach (var cb in checks) cb.IsChecked = true;
            UpdateCourseChecklistHeader(expander, checks, emptyHeader);
            return Task.CompletedTask;
        });
        var clearBtn = Button("取消选择", () =>
        {
            foreach (var cb in checks) cb.IsChecked = false;
            UpdateCourseChecklistHeader(expander, checks, emptyHeader);
            return Task.CompletedTask;
        });
        var scroller = Scroll(listPanel);
        scroller.MaxHeight = 200;
        var content = Column(Row(selectAllBtn, clearBtn), scroller);
        content.Spacing = 8;
        expander.Content = content;
        return (expander, listPanel);
    }

    private static void UpdateCourseChecklistHeader(Expander? expander, List<CheckBox> checks, string emptyHeader)
    {
        if (expander == null) return;
        if (checks.Count == 0)
        {
            expander.Header = $"{emptyHeader}（尚未读取课程）";
            return;
        }
        int selected = checks.Count(c => c.IsChecked == true);
        expander.Header = $"{emptyHeader}（已选 {selected} / {checks.Count} 门）";
    }

    private void SyncCourseChecklist(Expander? expander, StackPanel? panel, List<CheckBox> checks, ref string lastSignature, string emptyHeader)
    {
        if (expander == null || panel == null) return;
        var courses = J.A(backend.State["courseChoices"]).OfType<JsonObject>().ToList();
        string sig = string.Join("|", courses.Select(c => $"{J.S(c, "courseId")}:{J.S(c, "clazzId")}:{J.S(c, "title")}"));
        if (sig == lastSignature && checks.Count == courses.Count)
        {
            UpdateCourseChecklistHeader(expander, checks, emptyHeader);
            return;
        }
        bool hadPrevious = lastSignature.Length > 0;
        lastSignature = sig;
        var prevSelected = SelectedCourseKeys(checks).ToHashSet();
        checks.Clear();
        panel.Children.Clear();
        bool isDemo = J.B(Summary, "demo");
        foreach (var c in courses)
        {
            string cid = J.S(c, "courseId");
            string clazzId = J.S(c, "clazzId");
            string title = J.S(c, "title");
            string key = $"{cid}:{clazzId}";
            bool isChecked = hadPrevious
                ? prevSelected.Contains(key)
                : (J.B(c, "selected") || isDemo);
            var cb = new CheckBox
            {
                Content = string.IsNullOrWhiteSpace(title) ? cid : title,
                Tag = key,
                IsChecked = isChecked,
                HorizontalAlignment = HorizontalAlignment.Stretch,
            };
            AutomationProperties.SetName(cb, $"选择课程 {cb.Content}");
            cb.Checked += (_, _) => UpdateCourseChecklistHeader(expander, checks, emptyHeader);
            cb.Unchecked += (_, _) => UpdateCourseChecklistHeader(expander, checks, emptyHeader);
            checks.Add(cb);
            panel.Children.Add(cb);
        }
        UpdateCourseChecklistHeader(expander, checks, emptyHeader);
    }

    private static List<string> SelectedCourseKeys(List<CheckBox> checks)
    {
        var keys = new List<string>();
        foreach (var cb in checks)
            if (cb.IsChecked == true && cb.Tag is string key && !string.IsNullOrWhiteSpace(key))
                keys.Add(key);
        return keys;
    }

    private void RefreshExam()
    {
        if (examHost == null) return;
        var exam = backend.State["exam"] as JsonObject ?? new JsonObject();
        bool busy = J.B(exam, "busy");
        SyncCourseChecklist(examCourseExpander, examCourseChecklistPanel, examCourseChecks, ref examCourseSignature, "选择要读取考试的课程");
        if (examCourseExpander != null) examCourseExpander.IsEnabled = !busy;
        if (examRefreshButton != null) examRefreshButton.IsEnabled = !busy;

        if (examAccountLabel != null)
        {
            if (J.B(Summary, "demo"))
                examAccountLabel.Text = "离线演示模式 · 考试功能";
            else
            {
                string activeId = J.S(Summary, "activeAccount");
                var acc = J.A(backend.State["accountRows"]).FirstOrDefault(r => J.S(r, "id") == activeId);
                examAccountLabel.Text = acc != null ? $"当前账号：{J.S(acc, "display")}" : "当前账号：尚未连接";
            }
        }

        var feedback = exam["feedback"] as JsonObject;
        string fbMsg = J.S(feedback, "message");
        InfoBarSeverity fbSeverity = J.S(feedback, "tone") switch
        {
            "danger" => InfoBarSeverity.Error,
            "warning" => InfoBarSeverity.Warning,
            "success" => InfoBarSeverity.Success,
            _ => InfoBarSeverity.Informational,
        };

        var current = exam["current"] as JsonObject;
        bool hasCurrent = current != null && current.Count > 0 && J.S(current, "examId").Length > 0;
        ShowExamEditorView(hasCurrent);

        if (hasCurrent)
        {
            if (examEditorNotice != null)
            {
                examEditorNotice.Message = fbMsg;
                examEditorNotice.Severity = fbSeverity;
                examEditorNotice.IsOpen = fbMsg.Length > 0;
            }
            RenderExamEditor(exam, current!, busy);
        }
        else
        {
            examCountdownTimer?.Stop();
            examCountdownKey = "";
            examRenderedQuestionKey = "";
            if (examListNotice != null)
            {
                examListNotice.Message = fbMsg;
                examListNotice.Severity = fbSeverity;
                examListNotice.IsOpen = fbMsg.Length > 0;
            }
            RenderExamList(exam, busy);
        }

        RefreshExamInput(exam);
    }

    private void RenderExamList(JsonObject exam, bool busy)
    {
        if (examListContainer == null || examEmptyText == null) return;
        var rows = J.A(exam["list"]).ToArray();
        string sig = (exam["list"]?.ToJsonString() ?? "[]") + "|" + busy;
        if (sig == examLastListSignature) return;
        examLastListSignature = sig;

        examListContainer.Children.Clear();
        examEmptyText.Visibility = rows.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (rows.Length == 0)
        {
            examEmptyText.Text = busy ? "正在读取考试安排…" : "暂无可显示的考试安排。请点击「读取考试」刷新。";
            return;
        }

        foreach (var item in rows)
        {
            string examId = J.S(item, "examId");
            string title = J.S(item, "title");
            string course = J.S(item, "courseTitle");
            string timeText = J.S(item, "timeText");
            string modeText = J.S(item, "modeText");
            string status = J.S(item, "status");
            string statusText = J.S(item, "statusText");
            string draftStateText = J.S(item, "draftStateText");
            string reason = J.S(item, "reason");
            string officialUrl = J.S(item, "officialUrl");
            bool nativeSupported = J.B(item, "nativeSupported");
            bool hasDraft = J.B(item, "hasLocalDraft");

            var metaParts = new List<string>();
            if (course.Length > 0) metaParts.Add(course);
            if (timeText.Length > 0) metaParts.Add(timeText);
            if (modeText.Length > 0) metaParts.Add(modeText);

            var info = Column(
                Text(title, 16),
                Text(string.Join(" · ", metaParts), 12, true)
            );
            info.Spacing = 4;
            ((TextBlock)info.Children[0]).FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;

            var statusLine = statusText;
            if (hasDraft && draftStateText.Length > 0 && draftStateText != statusText)
                statusLine += $" · 本机：{draftStateText}";
            var statusLabel = Text(statusLine, 12);
            statusLabel.Foreground = status switch
            {
                "submitted" or "simulated_submitted" => ThemeBrush("SuccessBrush"),
                "submit_unknown" or "rejected" => ThemeBrush("WarningBrush"),
                "open" when nativeSupported => ThemeBrush("AccentBrush"),
                _ => ThemeBrush("MutedBrush"),
            };
            info.Children.Add(statusLabel);

            if (reason.Length > 0)
            {
                var reasonText = Text(reason, 12, true);
                if (!nativeSupported || status == "submit_unknown")
                    reasonText.Foreground = ThemeBrush("WarningBrush");
                info.Children.Add(reasonText);
            }

            var actions = Row();
            actions.VerticalAlignment = VerticalAlignment.Center;

            if (status == "submit_unknown")
            {
                var verifyBtn = Button("核验交卷结果", async () => await Call("verifyExamSubmit", examId, J.S(item, "attemptId")), color: "#B45309");
                verifyBtn.IsEnabled = !busy;
                actions.Children.Add(verifyBtn);
            }
            else if ((status is "submitted" or "simulated_submitted") && hasDraft &&
                J.S(item, "draftState") is "submitted" or "simulated_submitted")
            {
                var viewBtn = Button("查看答卷", async () => await Call("openExam", examId, true));
                viewBtn.IsEnabled = !busy;
                actions.Children.Add(viewBtn);
            }
            else if (nativeSupported)
            {
                var enterBtn = Button(hasDraft ? "继续答题" : "进入考试", async () => await ConfirmAndOpenExamAsync(examId), color: "#2563EB");
                enterBtn.IsEnabled = !busy;
                actions.Children.Add(enterBtn);
            }

            if (officialUrl.Length > 0 && (!nativeSupported || status == "submit_unknown"))
            {
                var officialBtn = Button("打开官方入口", () =>
                {
                    if (Uri.TryCreate(officialUrl, UriKind.Absolute, out var uri) && uri.Scheme is "https" or "http")
                        _ = Windows.System.Launcher.LaunchUriAsync(uri);
                    return Task.CompletedTask;
                });
                actions.Children.Add(officialBtn);
            }

            var card = Card(StretchRow(info, actions));
            card.Padding = new Thickness(18, 14, 18, 14);
            examListContainer.Children.Add(card);
        }
    }

    private async Task ConfirmAndOpenExamAsync(string examId)
    {
        var preview = await Call("openExam", examId, false);
        if (preview is not JsonObject info) return;

        bool startsTimer = J.B(info, "startsTimer");
        if (startsTimer)
        {
            var lines = new List<string>
            {
                $"账号：{J.S(info, "accountLabel", "当前账号")}",
                $"课程：{J.S(info, "courseTitle")}",
                $"考试：{J.S(info, "title")}",
                $"时间：{J.S(info, "timeText")}",
                $"时限：{(J.I(info, "limitMinutes") > 0 ? $"{J.I(info, "limitMinutes")} 分钟" : "以平台须知为准")}",
                $"方式：{J.S(info, "modeText")}",
                "",
                "进入考试后可能开始服务端计时或占用考试次数，确认现在进入吗？"
            };
            if (!await Confirm("确认进入考试", string.Join("\n", lines), "进入考试"))
                return;
        }
        await Call("openExam", examId, true);
    }

    private void RenderExamEditor(JsonObject exam, JsonObject current, bool busy)
    {
        string examId = J.S(current, "examId");
        string attemptId = J.S(current, "attemptId");
        string state = J.S(current, "state");
        string stateText = J.S(current, "stateText");
        bool dirty = J.B(current, "dirty");
        bool editable = ExamEditableStates.Contains(state);

        if (examEditorTitle != null) examEditorTitle.Text = J.S(current, "title");
        if (examEditorAccount != null)
            examEditorAccount.Text = $"{J.S(current, "courseTitle")} · {J.S(current, "accountLabel")}";
        if (examStateText != null)
        {
            examStateText.Text = dirty ? $"{stateText} · 有修改 · 未保存" : stateText;
            examStateText.Foreground = state switch
            {
                "submitted" or "simulated_submitted" => ThemeBrush("SuccessBrush"),
                "submit_unknown" or "rejected" => ThemeBrush("WarningBrush"),
                _ => ThemeBrush("MutedBrush"),
            };
        }

        string countdownKey = $"{examId}|{attemptId}|{J.S(current, "deadlineUtc")}|{state}";
        if (countdownKey != examCountdownKey)
        {
            examCountdownKey = countdownKey;
            examRemainingSeconds = current["remainingSeconds"] != null ? J.I(current, "remainingSeconds") : -1;
            examCountdownEndsAt = Environment.TickCount64 + examRemainingSeconds * 1000L;
            UpdateExamCountdown();
            if (editable && examRemainingSeconds > 0) examCountdownTimer?.Start();
            else examCountdownTimer?.Stop();
        }
        else if (!editable)
        {
            examCountdownTimer?.Stop();
            UpdateExamCountdown();
        }

        var questions = J.A(current["questions"]).ToArray();
        if (questions.Length == 0) return;
        if (examCurrentQuestionIndex < 0 || examCurrentQuestionIndex >= questions.Length)
            examCurrentQuestionIndex = 0;

        int answeredCount = J.I(current, "answeredCount");
        int totalCount = J.I(current, "totalCount");
        if (examProgressText != null)
            examProgressText.Text = $"已答 {answeredCount} / {totalCount} 题";

        if (examQuestionNumberGrid != null)
        {
            examQuestionNumberGrid.Children.Clear();
            examQuestionNumberGrid.ColumnDefinitions.Clear();
            examQuestionNumberGrid.RowDefinitions.Clear();
            const int cols = 2;
            for (int c = 0; c < cols; c++)
                examQuestionNumberGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            int rowCount = (questions.Length + cols - 1) / cols;
            for (int r = 0; r < rowCount; r++)
                examQuestionNumberGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            for (int i = 0; i < questions.Length; i++)
            {
                var q = questions[i];
                int idx = i;
                int no = J.I(q, "no");
                bool answered = J.B(q, "answered");
                bool isCurrent = idx == examCurrentQuestionIndex;
                string label = $"{no} · {(answered ? "已答" : "未答")}";
                var btn = new Button
                {
                    Content = label,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    Padding = new Thickness(8, 6, 8, 6),
                    BorderThickness = new Thickness(isCurrent ? 2 : 1),
                    BorderBrush = ThemeBrush(isCurrent ? "AccentBrush" : "BorderBrush"),
                    Background = ThemeBrush(isCurrent ? "SelectionBrush" : answered ? "SoftBrush" : "SurfaceBrush"),
                };
                AutomationProperties.SetName(btn, $"第 {no} 题 · {(answered ? "已答" : "未答")}{(isCurrent ? "（当前）" : "")}");
                btn.Click += (_, _) => SelectExamQuestion(idx);
                Grid.SetRow(btn, i / cols);
                Grid.SetColumn(btn, i % cols);
                examQuestionNumberGrid.Children.Add(btn);
            }
        }

        var currentQuestion = questions[examCurrentQuestionIndex];
        string qId = J.S(currentQuestion, "id");
        bool canEdit = editable && !busy && examRemainingSeconds > 0;
        string qKey = $"{examId}|{attemptId}|{qId}|{state}|{canEdit}";
        // 当正在同一道可编辑题的输入框中输入时，避免因自身 setExamAnswer 回推状态而重建控件导致光标丢失。
        bool keepEditingBox = qKey == examRenderedQuestionKey &&
            (J.S(currentQuestion, "type") is "completion" or "shortanswer") &&
            examQuestionDetailPanel != null && examQuestionDetailPanel.Children.Count > 0;
        if (!keepEditingBox)
        {
            examRenderedQuestionKey = qKey;
            RenderCurrentQuestion(currentQuestion, canEdit);
        }
        else
        {
            UpdateQuestionSourceHeader(currentQuestion);
        }

        var assist = exam["assist"] as JsonObject ?? new JsonObject();
        bool assistEnabled = J.B(assist, "enabled");
        bool assistRunning = J.B(assist, "running");
        if (examAssistActions != null)
            examAssistActions.Visibility = assistEnabled ? Visibility.Visible : Visibility.Collapsed;

        if (examBackButton != null) examBackButton.IsEnabled = !busy;
        if (examPrevButton != null) examPrevButton.IsEnabled = examCurrentQuestionIndex > 0;
        if (examNextButton != null) examNextButton.IsEnabled = examCurrentQuestionIndex < questions.Length - 1;
        if (examSaveButton != null)
        {
            examSaveButton.Visibility = editable ? Visibility.Visible : Visibility.Collapsed;
            examSaveButton.IsEnabled = !busy;
        }
        if (examExportButton != null)
            examExportButton.Visibility = state is "submitted" or "simulated_submitted" ? Visibility.Visible : Visibility.Collapsed;
        if (examSavePlatformButton != null)
        {
            examSavePlatformButton.Visibility = J.B(current, "canSaveToPlatform") && editable ? Visibility.Visible : Visibility.Collapsed;
            examSavePlatformButton.IsEnabled = canEdit;
        }
        if (examVerifyButton != null)
        {
            examVerifyButton.Visibility = state == "submit_unknown" ? Visibility.Visible : Visibility.Collapsed;
            examVerifyButton.IsEnabled = !busy;
        }
        if (examSubmitButton != null)
        {
            examSubmitButton.Visibility = state is "submitted" or "simulated_submitted" ? Visibility.Collapsed : Visibility.Visible;
            examSubmitButton.IsEnabled = canEdit;
        }

        if (examAssistAllButton != null)
        {
            examAssistAllButton.Visibility = assistEnabled && !assistRunning && editable ? Visibility.Visible : Visibility.Collapsed;
            examAssistAllButton.IsEnabled = canEdit;
        }
        if (examAssistOneButton != null)
        {
            examAssistOneButton.Visibility = assistEnabled && !assistRunning && editable ? Visibility.Visible : Visibility.Collapsed;
            examAssistOneButton.IsEnabled = canEdit;
        }
        if (examAssistCancelButton != null)
            examAssistCancelButton.Visibility = assistEnabled && assistRunning ? Visibility.Visible : Visibility.Collapsed;
        if (examAssistSettingsButton != null)
            examAssistSettingsButton.Visibility = assistEnabled ? Visibility.Visible : Visibility.Collapsed;
        if (examAssistStatusText != null)
        {
            if (assistRunning)
            {
                examAssistStatusText.Text = $"参考答案 {J.I(assist, "completed")} / {J.I(assist, "total")}";
                examAssistStatusText.Visibility = Visibility.Visible;
            }
            else
            {
                examAssistStatusText.Visibility = Visibility.Collapsed;
            }
        }
    }

    private void SelectExamQuestion(int index)
    {
        var current = backend.State["exam"]?["current"] as JsonObject;
        var questions = J.A(current?["questions"]).ToArray();
        if (index < 0 || index >= questions.Length) return;
        examCurrentQuestionIndex = index;
        examRenderedQuestionKey = "";
        RefreshExam();
    }

    private void RenderCurrentQuestion(JsonNode question, bool editable)
    {
        if (examQuestionDetailPanel == null) return;
        examPopulatingQuestion = true;
        try
        {
            examQuestionDetailPanel.Children.Clear();
            string qId = J.S(question, "id");
            int no = J.I(question, "no");
            string qType = J.S(question, "type");
            string typeLabel = qType switch
            {
                "single" => "单选题",
                "multiple" => "多选题",
                "judgement" => "判断题",
                "completion" => "填空题",
                "shortanswer" => "简答题",
                _ => "题目",
            };
            string score = J.S(question, "score");
            string heading = score.Length > 0 ? $"第 {no} 题 · {typeLabel}（{score}）" : $"第 {no} 题 · {typeLabel}";
            var headingText = Text(heading, 14, true);
            headingText.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;

            var sourceTag = Text("", 12, true);
            sourceTag.Tag = "sourceTag";
            examQuestionDetailPanel.Children.Add(StretchRow(headingText, sourceTag));
            UpdateQuestionSourceHeader(question);

            var titleBlock = Text(J.S(question, "title"), 16);
            titleBlock.IsTextSelectionEnabled = true;
            examQuestionDetailPanel.Children.Add(titleBlock);

            foreach (var imgNode in J.A(question["images"]))
                AddPaperImage(examQuestionDetailPanel, imgNode?.ToString() ?? "", "图片加载失败，请在官方入口查看原题。");

            var ans = question["answer"];
            var selectedIds = J.A(ans?["option_ids"]).Select(n => n.ToString()).ToHashSet();

            if (qType is "single" or "judgement")
            {
                string group = $"exam_q_{qId}";
                foreach (var opt in J.A(question["options"]))
                {
                    string optId = J.S(opt, "id");
                    string optText = J.S(opt, "text");
                    string display = optId == optText ? optText : $"{optId}. {optText}";
                    var radio = new RadioButton
                    {
                        Content = Text(display),
                        GroupName = group,
                        IsChecked = selectedIds.Contains(optId),
                        IsEnabled = editable,
                    };
                    AutomationProperties.SetName(radio, display);
                    radio.Checked += async (_, _) =>
                    {
                        if (!examPopulatingQuestion)
                            await Call("setExamAnswer", qId, new { option_ids = new[] { optId } });
                    };
                    examQuestionDetailPanel.Children.Add(radio);
                }
            }
            else if (qType == "multiple")
            {
                var boxes = new List<(string Id, CheckBox Box)>();
                foreach (var opt in J.A(question["options"]))
                {
                    string optId = J.S(opt, "id");
                    string optText = J.S(opt, "text");
                    string display = optId == optText ? optText : $"{optId}. {optText}";
                    var check = new CheckBox
                    {
                        Content = Text(display),
                        IsChecked = selectedIds.Contains(optId),
                        IsEnabled = editable,
                    };
                    AutomationProperties.SetName(check, display);
                    boxes.Add((optId, check));
                    check.Click += async (_, _) =>
                    {
                        if (examPopulatingQuestion) return;
                        var picked = boxes.Where(p => p.Box.IsChecked == true).Select(p => p.Id).ToArray();
                        await Call("setExamAnswer", qId, new { option_ids = picked });
                    };
                    examQuestionDetailPanel.Children.Add(check);
                }
            }
            else if (qType == "completion")
            {
                int blankCount = Math.Max(1, J.I(question, "blankCount"));
                var existingBlanks = J.A(ans?["blanks"]).Select(n => n.ToString()).ToArray();
                var blankBoxes = new List<TextBox>();
                for (int i = 0; i < blankCount; i++)
                {
                    var box = new TextBox
                    {
                        Header = $"第 {i + 1} 空",
                        Text = i < existingBlanks.Length ? existingBlanks[i] : "",
                        IsReadOnly = !editable,
                        HorizontalAlignment = HorizontalAlignment.Stretch,
                    };
                    AutomationProperties.SetName(box, $"第 {no} 题 第 {i + 1} 空");
                    blankBoxes.Add(box);
                    box.TextChanged += async (_, _) =>
                    {
                        if (examPopulatingQuestion) return;
                        await Call("setExamAnswer", qId, new { blanks = blankBoxes.Select(b => b.Text).ToArray() });
                    };
                    examQuestionDetailPanel.Children.Add(box);
                }
            }
            else
            {
                var textBox = new TextBox
                {
                    Header = "作答内容",
                    Text = J.S(ans, "text"),
                    AcceptsReturn = true,
                    TextWrapping = TextWrapping.Wrap,
                    MinHeight = 140,
                    MaxLength = 12000,
                    IsReadOnly = !editable,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                };
                AutomationProperties.SetName(textBox, $"第 {no} 题 作答内容");
                textBox.TextChanged += async (_, _) =>
                {
                    if (!examPopulatingQuestion)
                        await Call("setExamAnswer", qId, new { text = textBox.Text });
                };
                examQuestionDetailPanel.Children.Add(textBox);
            }
        }
        finally
        {
            examPopulatingQuestion = false;
        }
    }

    private void UpdateQuestionSourceHeader(JsonNode question)
    {
        if (examQuestionDetailPanel == null || examQuestionDetailPanel.Children.Count == 0) return;
        if (examQuestionDetailPanel.Children[0] is Grid headerRow && headerRow.Children.Count > 1 && headerRow.Children[1] is TextBlock sourceTag)
        {
            string srcText = J.S(question, "sourceText", "未作答");
            string reason = J.S(question, "reason");
            sourceTag.Text = reason.Length > 0 ? $"{srcText} · {reason}" : srcText;
            sourceTag.Foreground = J.S(question, "source") switch
            {
                "manual" => ThemeBrush("AccentBrush"),
                "" => ThemeBrush("MutedBrush"),
                _ => ThemeBrush("SuccessBrush"),
            };
        }
    }

    private async void AddPaperImage(StackPanel panel, string url, string failureMessage)
    {
        var image = new Image { MaxHeight = 260, Stretch = Stretch.Uniform, HorizontalAlignment = HorizontalAlignment.Left };
        var failed = Text(failureMessage, 12, true);
        failed.Visibility = Visibility.Collapsed;
        panel.Children.Add(image);
        panel.Children.Add(failed);
        try
        {
            if (Uri.TryCreate(url, UriKind.Absolute, out var uri) && uri.IsFile)
            {
                var file = await Windows.Storage.StorageFile.GetFileFromPathAsync(uri.LocalPath);
                using var stream = await file.OpenReadAsync();
                var bitmap = new BitmapImage();
                await bitmap.SetSourceAsync(stream);
                image.Source = bitmap;
                return;
            }
        }
        catch (Exception) { }
        failed.Visibility = Visibility.Visible;
    }

    private void UpdateExamCountdown()
    {
        if (examTimerText == null) return;
        var current = backend.State["exam"]?["current"] as JsonObject;
        string state = J.S(current, "state");
        if (!ExamEditableStates.Contains(state))
        {
            examTimerText.Text = J.S(current, "stateText");
            examTimerText.Foreground = ThemeBrush("MutedBrush");
            return;
        }
        if (examRemainingSeconds < 0)
        {
            examTimerText.Text = "不限时间";
            examTimerText.Foreground = ThemeBrush("MutedBrush");
            return;
        }
        examRemainingSeconds = (int)Math.Max(0, (examCountdownEndsAt - Environment.TickCount64 + 999) / 1000);
        if (examRemainingSeconds == 0)
        {
            examTimerText.Text = "已到截止时间，请在官方入口确认收卷状态";
            examTimerText.Foreground = ThemeBrush("ErrorBrush");
            examCountdownTimer?.Stop();
            return;
        }
        var span = TimeSpan.FromSeconds(examRemainingSeconds);
        examTimerText.Text = span.TotalHours >= 1
            ? $"剩余 {(int)span.TotalHours:D2}:{span.Minutes:D2}:{span.Seconds:D2}"
            : $"剩余 {span.Minutes:D2}:{span.Seconds:D2}";
        examTimerText.Foreground = examRemainingSeconds < 300 ? ThemeBrush("WarningBrush") : ThemeBrush("InkBrush");
    }

    private async Task CloseExamViewAsync()
    {
        var result = (await Call("closeExam", false))?.ToString();
        if (result == "confirm")
        {
            var dialog = Dialog("有未保存的答卷修改", Text("当前答卷有尚未保存到本机草稿的修改，返回列表前如何处理？"), "继续答题");
            dialog.PrimaryButtonText = "保存草稿并返回";
            dialog.SecondaryButtonText = "放弃修改并返回";
            var choice = await ShowModal(dialog);
            if (choice == ContentDialogResult.Primary)
            {
                if ((await Call("saveExamDraft", false))?.ToString() == "true")
                    await Call("closeExam", true);
            }
            else if (choice == ContentDialogResult.Secondary)
            {
                await Call("closeExam", true);
            }
        }
    }

    private async Task ConfirmAndSubmitExamAsync()
    {
        var current = backend.State["exam"]?["current"] as JsonObject;
        if (current == null || current.Count == 0) return;
        string examId = J.S(current, "examId");
        string attemptId = J.S(current, "attemptId");
        int answered = J.I(current, "answeredCount");
        int total = J.I(current, "totalCount");
        var unanswered = J.A(current["unansweredNumbers"]).Select(n => n.ToString()).ToArray();
        string unansweredText = unanswered.Length > 0 ? string.Join("、", unanswered) : "无";

        var lines = new List<string>
        {
            $"账号：{J.S(current, "accountLabel", "当前账号")}",
            $"试卷：{J.S(current, "title")}",
            $"作答进度：{answered} / {total} 题",
            $"未答题号：{unansweredText}",
            "",
            "交卷后不能修改，确认现在交卷吗？"
        };
        if (await Confirm("确认交卷", string.Join("\n", lines), "确认交卷"))
            await Call("submitExam", examId, attemptId);
    }

    private async Task RequestExamSuggestionsAsync(string scopeMode)
    {
        var current = backend.State["exam"]?["current"] as JsonObject;
        if (current == null || current.Count == 0) return;

        string scope = "all";
        bool replaceManual = false;
        if (scopeMode == "current")
        {
            var questions = J.A(current["questions"]).ToArray();
            if (examCurrentQuestionIndex < 0 || examCurrentQuestionIndex >= questions.Length) return;
            var q = questions[examCurrentQuestionIndex];
            scope = J.S(q, "id");
            if (J.S(q, "source") == "manual")
            {
                if (!await Confirm("替换手动答案？", "当前题目已由您手动修改。要用新获取的参考答案替换当前手动答案吗？", "替换并获取"))
                    return;
                replaceManual = true;
            }
        }

        var preview = await Call("requestExamSuggestions", scope, false, replaceManual);
        if (preview is not JsonObject info) return;

        int qCount = J.I(info, "questionCount");
        if (qCount == 0)
        {
            await Call("requestExamSuggestions", scope, true, replaceManual);
            return;
        }
        int imgCount = J.I(info, "imageCount");
        var body = string.Join("\n", new[]
        {
            $"生效 AI 服务：{J.S(info, "aiSummary")}",
            $"生效题库：{J.S(info, "bankSummary")}",
            $"待查题目数：{qCount} 题（含图题：{imgCount} 处）",
            "",
            "参考答案仅填入本机答卷并标注来源，不会自动保存到平台或交卷。"
        });
        if (await Confirm("获取参考答案", body, "开始获取"))
            await Call("requestExamSuggestions", scope, true, replaceManual);
    }

    private void RefreshExamInput(JsonObject exam)
    {
        var input = exam["input"];
        string sig = input?.ToJsonString() ?? "{}";
        if (sig == examInputSignature) return;
        examInputSignature = sig;
        if (input is not JsonObject obj || obj.Count == 0)
        {
            examInputDialog?.Hide();
            return;
        }
        examInputDialog?.Hide();
        var clone = obj.DeepClone();
        EnqueueDialog(() => ShowExamInputAsync(clone, sig));
    }

    private async Task ShowExamInputAsync(JsonNode input, string signature)
    {
        if (examInputSignature != signature) return;
        var panel = Column(
            Text(J.S(input, "message", "请确认是否继续当前考试辅助请求。"), 12, true),
            Text(J.S(input, "title"), 15)
        );
        var dialog = Dialog("题图发送确认", Scroll(panel), "取消获取");
        examInputDialog = dialog;
        if (J.S(input, "kind") == "choice")
        {
            foreach (var choice in J.A(input["choices"]))
                panel.Children.Add(Button(J.S(choice, "label"), async () =>
                {
                    await Call("replyExamInput", J.S(choice, "value"), false);
                    dialog.Hide();
                }));
        }
        else
        {
            var answerBox = new TextBox { Header = "回复内容", AcceptsReturn = true, MinHeight = 80 };
            panel.Children.Add(answerBox);
            dialog.PrimaryButtonText = "确认";
            dialog.PrimaryButtonClick += async (_, _) => await Call("replyExamInput", answerBox.Text, false);
        }
        dialog.CloseButtonClick += async (_, _) => await Call("replyExamInput", "", true);
        try { await ShowModal(dialog); }
        finally { if (examInputDialog == dialog) examInputDialog = null; }
    }
}
