using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using System.Text.Json.Nodes;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private string? smokeDestination;
    private bool smokeStarted;
    private string smokeStep = "startup";
    private void ConfigureSmoke()
    {
        var args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, "--smoke-test");
        if (index >= 0 && index + 1 < args.Length) smokeDestination = Path.GetFullPath(args[index + 1]);
    }
    private async Task WaitFor(Func<bool> predicate, int seconds = 20)
    {
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (!predicate()) { if (DateTime.UtcNow > deadline) throw new TimeoutException($"离线界面验收超时：step={smokeStep}, page={Navigation.SelectedIndex}, ready={CoursesReady}, coursesShown={showingMyCourses}, pending={coursesLoadedPending}, animations={contentAnimations.Count}, busy={Busy}, message={J.S(Summary, "message")}。"); await Task.Delay(50); }
    }

    /// <summary>Perform a real control click through the automation invoke pattern.</summary>
    private static async Task Click(Button button)
    {
        var peer = FrameworkElementAutomationPeer.CreatePeerForElement(button)
                   ?? new ButtonAutomationPeer(button);
        if (peer.GetPattern(PatternInterface.Invoke) is not IInvokeProvider invoke)
            throw new InvalidOperationException("续跑按钮无法通过界面自动化点击。");
        invoke.Invoke();
        await Task.CompletedTask;
    }
    private async Task RunSmoke()
    {
        if (smokeDestination == null || smokeStarted) return; smokeStarted = true;
        bool success = false; string message;
        try
        {
            // The packaging harness starts the process hidden. ScrollViewer's
            // animated ChangeView needs rendered frames, so expose the test
            // window before exercising interactive layout and scrolling.
            AppWindow.Show();
            Activate();
            await Task.Delay(100);
            if (pages.Count != 5 || fields.Count < 40) throw new InvalidOperationException("页面或设置表单未加载。");
            await CheckSettingsReviewRegressions();
            await CheckBankHealthReviewRegression();
            await CheckModalReviewRegressions();
            await CheckScopeResetDetailRace();
            await CheckNativeDialogControls();
            await CheckThemes();
            ShowCourseView(true, animate: false);
            if (showingMyCourses || CoursesReady || courseViews[1].Visibility != Visibility.Collapsed)
                throw new InvalidOperationException("未登录时不应进入我的课程。");
            Root.UpdateLayout();
            if (accountConnectionCard == null) throw new InvalidOperationException("账号面板未加载。");
            var accountPosition = accountConnectionCard.TransformToVisual(courseViews[0]).TransformPoint(new Windows.Foundation.Point(0, 0));
            double accountRight = courseViews[0].ActualWidth - accountPosition.X - accountConnectionCard.ActualWidth;
            if (accountPosition.X < -1 || accountRight < -1 || Math.Abs(accountPosition.X - accountRight) > 3)
                throw new InvalidOperationException("账号面板没有居中或超出可见区域。");
            if (fields["common.username"].ActualHeight < 20 || fields["common.password"].ActualHeight < 20)
                throw new InvalidOperationException("账户详情未直接显示。");
            var cookieLogin = VisualChild<Button>(accountConnectionCard, "SavedCookieLogin") ?? throw new InvalidOperationException("保存的 Cookie 缺少独立登录入口。");
            var summary = (JsonObject)Summary!;
            bool cookieSaved = J.B(summary, "cookie");
            try
            {
                summary["cookie"] = true; Refresh(new JsonObject()); Root.UpdateLayout();
                if (cookieLogin.Visibility != Visibility.Visible || !cookieLogin.Content.ToString()!.Contains("已保存 Cookie"))
                    throw new InvalidOperationException("已保存 Cookie 没有显示直接进入课程的按钮。");
                summary["cookie"] = false; Refresh(new JsonObject()); Root.UpdateLayout();
                if (cookieLogin.Visibility != Visibility.Collapsed)
                    throw new InvalidOperationException("尚未保存 Cookie 时显示了无效的登录入口。");
            }
            finally { summary["cookie"] = cookieSaved; Refresh(new JsonObject()); }
            var originalAccounts = backend.State["accountRows"]?.DeepClone();
            string originalActive = J.S(summary, "activeAccount");
            try
            {
                backend.State["accountRows"] = new JsonArray(
                    new JsonObject { ["id"] = "note-a", ["label"] = "学习账号 A", ["username"] = "user-a", ["display"] = "学习账号 A" },
                    new JsonObject { ["id"] = "note-b", ["label"] = "学习账号 B", ["username"] = "user-b", ["display"] = "学习账号 B" });
                var note = VisualChild<TextBox>(accountConnectionCard, "AccountNote")!;
                var target = VisualChild<TextBlock>(accountConnectionCard, "AccountNoteTarget")!;
                foreach (string suffix in new[] { "a", "b" })
                {
                    summary["activeAccount"] = "note-" + suffix; Refresh(new JsonObject());
                    if (!target.Text.Contains("user-" + suffix) || note.Text != "学习账号 " + suffix.ToUpperInvariant())
                        throw new InvalidOperationException("切换账号后备注归属或已有备注没有同步。");
                    note.Text = "尚未保存的备注";
                }
                summary["activeAccount"] = ""; Refresh(new JsonObject());
                if (note.IsEnabled || note.Text.Length > 0) throw new InvalidOperationException("未选择账号时不应编辑备注。");
            }
            finally { backend.State["accountRows"] = originalAccounts; summary["activeAccount"] = originalActive; Refresh(new JsonObject()); }
            for (int i = 0; i < pages.Count; i++)
            {
                Navigation.SelectedIndex = i; Root.UpdateLayout(); await Task.Delay(80);
                if (pages[i].ActualWidth < 100 || pages[i].ActualHeight < 100) throw new InvalidOperationException("页面布局异常：" + Titles[i]);
            }
            foreach (int index in new[] { 4, 1, 3, 0, 2 })
            {
                Navigation.SelectedIndex = index;
                await Task.Delay(25);
            }
            await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
            if (pages.Count(p => p.Visibility == Visibility.Visible) != 1 || pages[2].Visibility != Visibility.Visible)
                throw new InvalidOperationException("连续导航没有停在最后选择的页面。");
            MoveCategoryIndicator(false);
            if (CategoryIndicator.Opacity != 1) throw new InvalidOperationException("侧栏指示条未显示。");
            foreach (var expected in new[] { ("success", "#15803D"), ("pending", "#B45309"), ("failed", "#DC2626") })
                if (chapterStatValues[expected.Item1].Foreground is not Microsoft.UI.Xaml.Media.SolidColorBrush brush || brush.Color != ParseColor(expected.Item2))
                    throw new InvalidOperationException("章节统计颜色不正确。");
            await CheckResponsiveServices();
            Navigation.SelectedIndex = 0;
            await Call("loadDemo");
            await WaitFor(() => !Busy && J.I(Summary, "courseCount") == 3 && courses?.Items.Count == 3);
            if (courses!.Items.OfType<RecordRow>().Any(row => !row.Title.Contains("演示")))
                throw new InvalidOperationException("冻结后台的中文课程名称未正确传输。");
            await WaitFor(() => showingMyCourses && courseHost != null && !contentAnimations.ContainsKey(courseHost));
            if (courseViews[0].Visibility != Visibility.Collapsed || PageTitle.Text != "我的课程")
                throw new InvalidOperationException("登录后未切换到独立课程视图。");
            ShowCourseView(false);
            await WaitFor(() => courseHost != null && !contentAnimations.ContainsKey(courseHost));
            if (showingMyCourses || PageTitle.Text != "账号连接") throw new InvalidOperationException("无法返回账号管理。");
            ShowCourseView(true);
            await WaitFor(() => courseHost != null && !contentAnimations.ContainsKey(courseHost));
            await Call("selectVisible", true);
            await WaitFor(() => J.I(Summary, "selectedCount") == 3);
            await Call("startTasks");
            await WaitFor(() => !Busy && resultLists["tasks"].Items.Count > 0, 40);
            if (J.I(Summary, "total") == 0) throw new InvalidOperationException("章节任务没有同步。");
            await CheckMonitorStability();
            await CheckRecordDetails();
            await CheckResultFiltersAndDiagnostics();
            await CheckRowClickAfterReset();
            await CheckAccountSwitchScopeReset();
            await CheckOfflineStartScopeReset();
            await CheckResumeEntry();
            var discussion = J.A(backend.State["discussions"]).FirstOrDefault();
            if (discussion != null)
            {
                var shown = ShowRecord("discussions", discussion);
                try
                {
                    await WaitFor(() => currentDialog?.IsLoaded == true);
                    var body = ((ScrollViewer)currentDialog!.Content).Content as TextBlock;
                    if (body == null || !body.Text.Contains("处理阶段：") || !body.Text.Contains("任务点状态：") ||
                        new[] { "id：", "run_id", "task_id", "elapsed_ms", "level:", "stage:" }.Any(body.Text.Contains))
                        throw new InvalidOperationException("讨论详情泄露内部字段或缺少中文状态。");
                }
                finally { currentDialog?.Hide(); await shown; }
            }
            if ((await Call("save"))?.ToString() != "true") throw new InvalidOperationException("设置未能保存。");
            // Also close during an active offline worker, using the normal save
            // handshake and hide path rather than merely terminating the app.
            await Call("startTasks");
            var closeTimer = System.Diagnostics.Stopwatch.StartNew();
            if ((await Call("close"))?.ToString() != "true") throw new InvalidOperationException("退出保存未完成。");
            HideForClose();
            if (AppWindow.IsVisible || closeTimer.Elapsed > TimeSpan.FromSeconds(1))
                throw new InvalidOperationException("关闭窗口仍在等待后台清理。");
            success = true; message = "WinUI 3 系统/浅色/深色主题、主题保存、交互动效、宽窄布局和离线任务验收通过。";
        }
        catch (Exception e) { message = e.Message; }
        Directory.CreateDirectory(smokeDestination);
        await File.WriteAllTextAsync(Path.Combine(smokeDestination, "result.json"), JsonSerializer.Serialize(new { success, message, frontend = "WinUI 3", animationsEnabled = motionSettings.AnimationsEnabled, pages = pages.Count, fields = fields.Count, courses = courses?.Items.Count, tasks = resultLists.GetValueOrDefault("tasks")?.Items.Count }));
        Environment.ExitCode = success ? 0 : 1;
        if (!closing) await Call("discardAndClose");
        await FinishCloseAsync();
    }

