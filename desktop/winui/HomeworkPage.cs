using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private Grid? examModeHost, homeworkHost;
    private FrameworkElement[] examModeViews = [], homeworkViews = [];
    private Button? modeExamButton, modeHomeworkButton;
    private bool showingHomeworkMode, showingHomeworkEditor;

    // 独立作业列表视图
    private TextBox? hwUrlInput;
    private Button? hwLoadButton, hwFetchListButton;
    private InfoBar? hwListNotice;
    private TextBlock? hwEmptyText, hwDraftsHeader, hwRemoteHeader;
    private StackPanel? hwDraftListContainer, hwRemoteListContainer;

    // 独立作业草稿编辑器
    private Button? hwBackButton, hwPrevButton, hwNextButton, hwSaveButton, hwSavePlatformButton, hwCopyButton, hwExportButton;
    private Button? hwAssistAllButton, hwAssistOneButton, hwAssistCancelButton;
    private TextBlock? hwEditorTitle, hwEditorAccount, hwStateText, hwProgressText, hwAssistStatusText;
    private InfoBar? hwEditorNotice;
    private Grid? hwBodyGrid, hwQuestionNumberGrid;
    private Border? hwQuestionNumberCard, hwQuestionCard;
    private ScrollViewer? hwQuestionNumberScroll, hwQuestionDetailScroll;
    private StackPanel? hwQuestionDetailPanel, hwAssistActions;
    private TextBlock? hwQuestionSource;

    private string hwRenderedQuestionKey = "";
    private string hwRenderedAnswer = "";
    private string hwLastListSignature = "";
    private int hwCurrentQuestionIndex;
    private bool hwPopulatingQuestion;

    private FrameworkElement BuildHomeworkSubview()
    {
        hwUrlInput = new TextBox
        {
            PlaceholderText = "粘贴超星独立作业链接（https://*.chaoxing.com/mooc-ans/mooc2/work/dowork?...）",
        };
        AutomationProperties.SetName(hwUrlInput, "独立作业链接");
        hwLoadButton = Button("读取作业", async () => await Call("loadHomework", hwUrlInput.Text), color: "#2563EB");
        hwFetchListButton = Button("读取账号作业", async () => await Call("loadHomeworks"));

        var urlGrid = new Grid { ColumnSpacing = 10 };
        urlGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        urlGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        urlGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        urlGrid.Children.Add(hwUrlInput);
        urlGrid.Children.Add(hwLoadButton);
        urlGrid.Children.Add(hwFetchListButton);
        Grid.SetColumn(hwLoadButton, 1);
        Grid.SetColumn(hwFetchListButton, 2);

        hwListNotice = new InfoBar { IsOpen = false, IsClosable = false };
        hwDraftsHeader = Text("本机草稿", 14);
        hwDraftsHeader.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        hwDraftsHeader.Visibility = Visibility.Collapsed;
        hwDraftListContainer = Column();
        hwRemoteHeader = Text("账号作业列表", 14);
        hwRemoteHeader.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        hwRemoteHeader.Visibility = Visibility.Collapsed;
        hwRemoteListContainer = Column();
        hwEmptyText = Text("暂无作业或本机草稿。点击「读取账号作业」或粘贴作业链接并点击「读取作业」（仅保存在本机，不向平台提交）。", 14, true);
        hwEmptyText.HorizontalAlignment = HorizontalAlignment.Center;
        hwEmptyText.Margin = new Thickness(0, 32, 0, 0);

        var listSections = Column(hwDraftsHeader, hwDraftListContainer, hwRemoteHeader, hwRemoteListContainer);
        listSections.Spacing = 10;
        var listBody = new Grid();
        listBody.Children.Add(Scroll(listSections));
        listBody.Children.Add(hwEmptyText);

        var listView = new Grid { RowSpacing = 12 };
        listView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        listView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        listView.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        listView.Children.Add(urlGrid);
        listView.Children.Add(hwListNotice);
        listView.Children.Add(listBody);
        Grid.SetRow(hwListNotice, 1);
        Grid.SetRow(listBody, 2);

        // 草稿编辑器顶部栏
        hwBackButton = Button("← 返回列表", CloseHomeworkViewAsync);
        hwEditorTitle = Text("", 17);
        hwEditorTitle.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        hwEditorAccount = Text("", 12, true);
        hwStateText = Text("仅本机草稿 · 不向平台提交", 12, true);
        hwStateText.HorizontalAlignment = HorizontalAlignment.Right;

        var headerLeft = Row(hwBackButton, Column(hwEditorTitle, hwEditorAccount));
        ((StackPanel)headerLeft.Children[1]).Spacing = 2;
        var editorTopCard = Card(StretchRow(headerLeft, hwStateText));
        editorTopCard.Padding = new Thickness(16, 12, 16, 12);

        hwEditorNotice = new InfoBar { IsOpen = false, IsClosable = false };

        hwQuestionNumberGrid = new Grid { ColumnSpacing = 8, RowSpacing = 8 };
        hwProgressText = Text("", 12, true);
        hwQuestionNumberScroll = Scroll(Column(Text("题目导航", 14), hwProgressText, hwQuestionNumberGrid));
        hwQuestionNumberCard = Card(hwQuestionNumberScroll);
        hwQuestionNumberCard.Padding = new Thickness(14);

        hwQuestionDetailPanel = Column();
        hwQuestionDetailScroll = Scroll(hwQuestionDetailPanel);
        hwQuestionCard = Card(hwQuestionDetailScroll);

        hwBodyGrid = new Grid { ColumnSpacing = 14, RowSpacing = 14 };
        hwBodyGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(250) });
        hwBodyGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        hwBodyGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        hwBodyGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        hwBodyGrid.Children.Add(hwQuestionNumberCard);
        hwBodyGrid.Children.Add(hwQuestionCard);
        Grid.SetColumn(hwQuestionCard, 1);

        hwBodyGrid.SizeChanged += (_, _) =>
        {
            if (hwBodyGrid == null || hwQuestionNumberCard == null || hwQuestionCard == null) return;
            bool wide = hwBodyGrid.ActualWidth > 860;
            if (wide)
            {
                hwBodyGrid.ColumnDefinitions[0].Width = new GridLength(250);
                hwBodyGrid.ColumnDefinitions[1].Width = new GridLength(1, GridUnitType.Star);
                hwBodyGrid.RowDefinitions[0].Height = new GridLength(1, GridUnitType.Star);
                hwBodyGrid.RowDefinitions[1].Height = GridLength.Auto;
                Grid.SetRow(hwQuestionNumberCard, 0);
                Grid.SetColumn(hwQuestionNumberCard, 0);
                Grid.SetRow(hwQuestionCard, 0);
                Grid.SetColumn(hwQuestionCard, 1);
            }
            else
            {
                hwBodyGrid.ColumnDefinitions[0].Width = new GridLength(1, GridUnitType.Star);
                hwBodyGrid.ColumnDefinitions[1].Width = new GridLength(0);
                hwBodyGrid.RowDefinitions[0].Height = new GridLength(180);
                hwBodyGrid.RowDefinitions[1].Height = new GridLength(1, GridUnitType.Star);
                Grid.SetRow(hwQuestionNumberCard, 0);
                Grid.SetColumn(hwQuestionNumberCard, 0);
                Grid.SetRow(hwQuestionCard, 1);
                Grid.SetColumn(hwQuestionCard, 0);
            }
        };

        hwPrevButton = Button("上一题", () =>
        {
            hwCurrentQuestionIndex = Math.Max(0, hwCurrentQuestionIndex - 1);
            hwRenderedQuestionKey = "";
            RefreshHomework();
            return Task.CompletedTask;
        });
        hwNextButton = Button("下一题", () =>
        {
            hwCurrentQuestionIndex++;
            hwRenderedQuestionKey = "";
            RefreshHomework();
            return Task.CompletedTask;
        });
        hwAssistStatusText = Text("", 12, true);
        hwAssistAllButton = Button("获取参考答案…", () => RequestHomeworkAssistAsync("all"));
        hwAssistOneButton = Button("本题参考答案", () => RequestHomeworkAssistSingleAsync());
        hwAssistCancelButton = Button("取消获取", async () => await Call("cancelExamTask"), color: "#B91C1C");
        hwAssistCancelButton.Visibility = Visibility.Collapsed;

        hwSaveButton = Button("保存本机草稿", async () => await Call("saveHomeworkDraft", false), color: "#2563EB");
        hwSavePlatformButton = Button("暂存到平台…", async () =>
        {
            var current = backend.State["homework"]?["current"];
            if (await Confirm("暂存作业到平台", $"将「{J.S(current, "title")}」的当前答案暂存到平台，暂存后仍可继续修改。", "暂存到平台"))
                await Call("saveHomeworkDraft", true);
        });
        hwCopyButton = Button("复制全文", CopyHomeworkDraftAsync);
        hwExportButton = Button("导出文本…", () => SaveAnswerTextAsync("exportHomeworkDraft", "独立作业草稿"));

        var leftBar = Row(hwPrevButton, hwNextButton);
        hwAssistActions = Row(hwAssistAllButton, hwAssistOneButton, hwAssistCancelButton, hwAssistStatusText);
        var rightBar = Row(hwSaveButton, hwSavePlatformButton, hwCopyButton, hwExportButton);
        var bottomBar = Card(Column(StretchRow(leftBar, rightBar), hwAssistActions));
        bottomBar.Padding = new Thickness(14, 10, 14, 10);

        var editorView = new Grid { RowSpacing = 12 };
        editorView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        editorView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        editorView.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        editorView.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        editorView.Children.Add(editorTopCard);
        editorView.Children.Add(hwEditorNotice);
        editorView.Children.Add(hwBodyGrid);
        editorView.Children.Add(bottomBar);
        Grid.SetRow(hwEditorNotice, 1);
        Grid.SetRow(hwBodyGrid, 2);
        Grid.SetRow(bottomBar, 3);

        homeworkHost = new Grid();
        homeworkViews = [listView, editorView];
        foreach (var v in homeworkViews) homeworkHost.Children.Add(v);
        ClipContent(homeworkHost);
        SwitchContent(homeworkHost, homeworkViews, 0, animate: false);
        return homeworkHost;
    }

    private void SwitchExamMode(bool toHomework)
    {
        if (examModeHost == null || showingHomeworkMode == toHomework) return;
        showingHomeworkMode = toHomework;
        double distance = Math.Max(64, examModeHost.ActualWidth);
        SwitchContent(examModeHost, examModeViews, toHomework ? 1 : 0, toHomework ? -distance : distance);
        if (modeExamButton != null)
            modeExamButton.Background = !toHomework ? ThemeBrush("AccentBrush") : null;
        if (modeHomeworkButton != null)
            modeHomeworkButton.Background = toHomework ? ThemeBrush("AccentBrush") : null;
    }

    private void ShowHomeworkEditorView(bool showEditor, bool animate = true)
    {
        if (homeworkHost == null || showingHomeworkEditor == showEditor) return;
        showingHomeworkEditor = showEditor;
        double distance = Math.Max(64, homeworkHost.ActualWidth);
        SwitchContent(homeworkHost, homeworkViews, showEditor ? 1 : 0, showEditor ? -distance : distance, animate);
    }

    private void RefreshHomework()
    {
        if (homeworkHost == null) return;
        var hw = backend.State["homework"] as JsonObject ?? new JsonObject();
        bool busy = J.B(hw, "busy");
        if (hwLoadButton != null) hwLoadButton.IsEnabled = !busy;
        if (hwFetchListButton != null) hwFetchListButton.IsEnabled = !busy;

        var feedback = hw["feedback"] as JsonObject;
        string fbMsg = J.S(feedback, "message");
        InfoBarSeverity fbSeverity = J.S(feedback, "tone") switch
        {
            "success" => InfoBarSeverity.Success,
            "warning" => InfoBarSeverity.Warning,
            "danger" => InfoBarSeverity.Error,
            _ => InfoBarSeverity.Informational,
        };

        var current = hw["current"] as JsonObject ?? new JsonObject();
        bool hasCurrent = current.ContainsKey("draftId") && !string.IsNullOrEmpty(J.S(current, "draftId"));
        ShowHomeworkEditorView(hasCurrent);

        if (!hasCurrent)
        {
            hwRenderedQuestionKey = "";
            if (hwListNotice != null)
            {
                hwListNotice.Message = fbMsg;
                hwListNotice.Severity = fbSeverity;
                hwListNotice.IsOpen = !string.IsNullOrWhiteSpace(fbMsg);
            }
            var drafts = J.A(hw["drafts"]).OfType<JsonObject>().ToList();
            var remoteWorks = J.A(hw["list"]).OfType<JsonObject>().ToList();
            string sig = (hw["drafts"]?.ToJsonString() ?? "[]") + "|" + (hw["list"]?.ToJsonString() ?? "[]") + "|" + busy;
            if (sig != hwLastListSignature && hwDraftListContainer != null && hwRemoteListContainer != null)
            {
                hwLastListSignature = sig;
                hwDraftListContainer.Children.Clear();
                foreach (var item in drafts)
                {
                    string did = J.S(item, "draftId");
                    string title = J.S(item, "title");
                    int answered = J.I(item, "answeredCount");
                    int supported = J.I(item, "supportedCount");
                    int total = J.I(item, "totalCount");
                    string updated = J.S(item, "updatedAt");
                    var openBtn = Button("打开草稿", async () => await Call("openHomeworkDraft", did), color: "#2563EB");
                    var delBtn = Button("删除", async () =>
                    {
                        if (await Confirm("删除本机作业草稿", $"确定删除「{title}」？此操作只删除本机草稿，不影响平台。", "确认删除"))
                            await Call("deleteHomeworkDraft", did);
                    });
                    var meta = Text($"已答支持题型 {answered}/{supported}（共 {total} 题） · 更新：{updated}", 12, true);
                    var card = Card(StretchRow(Column(Text(title, 15), meta), Row(openBtn, delBtn)));
                    card.Padding = new Thickness(14, 10, 14, 10);
                    hwDraftListContainer.Children.Add(card);
                }

                hwRemoteListContainer.Children.Clear();
                foreach (var item in remoteWorks)
                {
                    string title = J.S(item, "title");
                    string courseTitle = J.S(item, "courseTitle");
                    string statusText = J.S(item, "statusText");
                    string timeText = J.S(item, "timeText");
                    string workUrl = J.S(item, "workUrl");
                    string reason = J.S(item, "reason");
                    bool canOpen = J.B(item, "canOpen");

                    var metaParts = new List<string>();
                    if (!string.IsNullOrWhiteSpace(courseTitle)) metaParts.Add(courseTitle);
                    if (!string.IsNullOrWhiteSpace(statusText)) metaParts.Add(statusText);
                    if (!string.IsNullOrWhiteSpace(timeText)) metaParts.Add(timeText);
                    var meta = Text(string.Join(" · ", metaParts), 12, true);

                    var left = Column(Text(title, 15), meta);
                    if (!canOpen && !string.IsNullOrWhiteSpace(reason))
                        left.Children.Add(Text(reason, 12, true));

                    Button actionBtn = canOpen
                        ? Button("读取作业", async () => await Call("loadHomework", workUrl), color: "#2563EB")
                        : Button("打开官方入口", () =>
                        {
                            if (Uri.TryCreate(workUrl, UriKind.Absolute, out var uri) && uri.Scheme is "https" or "http")
                                _ = Windows.System.Launcher.LaunchUriAsync(uri);
                            return Task.CompletedTask;
                        });
                    actionBtn.IsEnabled = !busy;
                    var card = Card(StretchRow(left, actionBtn));
                    card.Padding = new Thickness(14, 10, 14, 10);
                    hwRemoteListContainer.Children.Add(card);
                }
            }
            if (hwDraftsHeader != null)
            {
                hwDraftsHeader.Text = $"本机草稿（{drafts.Count}）";
                hwDraftsHeader.Visibility = drafts.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            }
            if (hwRemoteHeader != null)
            {
                hwRemoteHeader.Text = $"账号作业列表（{remoteWorks.Count}）";
                hwRemoteHeader.Visibility = remoteWorks.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            }
            if (hwEmptyText != null)
                hwEmptyText.Visibility = drafts.Count == 0 && remoteWorks.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            return;
        }

        if (hwEditorNotice != null)
        {
            hwEditorNotice.Message = fbMsg;
            hwEditorNotice.Severity = fbSeverity;
            hwEditorNotice.IsOpen = !string.IsNullOrWhiteSpace(fbMsg);
        }
        if (hwEditorTitle != null) hwEditorTitle.Text = J.S(current, "title");
        if (hwEditorAccount != null) hwEditorAccount.Text = J.S(current, "accountLabel");
        if (hwStateText != null)
            hwStateText.Text = J.B(current, "dirty") ? "有修改 · 未保存到本机" : "已保存到本机";

        var questions = J.A(current["questions"]).OfType<JsonObject>().ToList();
        if (questions.Count == 0) return;
        hwCurrentQuestionIndex = Math.Clamp(hwCurrentQuestionIndex, 0, questions.Count - 1);
        if (hwProgressText != null)
            hwProgressText.Text = $"已答 {J.I(current, "answeredCount")} / {J.I(current, "supportedCount")} 题（共 {questions.Count} 题）";

        if (hwQuestionNumberGrid != null)
        {
            hwQuestionNumberGrid.Children.Clear();
            hwQuestionNumberGrid.ColumnDefinitions.Clear();
            hwQuestionNumberGrid.RowDefinitions.Clear();
            const int cols = 4;
            for (int c = 0; c < cols; c++)
                hwQuestionNumberGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            for (int r = 0; r < (questions.Count + cols - 1) / cols; r++)
                hwQuestionNumberGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            for (int i = 0; i < questions.Count; i++)
            {
                var q = questions[i];
                int idx = i;
                bool isCur = i == hwCurrentQuestionIndex;
                bool sup = J.B(q, "supported");
                bool answered = sup && IsHomeworkQuestionAnswered(q);
                string label = sup ? $"{J.I(q, "no")} · {(answered ? "已答" : "未答")}" : $"{J.I(q, "no")} · 跳过";
                var btn = new Button
                {
                    Content = label,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    Padding = new Thickness(4, 6, 4, 6),
                    BorderThickness = new Thickness(isCur ? 2 : 1),
                    BorderBrush = ThemeBrush(isCur ? "AccentBrush" : answered ? "SuccessBrush" : "MutedBrush"),
                };
                AutomationProperties.SetName(btn, $"第 {J.I(q, "no")} 题，{(sup ? (answered ? "已答" : "未答") : "不支持")}");
                btn.Click += (_, _) =>
                {
                    hwCurrentQuestionIndex = idx;
                    hwRenderedQuestionKey = "";
                    RefreshHomework();
                };
                hwQuestionNumberGrid.Children.Add(btn);
                Grid.SetRow(btn, i / cols);
                Grid.SetColumn(btn, i % cols);
            }
        }

        var curQ = questions[hwCurrentQuestionIndex];
        string qid = J.S(curQ, "id");
        string renderKey = $"{J.S(current, "draftId")}:{qid}:{busy}";
        string answer = curQ["answer"]?.ToJsonString() ?? "";
        bool typing = !busy && Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(Root.XamlRoot) is TextBox box &&
            hwQuestionDetailPanel?.Children.Contains(box) == true;
        if (renderKey != hwRenderedQuestionKey || (answer != hwRenderedAnswer && !typing))
        {
            hwRenderedQuestionKey = renderKey;
            RenderHomeworkQuestion(curQ, !busy);
        }
        hwRenderedAnswer = answer;
        if (hwQuestionSource != null)
        {
            string source = J.S(curQ, "sourceText");
            string reason = J.S(curQ, "reason");
            hwQuestionSource.Text = reason.Length == 0 ? $"来源：{source}" : $"来源：{source} · {reason}";
            hwQuestionSource.Visibility = source.Length + reason.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        }

        var assist = hw["assist"] as JsonObject ?? new JsonObject();
        bool assistEnabled = J.B(assist, "enabled");
        bool assistRunning = J.B(assist, "running");
        if (hwAssistActions != null)
            hwAssistActions.Visibility = assistEnabled ? Visibility.Visible : Visibility.Collapsed;
        if (hwPrevButton != null) hwPrevButton.IsEnabled = hwCurrentQuestionIndex > 0;
        if (hwNextButton != null) hwNextButton.IsEnabled = hwCurrentQuestionIndex < questions.Count - 1;
        if (hwSaveButton != null) hwSaveButton.IsEnabled = !busy;
        if (hwSavePlatformButton != null)
        {
            hwSavePlatformButton.Visibility = J.B(current, "canSaveToPlatform") ? Visibility.Visible : Visibility.Collapsed;
            hwSavePlatformButton.IsEnabled = !busy;
        }
        if (hwCopyButton != null) hwCopyButton.IsEnabled = !busy;
        if (hwExportButton != null) hwExportButton.IsEnabled = !busy;
        if (hwAssistAllButton != null)
        {
            hwAssistAllButton.Visibility = assistEnabled && !assistRunning ? Visibility.Visible : Visibility.Collapsed;
            hwAssistAllButton.IsEnabled = !busy;
        }
        if (hwAssistOneButton != null)
        {
            hwAssistOneButton.Visibility = assistEnabled && !assistRunning ? Visibility.Visible : Visibility.Collapsed;
            hwAssistOneButton.IsEnabled = !busy && J.B(curQ, "supported");
        }
        if (hwAssistCancelButton != null)
            hwAssistCancelButton.Visibility = assistRunning ? Visibility.Visible : Visibility.Collapsed;
        if (hwAssistStatusText != null)
            hwAssistStatusText.Text = assistRunning
                ? $"正在获取参考答案 {J.I(assist, "completed")}/{J.I(assist, "total")}…"
                : "";

    }

    private void RenderHomeworkQuestion(JsonObject q, bool editable)
    {
        if (hwQuestionDetailPanel == null) return;
        hwPopulatingQuestion = true;
        try
        {
            hwQuestionDetailPanel.Children.Clear();
            hwQuestionSource = null;
            string qid = J.S(q, "id");
            int no = J.I(q, "no");
            string qtype = J.S(q, "type");
            string typeName = J.S(q, "typeName");
            hwQuestionDetailPanel.Children.Add(Text($"第 {no} 题 · {typeName}", 13, true));

            var titleTb = Text(J.S(q, "title"), 15);
            titleTb.IsTextSelectionEnabled = true;
            hwQuestionDetailPanel.Children.Add(titleTb);

            foreach (var imgNode in J.A(q["images"]))
                AddPaperImage(hwQuestionDetailPanel, imgNode?.ToString() ?? "", "图片已不可用，请重新读取作业链接。");

            if (!J.B(q, "supported") || qtype == "unsupported")
            {
                hwQuestionDetailPanel.Children.Add(new InfoBar
                {
                    IsOpen = true,
                    IsClosable = false,
                    Severity = InfoBarSeverity.Warning,
                    Message = J.S(q, "unsupportedReason"),
                });
                return;
            }

            var ans = q["answer"] as JsonObject ?? new JsonObject();
            var selected = J.A(ans["option_ids"]).Select(n => n?.ToString() ?? "").ToHashSet();

            if (qtype is "single" or "judgement")
            {
                foreach (var optNode in J.A(q["options"]))
                {
                    if (optNode is not JsonObject opt) continue;
                    string oid = J.S(opt, "id");
                    string otext = J.S(opt, "text");
                    var rb = new RadioButton
                    {
                        GroupName = $"hw_q_{qid}",
                        Content = qtype == "judgement" ? otext : $"{oid}. {otext}",
                        IsChecked = selected.Contains(oid),
                        IsEnabled = editable,
                    };
                    AutomationProperties.SetName(rb, $"第 {no} 题选项 {oid} {otext}");
                    rb.Checked += async (_, _) =>
                    {
                        if (!hwPopulatingQuestion)
                            await Call("setHomeworkAnswer", qid, new JsonObject { ["option_ids"] = new JsonArray(oid) });
                    };
                    hwQuestionDetailPanel.Children.Add(rb);
                }
            }
            else if (qtype == "multiple")
            {
                var checks = new List<(string Id, CheckBox Box)>();
                foreach (var optNode in J.A(q["options"]))
                {
                    if (optNode is not JsonObject opt) continue;
                    string oid = J.S(opt, "id");
                    string otext = J.S(opt, "text");
                    var cb = new CheckBox
                    {
                        Content = $"{oid}. {otext}",
                        IsChecked = selected.Contains(oid),
                        IsEnabled = editable,
                    };
                    AutomationProperties.SetName(cb, $"第 {no} 题多选 {oid} {otext}");
                    checks.Add((oid, cb));
                    hwQuestionDetailPanel.Children.Add(cb);
                }
                foreach (var (_, cb) in checks)
                {
                    async void OnToggle(object s, RoutedEventArgs e)
                    {
                        if (hwPopulatingQuestion) return;
                        var arr = new JsonArray();
                        foreach (var (id, box) in checks)
                            if (box.IsChecked == true) arr.Add(id);
                        await Call("setHomeworkAnswer", qid, new JsonObject { ["option_ids"] = arr });
                    }
                    cb.Checked += OnToggle;
                    cb.Unchecked += OnToggle;
                }
            }
            else if (qtype == "completion")
            {
                int count = Math.Max(1, J.I(q, "blankCount"));
                var existing = J.A(ans["blanks"]).Select(n => n?.ToString() ?? "").ToList();
                var boxes = new List<TextBox>();
                for (int b = 0; b < count; b++)
                {
                    var tb = new TextBox
                    {
                        Header = $"第 {b + 1} 空",
                        Text = b < existing.Count ? existing[b] : "",
                        IsReadOnly = !editable,
                    };
                    AutomationProperties.SetName(tb, $"第 {no} 题第 {b + 1} 空");
                    boxes.Add(tb);
                    hwQuestionDetailPanel.Children.Add(tb);
                }
                foreach (var tb in boxes)
                {
                    tb.TextChanged += async (_, _) =>
                    {
                        if (hwPopulatingQuestion || !editable) return;
                        var arr = new JsonArray();
                        foreach (var box in boxes) arr.Add(box.Text ?? "");
                        await Call("setHomeworkAnswer", qid, new JsonObject { ["blanks"] = arr });
                    };
                }
            }
            else if (qtype == "shortanswer")
            {
                var tb = new TextBox
                {
                    Header = "简答草稿",
                    AcceptsReturn = true,
                    TextWrapping = TextWrapping.Wrap,
                    MinHeight = 140,
                    Text = J.S(ans, "text"),
                    IsReadOnly = !editable,
                };
                AutomationProperties.SetName(tb, $"第 {no} 题简答草稿");
                tb.TextChanged += async (_, _) =>
                {
                    if (!hwPopulatingQuestion && editable)
                        await Call("setHomeworkAnswer", qid, new JsonObject { ["text"] = tb.Text ?? "" });
                };
                hwQuestionDetailPanel.Children.Add(tb);
            }

            hwQuestionSource = Text("", 12, true);
            hwQuestionDetailPanel.Children.Add(hwQuestionSource);
        }
        finally
        {
            hwPopulatingQuestion = false;
        }
    }

    private static bool IsHomeworkQuestionAnswered(JsonObject q)
    {
        var ans = q["answer"] as JsonObject;
        if (ans == null) return false;
        string qtype = J.S(q, "type");
        if (qtype is "single" or "multiple" or "judgement")
            return J.A(ans["option_ids"]).Any();
        if (qtype == "completion")
        {
            var blanks = J.A(ans["blanks"]).ToList();
            return blanks.Count > 0 && blanks.All(b => !string.IsNullOrWhiteSpace(b?.ToString()));
        }
        return !string.IsNullOrWhiteSpace(J.S(ans, "text"));
    }

    private async Task CloseHomeworkViewAsync()
    {
        var res = await Call("closeHomework", false);
        if (res?.ToString() == "confirm")
        {
            if (await Confirm("放弃未保存的作业修改？", "当前作业草稿尚有未保存到本机的修改，返回列表将放弃这些修改。", "放弃修改"))
                await Call("closeHomework", true);
        }
    }

    private async Task RequestHomeworkAssistAsync(string scope)
    {
        var preview = await Call("requestHomeworkSuggestions", scope, false, false) as JsonObject;
        if (preview == null) return;
        if (scope != "all" && J.B(preview, "replaceManualConfirm"))
        {
            if (!await Confirm("替换当前手动答案？", "用新获取的参考答案替换当前手动答案？（仅写入本机草稿）", "替换"))
                return;
        }
        else if (scope == "all")
        {
            string summary = $"即将使用以下服务为 {J.I(preview, "questionCount")} 道题生成参考答案（仅填入本机草稿，不向平台提交）：\n"
                + $"• AI 服务：{J.S(preview, "aiSummary")}\n"
                + $"• 题库：{J.S(preview, "bankSummary")}\n"
                + $"• 含图题数：{J.I(preview, "imageCount")}";
            if (!await Confirm("获取作业参考答案", summary, "开始获取"))
                return;
        }
        await Call("requestHomeworkSuggestions", scope, true, false);
    }

    private async Task RequestHomeworkAssistSingleAsync()
    {
        var hw = backend.State["homework"] as JsonObject ?? new JsonObject();
        var current = hw["current"] as JsonObject ?? new JsonObject();
        var questions = J.A(current["questions"]).OfType<JsonObject>().ToList();
        if (hwCurrentQuestionIndex < 0 || hwCurrentQuestionIndex >= questions.Count) return;
        await RequestHomeworkAssistAsync(J.S(questions[hwCurrentQuestionIndex], "id"));
    }

    private async Task CopyHomeworkDraftAsync()
    {
        var res = await Call("exportHomeworkDraft");
        string text = res?.ToString() ?? "";
        if (string.IsNullOrEmpty(text)) return;
        var pkg = new DataPackage();
        pkg.SetText(text);
        Clipboard.SetContent(pkg);
    }

    private async Task SaveAnswerTextAsync(string command, string suggestedFileName)
    {
        var res = await Call(command);
        string text = res?.ToString() ?? "";
        if (string.IsNullOrEmpty(text)) return;
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add("文本文件", [".txt"]);
        picker.SuggestedFileName = suggestedFileName;
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var file = await picker.PickSaveFileAsync();
        if (file != null)
            await File.WriteAllTextAsync(file.Path, text);
    }
}
