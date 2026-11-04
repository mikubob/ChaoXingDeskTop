using System.Text.Json;
using Microsoft.UI.Xaml;
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