private static T? FindDescendant<T>(DependencyObject root, Func<T, bool> match) where T : FrameworkElement
    {
        int count = VisualTreeHelper.GetChildrenCount(root);
        for (int i = 0; i < count; i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T typed && match(typed)) return typed;
            if (FindDescendant(child, match) is { } nested) return nested;
        }
        return null;
    }

    // Exercises the real completion dialog and the start-confirmation dialog
    // with native button clicks, then records which RPCs the product's own
    // handlers issued. The prompts are injected locally (no platform call);
    // only the buttons, handlers and RPC path are under test.
    private async Task CheckNativeDialogControls()
    {
        smokeStep = "native-dialogs";
        var report = new Dictionary<string, object>();
        bool originalIntercept = smokeInterceptInput;
        var originalInput = backend.State["input"]?.DeepClone();
        string originalSignature = inputSignature;
        try
        {
            smokeInterceptInput = true;

            // 1) Completion prompt: incomplete primary press must stay open,
            //    a complete press must reply with the typed answer, and the
            //    skip button must reply empty without reusing the answer.
            for (int pass = 0; pass < 2; pass++)
            {
                smokeCalls.Clear();
                var input = new JsonObject
                {
                    ["title"] = "合成人工补全", ["type"] = "text", ["kind"] = "text",
                    ["message"] = $"合成题目（第 {pass + 1} 轮）：请填写答案。", ["options"] = new JsonArray(),
                };
                backend.State["input"] = input.DeepClone();
                RefreshInput();
                await WaitFor(() => manualDialog?.IsLoaded == true, 10);
                var dialog = manualDialog!;
                var answer = FindDescendant<TextBox>(dialog, t => t.PlaceholderText == "填写答案")
                             ?? throw new InvalidOperationException("人工补全缺少答案输入框。");
                var use = FindDescendant<Button>(dialog, b => b.Content?.ToString() == "使用答案")
                          ?? throw new InvalidOperationException("人工补全缺少「使用答案」按钮。");
                var skip = FindDescendant<Button>(dialog, b => b.Content?.ToString() == "跳过此题")
                           ?? throw new InvalidOperationException("人工补全缺少「跳过此题」按钮。");
                if (pass == 0)
                {
                    await Click(use);
                    await Task.Delay(150);
                    if (smokeCalls.Count != 0 || manualDialog != dialog || !dialog.IsLoaded)
                        throw new InvalidOperationException("空白答案竟提交或关闭了人工补全。");
                    answer.Text = "合成答案";
                    await Click(use);
                    await WaitFor(() => smokeCalls.Count > 0, 10);
                    if (smokeCalls[0] != "reply 合成答案|False")
                        throw new InvalidOperationException("「使用答案」未提交填写的答案：" + smokeCalls[0]);
                    report["completionPrimary"] = smokeCalls.ToArray();
                }
                else
                {
                    answer.Text = "不应提交的答案";
                    await Click(skip);
                    await WaitFor(() => smokeCalls.Count > 0, 10);
                    if (smokeCalls[0] != "reply |False")
                        throw new InvalidOperationException("「跳过此题」提交了答案而非空答案：" + smokeCalls[0]);
                    report["completionSkip"] = smokeCalls.ToArray();
                }
                // Closing follows the product's own ClearInput -> inputChanged path.
                backend.State["input"] = new JsonObject();
                RefreshInput();
                await WaitFor(() => manualDialog == null, 10);
            }

            // 2) Start confirmation: the production handler builds and waits on
            //    a real dialog; its primary and cancel buttons must reach the
            //    matching backend commands.
            foreach (var (label, expected) in new[] { ("按此策略开始", "confirmStartTasks"), ("取消", "cancelStartTasks") })
            {
                smokeCalls.Clear();
                HandleEvent("startConfirmationRequested", new JsonArray(J.S(backend.State, "strategySummary", "合成策略摘要")));
                await WaitFor(() => currentDialog?.IsLoaded == true && currentDialog.Title?.ToString() == "确认本轮提交策略", 10);
                var button = FindDescendant<Button>(currentDialog!, b => b.Content?.ToString() == label)
                             ?? throw new InvalidOperationException("启动确认缺少「" + label + "」按钮。");
                await Click(button);
                await WaitFor(() => smokeCalls.Count > 0, 10);
                if (smokeCalls[0].TrimEnd() != expected)
                    throw new InvalidOperationException($"「{label}」应发送 {expected}，实际 {smokeCalls[0]}。");
                report[expected] = smokeCalls.ToArray();
                await WaitFor(() => currentDialog == null, 10);
            }
            await WaitFor(() => !processingDialogs && dialogs.Count == 0, 10);
            await File.WriteAllTextAsync(Path.Combine(smokeDestination!, "native-dialogs.json"),
                JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        }
        finally
        {
            smokeInterceptInput = originalIntercept;
            inputSignature = originalSignature;
            backend.State["input"] = originalInput;
            manualDialog?.Hide();
            if (currentDialog != null) currentDialog.Hide();
            await WaitFor(() => !processingDialogs, 10);
        }
    }

    private async Task CheckResultFiltersAndDiagnostics()
    {
        smokeStep = "result-filters";
        // Chinese labels must map to the exact internal keys the backend filters on.
        (string Name, string[] Labels, string[] Values)[] combos =
        [
            ("attentionStages", ["全部需处理", "平台待确认", "提交未知", "失败／跳过", "草稿／人工"], ["", "platform", "unknown", "failed", "manual"]),
            ("discussionsStages", ["全部记录", "本地草稿", "已发布", "本人已回复", "发布未知", "待处理"], ["", "draft", "published", "existing", "publish_unknown", "pending"]),
            ("activitiesStages", ["全部记录", "本地草稿", "已确认提交", "本人已参与", "提交未知", "待处理", "平台拒绝"], ["", "draft", "submitted", "existing", "submit_unknown", "pending", "rejected"]),
        ];
        foreach (var (name, labels, values) in combos)
        {
            var combo = VisualChild<ComboBox>(pages[4], name) ?? throw new InvalidOperationException("结果筛选缺少 " + name + " 下拉。");
            if (combo.DisplayMemberPath != "Label") throw new InvalidOperationException(name + " 未使用中文标签。");
            var items = combo.Items.Cast<object>().ToArray();
            for (int i = 0; i < labels.Length; i++)
            {
                var label = items[i].GetType().GetProperty("Label")?.GetValue(items[i])?.ToString();
                var value = items[i].GetType().GetProperty("Value")?.GetValue(items[i])?.ToString();
                if (label != labels[i] || value != values[i])
                    throw new InvalidOperationException($"{name} 第 {i} 项应为 {labels[i]}/{values[i]}，实际 {label}/{value}。");
            }
        }

        // A chapter row must reach that chapter's question results in one step.
        var tasks = resultLists["tasks"];
        if (tasks.Items.Count == 0) throw new InvalidOperationException("章节任务为空，无法验证逐题入口。");
        tasks.ScrollIntoView(tasks.Items[0]);
        await WaitFor(() => tasks.ContainerFromIndex(0) != null);
        var container = (FrameworkElement)tasks.ContainerFromIndex(0)!;
        var jump = FindDescendant<Button>(container, button => button.Content?.ToString() == "题目")
                   ?? throw new InvalidOperationException("章节行缺少「题目」入口。");
        string chapterId = J.S(((RecordRow)tasks.Items[0]).Data, "id");
        await Click(jump);
        if (questionTask != chapterId || resultTabs?.SelectedIndex != questionsTabIndex)
            throw new InvalidOperationException("章节行未能定位该章的逐题结果。");
        var scope = VisualChild<TextBlock>(pages[4], "QuestionScope") ?? throw new InvalidOperationException("逐题页缺少筛选范围提示。");
        var clear = VisualChild<Button>(pages[4], "QuestionScopeClear") ?? throw new InvalidOperationException("逐题页缺少清除筛选按钮。");
        if (scope.Text != "已筛选章节" || clear.Visibility != Visibility.Visible)
            throw new InvalidOperationException("章节筛选未显示为已筛选状态。");
        await Click(clear);
        if (questionTask.Length != 0 || scope.Text != "全部章节" || clear.Visibility != Visibility.Collapsed)
            throw new InvalidOperationException("清除章节筛选后未恢复全部章节。");

        // A hand-typed chapter id is the same scope as the list button, and
        // clearing it clears the text too, so no stale filter is left behind.
        var typed = FindDescendant<TextBox>(pages[4], box => box.PlaceholderText == "按章节任务 ID 筛选")!;
        typed.Text = "typed-task";
        await WaitFor(() => questionTask == "typed-task");
        if (scope.Text != "已筛选章节" || clear.Visibility != Visibility.Visible)
            throw new InvalidOperationException("手输章节任务 ID 未成为筛选范围。");
        await Click(clear);
        await WaitFor(() => typed.Text.Length == 0);
        if (questionTask.Length != 0 || scope.Text != "全部章节")
            throw new InvalidOperationException("清除章节筛选未清掉手输的任务 ID。");

        // The scope reset clears the retained filters and closes a stale detail,
        // while the queued manual-answer dialog keeps its own gate.
        var pending = VisualChild<CheckBox>(pages[4], "QuestionPending")!;
        var search = VisualChild<TextBox>(pages[4], "筛选章节任务 ID") ?? FindDescendant<TextBox>(pages[4], box => box.PlaceholderText == "按章节任务 ID 筛选")!;
        var stages = VisualChild<ComboBox>(pages[4], "discussionsStages")!;
        pending.IsChecked = true; search.Text = chapterId; stages.SelectedIndex = 2;
        var chapter = new JsonObject
        {
            ["title"] = "1.2 大学体育课程学习的规定", ["course"] = "大学体育", ["kind"] = "章节",
            ["status"] = "success", ["statusText"] = "已完成", ["courseId"] = "10001",
            ["pointId"] = "20001", ["clazzId"] = "15800725",
        };
        var shown = ShowRecord("tasks", chapter);
        await WaitFor(() => currentDialog?.IsLoaded == true);
        HandleEvent("questionsReset", new JsonArray());
        await WaitFor(() => currentDialog == null);
        await shown;
        if (pending.IsChecked == true || search.Text.Length != 0 || stages.SelectedIndex != 0 || questionTask.Length != 0)
            throw new InvalidOperationException("作用域切换未清空结果筛选。");
        if (scope.Text != "全部章节") throw new InvalidOperationException("作用域切换未恢复全部章节。");

        // The question detail must stay readable and copyable, and an evicted
        // record must explain itself instead of showing raw fields.
        var question = new JsonObject
        {
            ["title"] = "合成题目", ["options"] = "A. 甲\nB. 乙", ["answer"] = "B", ["reason"] = "命中题库",
            ["source"] = "ai", ["sourceText"] = "AI", ["match_mode"] = "exact",
            ["elapsed_ms"] = 120, ["ai_elapsed_ms"] = 300, ["manual_elapsed_ms"] = 0,
            ["ai_requests"] = 2, ["ai_retries"] = 1,
            ["attempts"] = new JsonArray(new JsonObject
            {
                ["provider"] = "TikuGo", ["status"] = "ok", ["reason"] = "返回单条候选",
                ["returned_title"] = "合成题目", ["candidate"] = "B", ["elapsed_ms"] = 120,
            }),
        };
        string detail = QuestionDetailText(question);
        foreach (var expected in new[] { "选项：", "A. 甲", "采用答案：", "B", "原因：命中题库", "来源：AI", "匹配：原文匹配",
                     "耗时：题库 120 ms；AI 300 ms；人工 0 ms", "AI 请求 2 次，重试 1 次", "服务记录：", "TikuGo · 120 ms",
                     "返回题干：合成题目", "候选答案：B" })
            if (!detail.Contains(expected)) throw new InvalidOperationException("逐题详情缺少「" + expected + "」。");
        if (QuestionDetailText(new JsonObject()).Contains("attempts"))
            throw new InvalidOperationException("已淘汰记录泄露了内部字段。");
        var questionRow = new JsonObject { ["id"] = "missing-question", ["title"] = "合成题目" };
        var questionShown = ShowRecord("questions", questionRow);
        await WaitFor(() => currentDialog?.IsLoaded == true);
        if (currentDialog!.SecondaryButtonText != "复制详情" || ((ScrollViewer)currentDialog.Content).Content is not TextBlock body ||
            !body.IsTextSelectionEnabled || !body.Text.Contains("该记录已被清除或淘汰"))
            throw new InvalidOperationException("逐题详情不可读或未处理已淘汰记录。");
        currentDialog.Hide();
        await questionShown;

        // The task page must render the diagnostics the backend already sends.
        var summary = (JsonObject)backend.State["summary"]!;
        var original = summary.DeepClone();
        try
        {
            summary["aiUsage"] = new JsonObject { ["requests"] = 3, ["limit"] = 5, ["known"] = 1, ["unknown"] = 1, ["tokens"] = 42 };
            summary["aiWarning"] = "本次操作 AI 请求已达硬上限；后续请求将留待处理。";
            summary["verificationProgress"] = new JsonObject { ["phase"] = "run", ["completed"] = 1, ["total"] = 2, ["reads"] = 7, ["failures"] = 0, ["elapsed_ms"] = 1500 };
            summary["questionCount"] = 3;
            summary["questionStats"] = new JsonObject { ["query_attempts"] = 4, ["requests"] = 5, ["candidates"] = 2, ["resolved"] = 1, ["cache"] = 1, ["ai"] = 1, ["manual"] = 0, ["random"] = 0 };
            Refresh(new JsonObject());
            var usage = VisualChild<TextBlock>(pages[4], "AiUsageText") ?? throw new InvalidOperationException("任务页缺少 AI 用量统计。");
            var warning = VisualChild<TextBlock>(pages[4], "AiWarningText") ?? throw new InvalidOperationException("任务页缺少 AI 警告。");
            var verification = VisualChild<TextBlock>(pages[4], "VerificationText") ?? throw new InvalidOperationException("任务页缺少核验进度。");
            var stats = VisualChild<TextBlock>(pages[4], "questionsStats") ?? throw new InvalidOperationException("逐题页缺少统计行。");
            if (!usage.Text.Contains("本次操作 AI 请求 3 / 5") || !usage.Text.Contains("服务返回 token 42") || !usage.Text.Contains("1 次用量未知"))
                throw new InvalidOperationException("AI 用量统计未按后台数据渲染：" + usage.Text);
            if (warning.Visibility != Visibility.Visible || !warning.Text.Contains("硬上限"))
                throw new InvalidOperationException("AI 警告未显示。");
            if (verification.Visibility != Visibility.Visible || verification.Text != "核验已处理 1/2 章 · 章节／卡片读取 7 · 失败 0 · 2 秒")
                throw new InvalidOperationException("核验进度未按后台数据渲染：" + verification.Text);
            if (!stats.Text.Contains("最近 3 条") || !stats.Text.Contains("题库轮次 4") || !stats.Text.Contains("已采用 1"))
                throw new InvalidOperationException("逐题统计未按后台数据渲染：" + stats.Text);
        }
        finally
        {
            backend.State["summary"] = original!.DeepClone();
            Refresh(new JsonObject());
        }

        // Logs append in place: following keeps the tail, reading up holds the
        // position, and "到底部" restores following.
        var originalLogs = backend.State["logs"]?.DeepClone();
        try
        {
            var expander = FindDescendant<Expander>(pages[4], _ => true) ?? throw new InvalidOperationException("任务页缺少日志面板。");
            expander.IsExpanded = true;
            // The panel content is realised when the expander opens, so the list
            // only exists in the visual tree after that layout pass.
            await WaitFor(() => VisualChild<ListView>(pages[4], "LogList") != null);
            var lines = new JsonArray();
            for (int i = 0; i < 80; i++) lines.Add((i == 2 ? "ERROR " : i == 3 ? "WARNING " : "") + "离线验收日志 " + i);
            backend.State["logs"] = lines;
            Refresh(new JsonObject { ["logs"] = true });
            var list = VisualChild<ListView>(pages[4], "LogList") ?? throw new InvalidOperationException("日志区缺少列表。");
            await WaitFor(() => list.Items.Count == 80);
            var scroller = VisualChild<ScrollViewer>(list) ?? throw new InvalidOperationException("日志列表缺少滚动区。");
            await WaitFor(() => scroller.ScrollableHeight > 0);
            if (logEntries[2].Brush.Color != ThemeBrush("ErrorBrush").Color || logEntries[3].Brush.Color != ThemeBrush("WarningBrush").Color)
                throw new InvalidOperationException("日志级别着色不正确。");
            await Task.Delay(500);
            if (!followLogs || scroller.VerticalOffset < scroller.ScrollableHeight - 2)
                throw new InvalidOperationException("跟随最新时日志未停在底部。");
            scroller.ChangeView(null, 0, null, true);
            await Task.Delay(600);
            if (followLogs || VisualChild<CheckBox>(pages[4], "LogFollow")?.IsChecked != false)
                throw new InvalidOperationException("上滚阅读未取消跟随。");
            lines.Add("ERROR 新日志 A"); lines.Add("WARNING 新日志 B");
            Refresh(new JsonObject { ["logs"] = true });
            await WaitFor(() => list.Items.Count == 82);
            if (scroller.VerticalOffset > 4) throw new InvalidOperationException("新增日志抢走了阅读位置。");
            await Click(VisualChild<Button>(pages[4], "LogBottom") ?? throw new InvalidOperationException("日志区缺少到底部按钮。"));
            await WaitFor(() => scroller.VerticalOffset >= scroller.ScrollableHeight - 2);
            if (!followLogs) throw new InvalidOperationException("到底部未恢复跟随。");
            lines.Add("离线验收日志 82");
            Refresh(new JsonObject { ["logs"] = true });
            await WaitFor(() => list.Items.Count == 83);
            await WaitFor(() => scroller.VerticalOffset >= scroller.ScrollableHeight - 2);

            // A full capacity window rolls: the shown tail becomes the head of
            // the new snapshot. Only the evicted head may go; the retained rows
            // keep their objects and the reader keeps the place.
            var clean = new JsonArray();
            for (int i = 0; i < 80; i++) clean.Add("窗口日志 " + i);
            backend.State["logs"] = clean;
            Refresh(new JsonObject { ["logs"] = true });
            if (list.Items.Count != 80 || logEntries.Count != 80)
                throw new InvalidOperationException("日志窗口重建后行数不符：items=" + list.Items.Count + ", entries=" + logEntries.Count);
            scroller.ChangeView(null, 40, null, true);
            await Task.Delay(600);
            if (followLogs) throw new InvalidOperationException("滚动到日志中段后仍在跟随。");
            double before = scroller.VerticalOffset;
            var retained = logEntries[logEntries.Count - 1];
            var rolled = new JsonArray();
            for (int i = 40; i < 120; i++) rolled.Add("窗口日志 " + i);
            backend.State["logs"] = rolled;
            Refresh(new JsonObject { ["logs"] = true });
            if (logEntries.Count != 80 || logEntries[0].Text != "窗口日志 40")
                throw new InvalidOperationException("滚动窗口未按重叠区淘汰：count=" + logEntries.Count +
                    ", first=" + (logEntries.Count > 0 ? logEntries[0].Text : "-"));
            if (!ReferenceEquals(logEntries[39], retained))
                throw new InvalidOperationException("滚动窗口重建了未淘汰的行对象。");
            if (Math.Abs(scroller.VerticalOffset - before) > 40)
                throw new InvalidOperationException("滚动窗口把阅读位置跳到了底部。");
        }
        finally
        {
            backend.State["logs"] = originalLogs ?? new JsonArray();
            Refresh(new JsonObject { ["logs"] = true });
        }
    }

    private async Task CheckSettingsReviewRegressions()
    {
        var originalValues = backend.State["values"]!.DeepClone();
        try
        {
            foreach (string spelling in new[] { "True", "TRUE", "false", "False" })
            {
                foreach (var pair in fields.Where(p => p.Value is ToggleSwitch))
                {
                    string[] path = pair.Key.Split('.');
                    backend.State["values"]![path[0]]![path[1]] = spelling;
                }
                Refresh(new JsonObject());
                bool expected = spelling.Equals("true", StringComparison.OrdinalIgnoreCase);
                if (fields.Values.OfType<ToggleSwitch>().Any(toggle => toggle.IsOn != expected))
                    throw new InvalidOperationException("布尔配置大小写导致开关显示不一致。");
                if (expected && !fields["common.work_max_retries"].IsEnabled)
                    throw new InvalidOperationException("依赖开关没有接受大写布尔值。");
            }
        }
        finally { backend.State["values"] = originalValues; Refresh(new JsonObject()); }

        string retries = Value("common", "work_max_retries"), redo = Value("common", "work_redo_enabled");
        var edit = (TextBox)fields["common.work_max_retries"];
        try
        {
            await Call("setValue", "common", "work_max_retries", "invalid");
            await Call("setValue", "common", "work_redo_enabled", "false");
            if ((await Call("save"))?.ToString() == "true") throw new InvalidOperationException("无效重试次数未被校验。");
            smokeStep = "correction requested";
            await WaitFor(() => correctingFields.Contains("common.work_max_retries") && edit.IsEnabled);
            await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
            Root.UpdateLayout();
            await Task.Delay(100);
            edit.Text = "";
            smokeStep = "empty edit acknowledged";
            await WaitFor(() => Value("common", "work_max_retries") == "");
            finishCorrections["common.work_max_retries"]();
            if (!edit.IsEnabled) throw new InvalidOperationException("编辑无效依赖字段时被再次禁用。");
            edit.Text = "2";
            smokeStep = "valid edit acknowledged";
            await WaitFor(() => Value("common", "work_max_retries") == "2");
            if (!edit.IsEnabled || (await Call("save"))?.ToString() != "true")
                throw new InvalidOperationException("依赖关闭时无法完成字段修正。");
            SaveButton.Focus(FocusState.Programmatic);
            // Hosted Windows runners need not grant foreground focus. Exercise
            // the same editing-finished handler without requiring OS focus.
            finishCorrections["common.work_max_retries"]();
            smokeStep = "correction finished";
            await WaitFor(() => !correctingFields.Contains("common.work_max_retries"));
            Refresh(new JsonObject());
            if (edit.IsEnabled) throw new InvalidOperationException("合法修正失焦后未恢复依赖禁用规则。");
        }
        finally
        {
            await Call("setValue", "common", "work_max_retries", retries);
            await Call("setValue", "common", "work_redo_enabled", redo);
            correctingFields.Remove("common.work_max_retries");
            Refresh(new JsonObject());
        }

        string speed = Value("common", "speed");
        var speedEdit = (TextBox)fields["common.speed"];
        try
        {
            await Call("setValue", "common", "speed", "invalid");
            if ((await Call("save"))?.ToString() == "true") throw new InvalidOperationException("无效倍速未被校验。");
            smokeStep = "independent field correction requested";
            await WaitFor(() => correctingFields.Contains("common.speed") && !contentAnimations.ContainsKey(PageHost));
            speedEdit.Text = "1.5";
            await WaitFor(() => Value("common", "speed") == "1.5");
            if ((await Call("save"))?.ToString() != "true") throw new InvalidOperationException("普通字段修正后无法保存。");
            Navigation.SelectedIndex = 0;
            await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
            finishCorrections["common.speed"]();
            if (correctingFields.Contains("common.speed")) throw new InvalidOperationException("普通字段纠错状态未清除。");
            string path = Path.Combine(smokeDestination!, "review-import.ini");
            await File.WriteAllTextAsync(path, "[common]\nspeed=2\n");
            await backend.Call("fileAction", ["import_config"], path);
            smokeStep = "independent field refreshed after import";
            await WaitFor(() => Value("common", "speed") == "2" && speedEdit.Text == "2");
        }
        finally
        {
            correctingFields.Remove("common.speed");
            await Call("setValue", "common", "speed", speed);
            Refresh(new JsonObject());
        }
    }

    private async Task CheckBankHealthReviewRegression()
    {
        Navigation.SelectedIndex = 2;
        await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
        Root.UpdateLayout();
        var originalProviders = backend.State["providers"]!.DeepClone();
        var originalHealth = backend.State["serviceHealth"]!.DeepClone();
        try
        {
            foreach (var provider in J.A(backend.State["providers"]))
            {
                string key = J.S(provider, "key");
                provider["health"] = "旧连接检查成功";
                backend.State["serviceHealth"]![key] = "配置已变化 · 上次测试已过期";
            }
            Refresh(new JsonObject());
            foreach (var provider in J.A(backend.State["providers"]))
            {
                string key = J.S(provider, "key");
                var health = VisualChild<TextBlock>(pages[2], key + "Health");
                if (health == null || health.Text != J.S(backend.State["serviceHealth"], key))
                    throw new InvalidOperationException("题库配置变更后仍显示旧检测结果：" + key);
            }
        }
        finally
        {
            backend.State["providers"] = originalProviders;
            backend.State["serviceHealth"] = originalHealth;
            Refresh(new JsonObject());
        }
    }

    private static T? VisualChild<T>(DependencyObject root, string? name = null) where T : FrameworkElement
    {
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T match && (name == null || match.Name == name)) return match;
            if (VisualChild<T>(child, name) is { } nested) return nested;
        }
        return null;
    }

    private async Task CheckThemes()
    {
        Navigation.SelectedIndex = 3;
        await WaitFor(() => ThemeSelector.IsLoaded && !contentAnimations.ContainsKey(PageHost));
        foreach (int selected in new[] { 2, 1, 2, 0, 1 })
        {
            ThemeSelector.SelectedIndex = selected;
            await Task.Delay(280);
            if (selected == 0 && Root.RequestedTheme != ElementTheme.Default ||
                selected == 1 && Root.ActualTheme != ElementTheme.Light ||
                selected == 2 && Root.ActualTheme != ElementTheme.Dark)
                throw new InvalidOperationException("主题未正确切换。");
            bool dark = Root.ActualTheme == ElementTheme.Dark;
            foreach (var key in new[] { "WindowBrush", "SurfaceBrush", "InkBrush", "MutedBrush", "SuccessBrush", "WarningBrush", "ErrorBrush" })
                if (ThemeBrush(key).Color != ParseColor(dark ? Palette[key].Dark : Palette[key].Light))
                    throw new InvalidOperationException("主题配色未更新：" + key);
            if (JsonSerializer.Deserialize<int>(await File.ReadAllTextAsync(appearancePath)) != selected)
                throw new InvalidOperationException("主题偏好未保存。");
            var dialog = Dialog("主题检查", ReadOnlyBody("主题检查"));
            if (dialog.RequestedTheme != Root.RequestedTheme) throw new InvalidOperationException("弹窗没有跟随主题。");
        }
        ThemeSelector.SelectedIndex = 2; ThemeSelector.SelectedIndex = 1; ThemeSelector.SelectedIndex = 2; ThemeSelector.SelectedIndex = 1;
        await Task.Delay(280);
        if (ThemeBrush("SurfaceBrush").Color != ParseColor(Palette["SurfaceBrush"].Light))
            throw new InvalidOperationException("快速切换主题没有停在最后选择。");
        AttachInteractionMotion(Root);
        if (SaveButton.RenderTransform is not ScaleTransform)
            throw new InvalidOperationException("按钮交互动效未加载。");
        foreach (int theme in new[] { 2, 1 })
        {
            ThemeSelector.SelectedIndex = theme;
            await Task.Delay(280);
            foreach (var button in new[] { SaveButton, VersionButton })
            {
                button.ApplyTemplate();
                var presenter = VisualChild<ContentPresenter>(button) ?? throw new InvalidOperationException("按钮模板未加载。");
                try
                {
                    foreach (var state in new[] { "PointerOver", "Pressed" })
                    {
                        if (!VisualStateManager.GoToState(button, state, false)) throw new InvalidOperationException("按钮状态未加载。");
                        await Task.Delay(180);
                        var expected = button == SaveButton ? (SolidColorBrush)button.Resources["ButtonBackground" + state] : ThemeBrush(state == "PointerOver" ? "HoverBrush" : "PressedBrush");
                        if (presenter.Background is not SolidColorBrush actual || actual.Color != expected.Color)
                            throw new InvalidOperationException("按钮悬停与按下配色未正确映射：" + state);
                    }
                }
                finally { VisualStateManager.GoToState(button, "Normal", false); }
            }
        }
        Navigation.SelectedIndex = 0;
        await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
    }

    private async Task CheckResponsiveServices()
    {
        Navigation.SelectedIndex = 2;
        await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
        var original = AppWindow.Size;
        double originalRootWidth = Root.Width;
        double scale = GetDpiForWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)) / 96d;
        try
        {
            foreach (int width in new[] { 1500, 980, 1500 })
            {
                AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(width * scale), original.Height));
                // Hosted desktops can clamp a native window to their small
                // work area. Exercise the requested logical viewport even in
                // that case, rather than assuming Resize accepted 1500 DIPs.
                Root.Width = width;
                smokeStep = $"responsive width={width}, native={AppWindow.Size.Width}, dpi={scale}";
                await WaitFor(() =>
                {
                    Root.UpdateLayout();
                    var viewport = VisualChild<ScrollViewer>(pages[2], "ServicesScroll");
                    var bankPane = VisualChild<ScrollViewer>(pages[2], "BankSettings");
                    var aiPane = VisualChild<ScrollViewer>(pages[2], "AiSettings");
                    var columns = VisualChild<Grid>(pages[2], "ServiceColumns");
                    if (viewport == null || bankPane == null || aiPane == null || columns == null) return false;
                    bool wide = width != 980;
                    if (Math.Abs(Root.ActualWidth - width) > 2 || (viewport.ActualWidth > 880) != wide) return false;
                    var bankPosition = bankPane.TransformToVisual(columns).TransformPoint(new Windows.Foundation.Point());
                    var aiPosition = aiPane.TransformToVisual(columns).TransformPoint(new Windows.Foundation.Point());
                    return wide ? aiPosition.X > bankPosition.X && bankPane.ActualHeight <= viewport.ActualHeight + 2
                        : aiPosition.Y >= bankPosition.Y + bankPane.ActualHeight && viewport.ScrollableHeight > 0;
                });
                if (Grid.GetRow(HeaderActions) != 0 || Grid.GetColumn(HeaderActions) != 1)
                    throw new InvalidOperationException("窗口变窄后保存设置移到了标题下方。");
                var scroll = VisualChild<ScrollViewer>(pages[2], "ServicesScroll")!;
                var banks = VisualChild<ScrollViewer>(pages[2], "BankSettings")!;
                var ai = VisualChild<ScrollViewer>(pages[2], "AiSettings")!;
                var grid = VisualChild<Grid>(pages[2], "ServiceColumns")!;
                var bankPoint = banks.TransformToVisual(grid).TransformPoint(new Windows.Foundation.Point());
                var aiPoint = ai.TransformToVisual(grid).TransformPoint(new Windows.Foundation.Point());
                if (ai.Visibility != Visibility.Visible || ai.ActualHeight < 100)
                    throw new InvalidOperationException("AI 设置在调整窗口大小后不可见。");
                if (width == 980)
                {
                    if (aiPoint.Y < bankPoint.Y + banks.ActualHeight || scroll.ScrollableHeight <= 0)
                        throw new InvalidOperationException("窄窗口没有将 AI 设置放在题库下方。");
                    revealServiceGroup?.Invoke("ai");
                    await Task.Delay(500); Root.UpdateLayout();
                    if (scroll.VerticalOffset <= 0)
                        throw new InvalidOperationException($"窄窗口无法定位到 AI 设置：width={scroll.ActualWidth}, mode={scroll.VerticalScrollMode}, offset={scroll.VerticalOffset}, range={scroll.ScrollableHeight}, aiY={aiPoint.Y}, bankHeight={banks.ActualHeight}, gridHeight={grid.ActualHeight}, request={scroll.Tag}。");
                }
                else if (aiPoint.X <= bankPoint.X || banks.ActualHeight > scroll.ActualHeight + 2)
                    throw new InvalidOperationException("宽窗口没有恢复为左右并排布局。");
            }
        }
        finally { Root.Width = originalRootWidth; AppWindow.Resize(original); smokeStep = "responsive complete"; }
    }

    private async Task CheckMonitorStability()
    {
        Navigation.SelectedIndex = 4;
        await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
        var list = resultLists["tasks"];
        var snapshot = J.A(backend.State["tasks"]).Select(n => n.DeepClone()).ToArray();
        var mediaTask = snapshot[0];
        mediaTask["status"] = "running"; mediaTask["statusText"] = "处理中";
        mediaTask["media"] = new JsonObject { ["duration"] = 100, ["percent"] = 10, ["name"] = "离线进度验收", ["statusText"] = "播放中" };
        void Sync() => SyncRows("tasks", list, snapshot.Select(n => new RecordRow(n.DeepClone())));
        Sync(); Root.UpdateLayout();
        list.ScrollIntoView(list.Items[0]);
        await WaitFor(() => list.ContainerFromIndex(0) != null);
        Root.UpdateLayout();
        var row = rowCollections["tasks"][0];
        var container = (DependencyObject)list.ContainerFromIndex(0);
        var bar = VisualChild<ProgressBar>(container) ?? throw new InvalidOperationException("视频进度条未显示。");
        for (int tick = 11; tick <= 30; tick++)
        {
            mediaTask["media"]!["percent"] = tick;
            Sync(); Root.UpdateLayout(); await Task.Delay(20);
            if (!ReferenceEquals(row, rowCollections["tasks"][0]) || !ReferenceEquals(bar, VisualChild<ProgressBar>(container)) || bar.Value != tick)
                throw new InvalidOperationException("视频更新重建了任务行或进度条未连续更新。");
        }
        SyncRows("tasks", list, J.A(backend.State["tasks"]).Select(n => new RecordRow(n)));

        var dialog = Dialog("离线详情居中验收", new TextBox { Text = "章节详情", MinWidth = 480 });
        var shown = ShowModal(dialog);
        try
        {
            await WaitFor(() => VisualChild<Border>(dialog, "BackgroundElement")?.ActualWidth > 0);
            await Task.Delay(350);
            var original = AppWindow.Size;
            try
            {
                foreach (var size in new[] { original, new Windows.Graphics.SizeInt32(original.Width - 120, original.Height - 60) })
                {
                    AppWindow.Resize(size); await Task.Delay(150); Root.UpdateLayout();
                    var surface = VisualChild<Border>(dialog, "BackgroundElement")!;
                    var center = surface.TransformToVisual(Root).TransformPoint(new Windows.Foundation.Point(surface.ActualWidth / 2, surface.ActualHeight / 2));
                    if (Math.Abs(center.X - Root.ActualWidth / 2) > 3 || Math.Abs(center.Y - Root.ActualHeight / 2) > 3)
                        throw new InvalidOperationException("详情弹窗没有随窗口居中。");
                }
            }
            finally { AppWindow.Resize(original); }
        }
        finally { dialog.Hide(); await shown; }
    }

    private async Task CheckModalReviewRegressions()
    {
        var originalInput = backend.State["input"]?.DeepClone();
        string originalSignature = inputSignature;
        Task? manual = null;
        var info = ShowText("显示与写盘容量", "离线容量检查");
        try
        {
            await WaitFor(() => currentDialog?.IsLoaded == true);
            var first = currentDialog!;
            var input = new JsonObject { ["title"] = "排队的人工问题", ["type"] = "text" };
            backend.State["input"] = input.DeepClone(); inputSignature = "modal-review";
            // Same path used when a worker request arrives during an info dialog.
            EnqueueDialog(async () => { manual = ShowInput(input, "modal-review"); await manual; });
            await Task.Delay(100);
            if (currentDialog != first || manualDialog != null || manual?.IsFaulted == true)
                throw new InvalidOperationException("信息弹窗和人工补全没有串行显示。");
            first.Hide(); await info;
            await WaitFor(() => manualDialog?.IsLoaded == true);
            if (currentDialog != manualDialog) throw new InvalidOperationException("人工问题被信息弹窗覆盖。");
        }
        finally
        {
            inputSignature = originalSignature;
            backend.State["input"] = originalInput;
            manualDialog?.Hide();
            if (manual != null) await manual;
            await info;
        }
        await WaitFor(() => !processingDialogs);

        var originalFeedback = backend.State["uiFeedback"]?.DeepClone();
        bool originalEditable = J.B(backend.State, "draftEditable");
        var draft = ShowDraft(new JsonObject { ["kind"] = "discussion", ["content"] = "保留的编辑内容",
            ["source"] = new JsonObject { ["title"] = "保存失败验收" } });
        try
        {
            await WaitFor(() => currentDialog?.IsLoaded == true);
            var dialog = currentDialog!;
            var feedback = VisualChild<InfoBar>(dialog)!;
            foreach (bool editable in new[] { false, true, false })
            {
                backend.State["draftEditable"] = editable;
                Refresh(new JsonObject());
                if (VisualChild<TextBox>(dialog)!.IsEnabled != editable)
                    throw new InvalidOperationException("后台状态变化后草稿编辑权限未刷新。");
            }
            await SaveDraftFromEditor(feedback); // No source draft: backend rejects this synthetic save.
            if (feedback.Severity != InfoBarSeverity.Error || string.IsNullOrWhiteSpace(feedback.Message))
                throw new InvalidOperationException("人工副本保存失败未在编辑器内显示。");
            if (!VisualChild<TextBox>(dialog)!.Text.Contains("保留的编辑内容"))
                throw new InvalidOperationException("保存失败丢失了编辑内容。");
            foreach (var tone in new[] { "danger", "warning", "success" })
            {
                backend.State["uiFeedback"] = new JsonObject { ["draft"] = new JsonObject { ["message"] = "副本反馈 " + tone, ["tone"] = tone } };
                UpdateDraftFeedback(feedback);
                var expected = tone == "danger" ? InfoBarSeverity.Error : tone == "warning" ? InfoBarSeverity.Warning : InfoBarSeverity.Success;
                if (feedback.Message != "副本反馈 " + tone || feedback.Severity != expected)
                    throw new InvalidOperationException("副本操作反馈或严重程度未同步。");
            }
        }
        finally { currentDialog?.Hide(); await draft; backend.State["uiFeedback"] = originalFeedback; backend.State["draftEditable"] = originalEditable; }

        // draftDirty only reaches the window on a later state flush, so an edit made
        // and then immediately closed must still be caught by the local edit flag.
        var raceDraft = ShowDraft(new JsonObject { ["kind"] = "discussion", ["content"] = "原始内容",
            ["source"] = new JsonObject { ["title"] = "编辑后立即关闭" } });
        try
        {
            await WaitFor(() => currentDialog?.IsLoaded == true);
            var dialog = currentDialog!;
            backend.State["draftEditable"] = true; backend.State["draftDirty"] = false;
            Refresh(new JsonObject());
            var editor = VisualChild<TextBox>(dialog) ?? throw new InvalidOperationException("草稿编辑器缺失。");
            editor.Text = "用户刚输入的改动";
            await Task.Delay(20);   // Lets the edit handler run; the state flush is still up to 100 ms away.
            // Reproduce the window deterministically: the backend has already
            // accepted the edit, but its broadcast dirty flag has not arrived yet.
            backend.State["draftDirty"] = false;
            // Positive control: with the state looking clean, only the local edit
            // flag can block the close, so a pass proves the edit was noticed.
            var close = FindDescendant<Button>(dialog, button => button.Content?.ToString() == "关闭")
                ?? throw new InvalidOperationException("草稿弹窗缺少关闭按钮。");
            await Click(close);
            await Task.Delay(300);
            if (currentDialog != dialog || !dialog.IsLoaded)
                throw new InvalidOperationException("编辑后立即关闭丢失了未保存的改动。");
            if (J.B(backend.State, "draftDirty"))
                throw new InvalidOperationException("竞争验收前提被破坏：关闭时 dirty 状态已经同步。");
            // The blocked dialog must still offer the explicit discard path.
            var discard = FindDescendant<Button>(dialog, button => button.Content?.ToString() == "放弃本次修改")
                ?? throw new InvalidOperationException("未保存改动被拦截后没有提供放弃入口。");
            await Click(discard);
            await WaitFor(() => currentDialog == null, 10);
        }
        finally { currentDialog?.Hide(); await raceDraft; backend.State["draftEditable"] = originalEditable; }
    }

    // Runs only where the offline run has populated the result lists: the
    // token must come from the product's own ItemClick handler, so a synthetic
    // enqueue would prove nothing.
    private async Task CheckRowClickAfterReset()
    {
        smokeStep = "row-click reset";
        foreach (var (kind, tab) in new[] { ("tasks", 0), ("questions", 1), ("discussions", 2), ("activities", 3) })
        {
            if (rowCollections[kind].Count == 0) throw new InvalidOperationException(kind + " 没有可用于真实点击的行。");
            if (resultTabs != null) resultTabs.SelectedIndex = tab;
            var list = resultLists[kind];
            list.ScrollIntoView(list.Items[0]);
            await WaitFor(() => list.ContainerFromIndex(0) != null, 10);
            Root.UpdateLayout();

            // Positive control: without a reset the very same click must open
            // the detail, otherwise the drop below proves nothing.
            var opener = FrameworkElementAutomationPeer.CreatePeerForElement((FrameworkElement)list.ContainerFromIndex(0)!);
            if (opener?.GetPattern(PatternInterface.Invoke) is not IInvokeProvider open)
                throw new InvalidOperationException(kind + " 首行不支持真实点击。");
            open.Invoke();
            await WaitFor(() => currentDialog?.IsLoaded == true, 15);
            currentDialog!.Hide();
            await WaitFor(() => !processingDialogs, 15);
            if (currentDialog != null) throw new InvalidOperationException(kind + " 对照点击的详情没有关闭。");

            // Same click, but the scope resets while the detail waits behind
            // another modal. The token captured at click time must drop it.
            var blocker = Dialog("阻断弹窗", Text("合成阻断", 12));
            var blocking = ShowModal(blocker);
            await WaitFor(() => ReferenceEquals(currentDialog, blocker) && blocker.IsLoaded);
            var container = (DependencyObject?)list.ContainerFromIndex(0)
                ?? throw new InvalidOperationException(kind + " 首行没有生成容器，无法点击。");
            var peer = FrameworkElementAutomationPeer.CreatePeerForElement((FrameworkElement)container);
            if (peer?.GetPattern(PatternInterface.Invoke) is not IInvokeProvider invoke)
                throw new InvalidOperationException(kind + " 首行不支持真实点击。");
            invoke.Invoke();
            // Wait until the product's own click handler has actually enqueued
            // the detail queue (and drained it) behind the blocking modal, so
            // the reset below genuinely lands while it is waiting on the gate.
            await WaitFor(() => processingDialogs || dialogs.Count > 0, 5);
            await WaitFor(() => dialogs.Count == 0, 5);
            HandleEvent("questionsReset", new JsonArray());
            blocker.Hide();
            await blocking;
            await WaitFor(() => !processingDialogs, 15);
            if (currentDialog != null) throw new InvalidOperationException(kind + " 复位后仍打开了排队中的旧详情。");
        }
        if (resultTabs != null) resultTabs.SelectedIndex = 0;
        smokeStep = "row-click reset complete";
    }

    private async Task CheckScopeResetDetailRace()
    {
        smokeStep = "scope-reset detail race";
        var originalInput = backend.State["input"]?.DeepClone();
        string originalSignature = inputSignature;
        try
        {
            // (1) A detail RPC still in flight when the scope resets belongs to
            // the discarded scope and must not open its dialog afterwards.
            var stale = ShowRecord("activities", new JsonObject { ["id"] = "synthetic-activity", ["title"] = "合成活动详情" });
            HandleEvent("questionsReset", new JsonArray());
            await Task.Delay(400);
            if (currentDialog != null) throw new InvalidOperationException("作用域复位后仍打开了旧详情弹窗。");
            await stale;
            if (currentDialog != null) throw new InvalidOperationException("旧详情在复位后迟开。");

            // (2) The same detail queued behind another modal must be dropped
            // when the reset arrives during that wait.
            var blocker = Dialog("阻断弹窗", Text("合成阻断", 12));
            var blocking = ShowModal(blocker);
            await WaitFor(() => ReferenceEquals(currentDialog, blocker) && blocker.IsLoaded);
            var queued = ShowRecord("discussions", new JsonObject { ["id"] = "synthetic-discussion", ["title"] = "合成讨论详情" });
            await Task.Delay(100);
            HandleEvent("questionsReset", new JsonArray());
            blocker.Hide();
            await blocking;
            await queued;
            await Task.Delay(200);
            if (currentDialog != null) throw new InvalidOperationException("复位后排队中的旧详情仍然弹出。");

            // (3) Real row clicks are verified on populated lists once the
            // offline demo run has produced rows; see CheckRowClickAfterReset.

            // (4) A manual-answer dialog owns its own gate and keeps working
            // through the same reset.
            var input = new JsonObject { ["title"] = "合成人工问题", ["type"] = "text", ["kind"] = "text" };
            backend.State["input"] = input.DeepClone(); inputSignature = "scope-reset-manual";
            Task? manual = null;
            EnqueueDialog(async () => { manual = ShowInput(input, "scope-reset-manual"); await manual; });
            await WaitFor(() => manualDialog?.IsLoaded == true);
            var shown = manualDialog!;
            HandleEvent("questionsReset", new JsonArray());
            await Task.Delay(150);
            if (!ReferenceEquals(manualDialog, shown) || !shown.IsLoaded)
                throw new InvalidOperationException("作用域复位关闭了人工补全弹窗。");
            if (shown.PrimaryButtonText != "使用答案" || shown.SecondaryButtonText != "跳过此题")
                throw new InvalidOperationException("人工补全弹窗的按钮在复位后不可用。");
            backend.State["input"] = null; inputSignature = originalSignature;
            shown.Hide();
            if (manual != null) await manual;
        }
        finally
        {
            inputSignature = originalSignature;
            backend.State["input"] = originalInput;
            manualDialog?.Hide();
            await WaitFor(() => !processingDialogs);
        }
        smokeStep = "scope-reset detail race complete";
    }

    // Real account switch through the product's own ComboBox handler. The two
    // accounts are created entirely offline: the isolated profile imports two
    // synthetic Cookie files through fileAction('import_cookie'); no network,
    // no real credential, no pre-seeded profile.
    private async Task CheckAccountSwitchScopeReset()
    {
        smokeStep = "account switch reset";
        Directory.CreateDirectory(smokeDestination!);
        string firstCookie = Path.Combine(smokeDestination!, "synthetic-account-1.txt");
        string secondCookie = Path.Combine(smokeDestination!, "synthetic-account-2.txt");
        await File.WriteAllTextAsync(firstCookie, "_uid=synthetic-uid-1; session=synthetic-1");
        await File.WriteAllTextAsync(secondCookie, "_uid=synthetic-uid-2; session=synthetic-2");
        await Call("addAccount");
        await Task.Delay(100);
        await Call("setValue", "common", "username", "合成账号一");
        await backend.Call("fileAction", new object?[] { "import_cookie" }, firstCookie);
        await Call("addAccount");
        await Task.Delay(100);
        await Call("setValue", "common", "username", "合成账号二");
        await backend.Call("fileAction", new object?[] { "import_cookie" }, secondCookie);
        await WaitFor(() => J.A(backend.State["accountRows"]).Count() == 2, 15);
        var rows = J.A(backend.State["accountRows"]).ToArray();
        string firstId = J.S(rows[0], "id"), secondId = J.S(rows[1], "id");
        if (firstId.Length == 0 || secondId.Length == 0 || firstId == secondId)
            throw new InvalidOperationException("合成账号未生成两个不同的本机账号。");
        // Establish the first account as the old scope, with real offline data.
        await Call("switchAccount", firstId);
        await WaitFor(() => J.S(Summary, "activeAccount") == firstId, 20);
        await Call("loadDemo");
        await WaitFor(() => !Busy && J.I(Summary, "courseCount") == 3, 30);
        await Call("selectVisible", true);
        await WaitFor(() => J.I(Summary, "selectedCount") == 3, 20);
        await Call("startTasks");
        await WaitFor(() => !Busy && resultLists["questions"].Items.Count > 0, 50);
        int oldQuestions = resultLists["questions"].Items.Count;
        // A retained filter and an open detail belong to the old account scope.
        resultFilters["questions"].Search.Text = "合成筛选关键字";
        await Task.Delay(150);
        var stale = ShowRecord("tasks", rowCollections["tasks"][0].Data);
        await WaitFor(() => currentDialog?.IsLoaded == true, 15);
        var staleDialog = currentDialog!;
        // Real selection change on the product control, not a direct RPC.
        var accountControl = accounts ?? throw new InvalidOperationException("账号下拉框未创建。");
        var target = ((IEnumerable<AccountItem>)accountControl.ItemsSource).FirstOrDefault(n => n.Id == secondId)
                     ?? throw new InvalidOperationException("账号下拉框缺少第二个合成账号。");
        accountControl.SelectedItem = target;
        await WaitFor(() => J.S(Summary, "activeAccount") == secondId, 20);
        await WaitFor(() => currentDialog == null, 20);
        await stale;
        if (staleDialog.IsLoaded) throw new InvalidOperationException("切换账号后旧详情仍然打开。");
        if (J.I(Summary, "courseCount") != 0 || courses?.Items.Count > 0) throw new InvalidOperationException("切换账号后旧课程没有清空。");
        if (J.B(Summary, "demo")) throw new InvalidOperationException("切换账号后旧演示作用域没有清空。");
        if (resultLists["questions"].Items.Count != 0 || resultLists["tasks"].Items.Count != 0)
            throw new InvalidOperationException("切换账号后旧结果列表没有清空（旧记录 " + oldQuestions + " 条）。");
        if (resultFilters["questions"].Search.Text.Length != 0) throw new InvalidOperationException("切换账号后旧搜索条件没有清空。");
        if (questionTask.Length != 0) throw new InvalidOperationException("切换账号后旧章节范围没有清空。");
        // The new account is usable: its own offline run produces its own rows.
        await Call("loadDemo");
        await WaitFor(() => !Busy && J.I(Summary, "courseCount") == 3, 30);
        await Call("selectVisible", true);
        await WaitFor(() => J.I(Summary, "selectedCount") == 3, 20);
        await Call("startTasks");
        await WaitFor(() => !Busy && resultLists["questions"].Items.Count > 0, 50);
        smokeStep = "account switch reset complete";
    }

    // A real offline startTasks: the backend clears its models and emits the
    // production questionsReset, which has to reset the retained result scope.
    private async Task CheckOfflineStartScopeReset()
    {
        smokeStep = "offline start reset";
        int revision = J.I(Summary, "questionRevision");
        int oldQuestions = resultLists["questions"].Items.Count;
        resultFilters["questions"].Search.Text = "合成筛选关键字";
        await Task.Delay(150);
        if (resultFilters["questions"].Search.Text.Length == 0) throw new InvalidOperationException("筛选框未接受输入。");
        var stale = ShowRecord("tasks", rowCollections["tasks"][0].Data);
        await WaitFor(() => currentDialog?.IsLoaded == true, 15);
        await Call("startTasks");
        await WaitFor(() => currentDialog == null, 25);
        await stale;
        await WaitFor(() => !Busy && resultLists["questions"].Items.Count > 0, 60);
        if (J.I(Summary, "questionRevision") <= revision)
            throw new InvalidOperationException("离线启动没有触发结果作用域复位。");
        if (resultFilters["questions"].Search.Text.Length != 0) throw new InvalidOperationException("真实启动后旧筛选没有清空。");
        if (questionTask.Length != 0) throw new InvalidOperationException("真实启动后旧章节范围没有清空。");
        if (oldQuestions == 0) throw new InvalidOperationException("复位前没有旧结果可比对。");
        smokeStep = "offline start reset complete";
    }

    private async Task CheckResumeEntry()
    {
        // Issue #5 entry: one real control tree, one real summary source.
        Navigation.SelectedIndex = 4;
        await WaitFor(() => !contentAnimations.ContainsKey(PageHost));
        var summary = (JsonObject)Summary!;
        if (!summary.ContainsKey("resumeScoped")) throw new InvalidOperationException("后台未提供续跑状态字段。");
        var card = VisualChild<Border>(pages[4], "ResumeCard") ?? throw new InvalidOperationException("任务工作区缺少续跑卡片。");
        var button = VisualChild<Button>(pages[4], "ResumeButton") ?? throw new InvalidOperationException("任务工作区缺少「继续上次任务」按钮。");
        if (VisualChild<TextBlock>(pages[4], "ResumeHint") is not { } hint) throw new InvalidOperationException("续跑入口缺少范围说明。");
        // Nothing was stopped yet, so the entry must be genuinely hidden.
        if (card.Visibility != Visibility.Collapsed || button.Visibility != Visibility.Collapsed)
            throw new InvalidOperationException("没有停止记录时仍显示续跑入口。");

        // Drive the real offline flow: demo courses, a real run, a real stop,
        // then a real continuation. No summary field is written by this test.
        // A fresh demo load keeps the preceding steps' run state untouched.
        await Call("loadDemo");
        await WaitFor(() => J.B(Summary, "coursesReady") && !J.B(Summary, "busy"), 30000);
        await Call("selectVisible", true);
        await Call("startTasks");
        await WaitFor(() => J.B(Summary, "busy"), 30000);
        if (J.B(Summary, "resumeScoped")) throw new InvalidOperationException("运行中不应出现续跑入口。");
        await WaitFor(() => J.I(Summary, "total") > 0 && J.I(Summary, "success") > 0, 60000);
        await Call("stop");
        await WaitFor(() => !J.B(Summary, "busy"), 60000);

        var resume = Summary?["resume"];
        if (!J.B(Summary, "resumeScoped") || !J.B(resume, "available"))
            throw new InvalidOperationException("显式停止后没有留下可继续的任务：" + J.S(resume, "reason"));
        if (J.I(resume, "taskCount") <= 0)
            throw new InvalidOperationException("续跑入口没有保留原任务点范围。");
        Root.UpdateLayout();
        if (card.Visibility != Visibility.Visible || button.Visibility != Visibility.Visible)
            throw new InvalidOperationException("显式停止后续跑入口仍不可见。");
        if (hint.Text.Length == 0) throw new InvalidOperationException("续跑入口没有显示原范围说明。");
        // Clickable is the real requirement: a pending write confirmation must
        // remain reachable, so enabled follows clickable, not readiness.
        if (J.B(resume, "clickable") != button.IsEnabled)
            throw new InvalidOperationException("续跑按钮可点击状态与后台不一致。");
        if (!button.IsEnabled) throw new InvalidOperationException("满足条件后续跑入口仍被禁用。");

        int before = J.I(Summary, "success"), total = J.I(Summary, "total");
        // The real command, through the same click the user performs.
        await Click(button);
        await WaitFor(() => J.B(Summary, "busy"), 30000);
        await WaitFor(() => J.I(Summary, "total") > 0, 30000);
        await WaitFor(() => !J.B(Summary, "busy"), 90000);
        if (J.I(Summary, "success") <= before || J.I(Summary, "total") != total)
            throw new InvalidOperationException("续跑没有在原章节范围内继续推进。");
        // The continuation is consumed; only a new stop may create another.
        if (J.B(Summary, "resumeScoped"))
            throw new InvalidOperationException("续跑完成后仍保留旧入口。");
        Root.UpdateLayout();
        if (card.Visibility != Visibility.Collapsed || button.Visibility != Visibility.Collapsed)
            throw new InvalidOperationException("清空续跑状态后入口未隐藏。");
        await Task.CompletedTask;
    }

    private async Task CheckRecordDetails()
    {
        var chapter = new JsonObject
        {
            ["title"] = "1.2 大学体育课程学习的规定", ["course"] = "大学体育",
            ["status"] = "success", ["statusText"] = "已完成",
            ["courseId"] = "10001", ["pointId"] = "20001", ["clazzId"] = "15800725",
        };
        foreach (var kind in new[] { "tasks", "attention" })
        {
            if (kind == "attention")
            {
                chapter["status"] = "待完成 / 未核验";
                chapter.Remove("statusText");
                chapter["reason"] = string.Join("\n", Enumerable.Repeat("章节包含测试题目，请在网页端查看。", 40));
                chapter["next"] = "在网页端完成测试题目后，再核验章节进度。";
            }
            var shown = ShowRecord(kind, chapter);
            try
            {
                await WaitFor(() => currentDialog?.IsLoaded == true);
                Root.UpdateLayout(); await Task.Delay(100);
                if (currentDialog?.Content is not ScrollViewer scroll || scroll.Content is not TextBlock text)
                    throw new InvalidOperationException("章节详情未使用可滚动的完整文本。");
                foreach (var expected in new[] { "课程：大学体育", "章节：1.2", "状态：", "班级编号：15800725" })
                    if (!text.Text.Contains(expected)) throw new InvalidOperationException("章节详情缺少信息：" + expected);
                if (text.Text.Contains("clazzId") || scroll.ActualHeight < 120 || text.ActualHeight < 120 || scroll.VerticalOffset > 1)
                    throw new InvalidOperationException("章节详情仍被压缩成单行或没有从开头显示。");
                if (kind == "attention")
                {
                    if (scroll.ScrollableHeight <= 0 || !text.Text.Contains("下一步：在网页端完成测试题目"))
                        throw new InvalidOperationException("长详情未保留处理说明和下一步。");
                    scroll.ChangeView(null, scroll.ScrollableHeight, null, disableAnimation: true);
                    await WaitFor(() => Math.Abs(scroll.VerticalOffset - scroll.ScrollableHeight) < 2);
                }
            }
            finally { currentDialog?.Hide(); await shown; }
        }
    }
}
