using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Windowing;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;
using WinRT.Interop;
using System.Runtime.InteropServices;
using System.Collections.ObjectModel;

namespace ChaoxingDesktop;

public sealed partial class MainWindow : Window
{
    private readonly BackendClient backend;
    private readonly TrayIcon tray;
    private readonly List<FrameworkElement> pages = new();
    private readonly List<Action> refreshers = new();
    private readonly List<Control> idleControls = new();
    private readonly Dictionary<string, Control> fields = new();
    private readonly HashSet<string> correctingFields = new();
    private readonly Dictionary<string, Action> finishCorrections = new();
    private readonly SemaphoreSlim modalGate = new(1, 1);
    private readonly Dictionary<string, string> fieldGroups = new();
    private Action<string>? revealServiceGroup;
    private readonly Dictionary<string, ListView> resultLists = new();
    private readonly Dictionary<string, string> listSignatures = new();
    private readonly Dictionary<string, ObservableCollection<RecordRow>> rowCollections = new();
    private ComboBox? accounts;
    private TextBlock? selectedText, courseCount, emptyCourses;
    private ListView? courses;
    private TextBox? courseSearch;
    private bool refreshing, closing, showIds;
    private TaskCompletionSource? closePending;
    private ContentDialog? currentDialog, manualDialog;
    private string inputSignature = "";
    private readonly Queue<Func<Task>> dialogs = new();
    private bool processingDialogs;
    private readonly string? releaseCheckDirectory;
    private bool releaseCheckStarted;
    private JsonNode? Summary => backend.State["summary"];
    private bool Busy => J.B(Summary, "busy");
    private string Value(string section, string key) => J.S(backend.State["values"]?[section], key);
    private static readonly string[] Titles = ["课程与账号", "学习设置", "题库与 AI", "高级设置", "任务与日志", "考试"];

    public MainWindow()
    {
        InitializeComponent();
        var arguments = Environment.GetCommandLineArgs();
        int check = Array.IndexOf(arguments, "--smoke-test");
        if (check >= 0 && check + 1 < arguments.Length)
            releaseCheckDirectory = Path.GetFullPath(arguments[check + 1]);
        InitializeAppearance();
        Title = "超星学习工作台 · WinUI 3";
        var hwnd = WindowNative.GetWindowHandle(this);
        var appWindow = AppWindow.GetFromWindowId(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(hwnd));
        double scale = GetDpiForWindow(hwnd) / 96.0;
        appWindow.Resize(new Windows.Graphics.SizeInt32((int)(1280 * scale), (int)(860 * scale)));
        string iconPath = Path.Combine(AppContext.BaseDirectory, "app.ico");
        appWindow.SetIcon(iconPath);
        tray = new TrayIcon(hwnd, iconPath,
            () => DispatcherQueue.TryEnqueue(RestoreWindow),
            () => DispatcherQueue.TryEnqueue(async () => { RestoreWindow(); await RequestCloseAsync(); }));
        if (appWindow.Presenter is OverlappedPresenter presenter) presenter.PreferredMinimumWidth = (int)(980 * scale);
        if (appWindow.Presenter is OverlappedPresenter presenter2) presenter2.PreferredMinimumHeight = (int)(700 * scale);
        appWindow.Closing += OnClosing;
        Root.SizeChanged += (_, _) => ResizeLayout();
        Navigation.Loaded += (_, _) => MoveCategoryIndicator(false);
        Navigation.SizeChanged += (_, _) => MoveCategoryIndicator(false);
        ClipContent(PageHost);
        backend = new BackendClient(DispatcherQueue);
        backend.Received += OnMessage;
        backend.Failed += message => { if (!closing) Error(message); };
        Closed += (_, _) => { tray.Dispose(); backend.Dispose(); };
        Root.Loaded += (_, _) => { try { backend.Start(); } catch (Exception e) { Error(e.Message); } };
        Root.Loaded += (_, _) => AttachInteractionMotion(Root);
    }

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    private void ResizeLayout()
    {
        bool narrow = Root.ActualWidth < 1100;
        SidebarColumn.Width = new GridLength(narrow ? 184 : 216);
        Workspace.Margin = new Thickness(Root.ActualHeight < 780 ? 16 : 24);
    }

    private async Task<JsonNode?> Call(string method, params object?[] args)
    {
        try { return await backend.Call(method, args); }
        catch (Exception e) { Error(e.Message); return null; }
    }
    private void Error(string message) { if (closing) return; Notice.Message = message; Notice.Severity = InfoBarSeverity.Error; Notice.IsOpen = true; }
    private static TextBlock Text(string text, double size = 13, bool muted = false) => new() { Text = text, FontSize = size, TextWrapping = TextWrapping.Wrap, Foreground = (Brush)Application.Current.Resources[muted ? "MutedBrush" : "InkBrush"] };
    private static StackPanel Column(params UIElement[] children) { var panel = new StackPanel { Spacing = 12, ChildrenTransitions = LayoutTransitions() }; foreach (var child in children) panel.Children.Add(child); return panel; }
    private static StackPanel Row(params UIElement[] children) { var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, ChildrenTransitions = LayoutTransitions() }; foreach (var child in children) panel.Children.Add(child); return panel; }
    private static ScrollViewer Scroll(UIElement content) => new() { Content = content, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalContentAlignment = HorizontalAlignment.Stretch };
    private static Border Card(UIElement content) => new() { Background = ThemeBrush("SurfaceBrush"), BorderBrush = ThemeBrush("BorderBrush"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Padding = new Thickness(20), Child = content };
    private Button Button(string title, Func<Task> action, bool idle = false, string? color = null)
    {
        var button = new Button { Content = title };
        button.Loaded += (_, _) => AttachInteractionMotion(button);
        if (color != null) { button.Background = new SolidColorBrush(ParseColor(color)); button.Foreground = new SolidColorBrush(Microsoft.UI.Colors.White); }
        button.Click += async (_, _) => { if (!refreshing) { try { await action(); } catch (Exception e) { Error(e.Message); } } };
        if (idle) idleControls.Add(button);
        return button;
    }
    private static Windows.UI.Color ParseColor(string hex) => Windows.UI.Color.FromArgb(255, Convert.ToByte(hex.Substring(1, 2), 16), Convert.ToByte(hex.Substring(3, 2), 16), Convert.ToByte(hex.Substring(5, 2), 16));
    private static Grid StretchRow(UIElement left, FrameworkElement right)
    {
        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.Children.Add(left); grid.Children.Add(right); Grid.SetColumn(right, 1); return grid;
    }
    private void Navigate(object sender, SelectionChangedEventArgs e)
    {
        int index = Navigation.SelectedIndex;
        if (PageHost == null || index < 0) return;
        UpdatePageHeading();
        if (pages.Count > 0)
        {
            double direction = mainPageIndex < 0 || index > mainPageIndex ? 24 : -24;
            SwitchContent(PageHost, pages, index, direction);
            mainPageIndex = index;
        }
        MoveCategoryIndicator(true);
    }

    private void OnMessage(JsonObject message)
    {
        if (closing) return;
        var type = J.S(message, "type");
        if (type == "ready")
        {
            Title = "超星学习工作台 · " + J.S(backend.State, "version");
            VersionButton.Content = "v" + J.S(backend.State, "version") + " · 版本与校验";
        }
        else if (type == "state")
        {
            if (pages.Count == 0) BuildPages();
            Refresh(message["data"] as JsonObject ?? new());
            if (releaseCheckDirectory != null && !releaseCheckStarted) _ = RunReleaseCheck(releaseCheckDirectory);
        }
        else if (type == "clipboard") { var data = new DataPackage(); data.SetText(J.S(message, "text")); Clipboard.SetContent(data); }
        else if (type == "openUrl") _ = Windows.System.Launcher.LaunchUriAsync(new Uri(J.S(message, "url")));
        else if (type == "activate") RestoreWindow();
        else if (type == "alreadyRunning") { closing = true; Close(); }
        else if (type == "event") HandleEvent(J.S(message, "name"), message["args"] as JsonArray ?? new());
    }

    private void BuildPages()
    {
        pages.Add(BuildCourses());
        pages.Add(Scroll(BuildGroups("learning")));
        pages.Add(BuildServices());
        pages.Add(Scroll(Column(BuildAppearance(), BuildGroups("advanced"), BuildBackup())));
        pages.Add(BuildMonitor());
        pages.Add(BuildExamPage());
        foreach (var page in pages) PageHost.Children.Add(page);
        DispatcherQueue.TryEnqueue(() => AttachInteractionMotion(Root));
        Navigate(Navigation, null!);
    }

    private async Task RunReleaseCheck(string destination)
    {
        const int demoCourses = 3;
        const int demoTasks = 9;
        releaseCheckStarted = true;
        AppWindow.Show();
        Activate();
        bool success = false;
        string message;
        async Task WaitFor(Func<bool> ready)
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!ready())
            {
                if (DateTime.UtcNow >= deadline) throw new InvalidOperationException("离线验收超时。" + Notice.Message);
                await Task.Delay(50);
            }
        }
        try
        {
            if (pages.Count != 6 || fields.Count == 0)
                throw new InvalidOperationException("原生导航或设置表单未加载。");
            await backend.Call("loadDemo");
            await WaitFor(() => !Busy && showingMyCourses && courses?.Items.Count == demoCourses);
            await backend.Call("selectVisible", [true]);
            await WaitFor(() => J.I(Summary, "selectedCount") == demoCourses);
            await backend.Call("startTasks");
            await WaitFor(() => !Busy && resultLists["tasks"].Items.Count == demoTasks && J.I(Summary, "progress") == 100);
            var taskRows = resultLists["tasks"].Items.OfType<RecordRow>().ToArray();
            if (!taskRows.Any(row => row.Subtitle.Contains("视频 · 2:00 / 2:00")) ||
                !taskRows.Any(row => row.Subtitle.Contains("音频 · 1:15 / 1:15")))
                throw new InvalidOperationException("章节任务未显示视频或音频时长。");
            var questions = resultLists["questions"].Items.OfType<RecordRow>().ToArray();
            if (questions.Length != demoCourses || !questions.Any(row => row.Subtitle == "答案：B · 第二项"))
                throw new InvalidOperationException("逐题结果未显示离线题目的答案。");
            var formula = questions.Single(row => J.S(row.Data, "source") == "ai");
            var random = questions.Single(row => J.S(row.Data, "source") == "random");
            var detail = QuestionDetailText((await backend.Call("questionDetail", [J.S(random.Data, "id")]))!);
            if (formula.Subtitle != "答案：B · [图片]" || !random.Subtitle.EndsWith(" · [图片]") ||
                !detail.Contains("D. [图片]") || !detail.Contains("请在言溪个人中心启用 AI 搜题。") ||
                !detail.Contains("AI 图片无法解码或转换。") || detail.Contains("formula.invalid") ||
                detail.Contains("未采用"))
                throw new InvalidOperationException("公式图片题未显示采用编号与服务记录。");
            SetQuestionScope(J.S(questions[0].Data, "task_id"));
            await WaitFor(() => resultLists["questions"].Items.Count == 1);
            SetQuestionScope("");
            await WaitFor(() => resultLists["questions"].Items.Count == demoCourses);

            Navigation.SelectedIndex = 5;
            await WaitFor(() => examCourseCombo?.Items.Count == demoCourses + 1);
            examCourseCombo!.SelectedIndex = 1;
            string examCourseKey = SelectedCourseKey(examCourseCombo);
            await backend.Call("loadExams", [examCourseKey]);
            await WaitFor(() => !J.B(backend.State["exam"], "busy") &&
                J.A(backend.State["exam"]?["list"]).SingleOrDefault() is JsonObject listedExam &&
                $"{J.S(listedExam, "courseId")}:{J.S(listedExam, "clazzId")}" == examCourseKey);
            await backend.Call("loadExams");
            await WaitFor(() => !J.B(backend.State["exam"], "busy") && J.A(backend.State["exam"]?["list"]).Count() == 4);
            var openableExam = J.A(backend.State["exam"]?["list"]).FirstOrDefault(item => J.B(item, "nativeSupported"))
                ?? throw new InvalidOperationException("演示考试列表中缺少可进入的场次。");
            string examId = J.S(openableExam, "examId");
            await backend.Call("openExam", [examId, true]);
            await WaitFor(() => !J.B(backend.State["exam"], "busy") && J.S(backend.State["exam"]?["current"], "examId") == examId);
            await backend.Call("requestExamSuggestions", ["all", true, false]);
            bool repliedExamInput = false;
            await WaitFor(() =>
            {
                if (!repliedExamInput && backend.State["exam"]?["input"] is JsonObject inp && inp.Count > 0)
                {
                    repliedExamInput = true;
                    _ = backend.Call("replyExamInput", ["allow-images", false]);
                }
                return !J.B(backend.State["exam"], "busy") && J.I(backend.State["exam"]?["assist"], "completed") == 5;
            });
            var firstQuestion = J.A(backend.State["exam"]?["current"]?["questions"]).FirstOrDefault()
                ?? throw new InvalidOperationException("演示试卷未返回题目。");
            string firstQid = J.S(firstQuestion, "id");
            await backend.Call("setExamAnswer", [firstQid, new { option_ids = new[] { "B" } }]);
            await WaitFor(() => J.B(backend.State["exam"]?["current"], "dirty"));
            await backend.Call("saveExamDraft", [false]);
            await WaitFor(() => !J.B(backend.State["exam"]?["current"], "dirty") && J.S(backend.State["exam"]?["current"], "state") == "local_draft");
            await backend.Call("closeExam", [false]);
            await WaitFor(() => !showingExamEditor);
            await backend.Call("openExam", [examId, true]);
            await WaitFor(() => showingExamEditor && !J.B(backend.State["exam"], "busy"));
            SelectExamQuestion(4);
            await WaitFor(() => examQuestionDetailPanel?.Children.OfType<Image>().Any(image =>
                image.Source is Microsoft.UI.Xaml.Media.Imaging.BitmapImage { PixelWidth: > 0 }) == true);
            string attemptId = J.S(backend.State["exam"]?["current"], "attemptId");
            await backend.Call("submitExam", [examId, attemptId]);
            await WaitFor(() => !J.B(backend.State["exam"], "busy") && J.S(backend.State["exam"]?["current"], "state") == "simulated_submitted");
            var finalQuestions = J.A(backend.State["exam"]?["current"]?["questions"]).ToArray();
            if (J.S(finalQuestions.FirstOrDefault(q => J.S(q, "id") == firstQid), "source") != "manual")
                throw new InvalidOperationException("考试手动修改的题目未保留 manual 来源。");
            var examExport = await backend.Call("exportExam");
            if (examExport?.ToString().Contains("已交卷（模拟）") != true)
                throw new InvalidOperationException("未能导出已交卷的模拟答卷。");

            await backend.Call("closeExam", [false]);
            SwitchExamMode(true);
            await WaitFor(() => hwCourseCombo?.Items.Count == demoCourses + 1);
            hwCourseCombo!.SelectedIndex = 0;
            string homeworkCourseKey = SelectedCourseKey(hwCourseCombo);
            await backend.Call("loadHomeworks", [homeworkCourseKey]);
            await WaitFor(() => !J.B(backend.State["homework"], "busy") &&
                J.A(backend.State["homework"]?["list"]).Count() == 2 &&
                J.A(backend.State["homework"]?["list"]).All(item =>
                    $"{J.S(item, "courseId")}:{J.S(item, "clazzId")}" == homeworkCourseKey));
            await backend.Call("loadHomework", [""]);
            await WaitFor(() => showingHomeworkEditor && !J.B(backend.State["homework"], "busy"));
            var homework = backend.State["homework"]?["current"];
            string homeworkId = J.S(homework, "draftId");
            if (J.I(homework, "supportedCount") != 5 || J.I(homework, "totalCount") != 6)
                throw new InvalidOperationException("作业未保留不支持题型的题号。");
            await backend.Call("setHomeworkAnswer", ["hw-104", new { blanks = new[] { "32", "128" } }]);
            await backend.Call("saveHomeworkDraft", [false]);
            await backend.Call("closeHomework", [false]);
            await backend.Call("openHomeworkDraft", [homeworkId]);
            await WaitFor(() => showingHomeworkEditor && J.I(backend.State["homework"]?["current"], "answeredCount") == 1);
            var exported = await backend.Call("exportHomeworkDraft");
            if (exported?.ToString().Contains("128") != true)
                throw new InvalidOperationException("作业草稿重新打开后未导出已保存的填空答案。");

            success = J.B(Summary, "demo") && J.I(Summary, "success") == demoTasks;
            message = success ? "WinUI 3 离线读课、任务、考试与独立作业验收通过。" : "离线任务结果与界面记录不一致。";
        }
        catch (Exception error) { message = error.Message; }

        Directory.CreateDirectory(destination);
        await File.WriteAllTextAsync(Path.Combine(destination, "result.json"),
            JsonSerializer.Serialize(new { success, message, frontend = "WinUI 3" }));
        Environment.ExitCode = success ? 0 : 1;
        if (backend.Connected) await Call("discardAndClose");
        await FinishCloseAsync();
    }

    private void Refresh(JsonObject update)
    {
        refreshing = true;
        try
        {
            Notice.Message = J.S(Summary, "message");
            Notice.Severity = J.S(Summary, "tone") switch { "danger" => InfoBarSeverity.Error, "warning" => InfoBarSeverity.Warning, "success" => InfoBarSeverity.Success, _ => InfoBarSeverity.Informational };
            SaveState.Text = J.S(Summary, "saveState");
            string mode = J.S(Summary, "mode");
            if (J.B(Summary, "demo") && !mode.StartsWith("离线演示", StringComparison.Ordinal))
                mode = mode.Length > 0 ? "离线演示 · " + mode : "离线演示";
            ModeText.Text = mode == "本地工作台" ? "" : mode;
            BusyRing.IsActive = Busy;
            BusyRing.Visibility = Busy ? Visibility.Visible : Visibility.Collapsed;
            PauseButton.Visibility = StopButton.Visibility = Busy ? Visibility.Visible : Visibility.Collapsed;
            PauseButton.Content = J.B(Summary, "paused") ? "恢复" : "暂停后续";
            StopButton.Content = J.B(Summary, "stopping") ? "停止中…" : "停止任务";
            PauseButton.IsEnabled = StopButton.IsEnabled = !J.B(Summary, "stopping");
            SaveButton.IsEnabled = !Busy;
            foreach (var control in idleControls) control.IsEnabled = !Busy;
            foreach (var refresh in refreshers) refresh();
            if (update.ContainsKey("courses"))
            {
                RefreshCourses();
                RefreshExam();
                RefreshHomework();
            }
            foreach (var pair in resultLists)
                if (update.ContainsKey(pair.Key)) RefreshRecords(pair.Key, pair.Value);
            if (update.ContainsKey("logs")) SyncLogs();
            if (update.ContainsKey("input")) RefreshInput();
            if (update.ContainsKey("exam") || update.ContainsKey("examProviders")) RefreshExam();
            if (update.ContainsKey("homework")) RefreshHomework();
            RefreshCourseNavigation();
        }
        finally { refreshing = false; }
    }

    private async void Save(object sender, RoutedEventArgs e) => await Call("save");
    private async void Pause(object sender, RoutedEventArgs e) => await Call("pause");
    private async void Stop(object sender, RoutedEventArgs e) => await Call("stop");
    internal async Task ResumeLastRunAsync()
    {
        // The summary is the only source of truth, and the backend re-checks
        // stop/identity/scope at call time. When the write policy needs
        // confirmation the backend emits startConfirmationRequested and nothing
        // starts until the user agrees, so this must stay clickable.
        var state = Summary?["resume"];
        if (!J.B(Summary, "resumeScoped") || !J.B(state, "available"))
        {
            Error(J.S(state, "reason", "当前没有可继续的任务。"));
            return;
        }
        try { await Call("resumeTasks"); }
        catch (Exception e) { Error(e.Message); return; }
        Navigation.SelectedIndex = 4;
    }
    private void ShowRelease(object sender, RoutedEventArgs e) => EnqueueDialog(async () => await ShowText("版本、发行说明与校验信息", J.S(backend.State, "releaseInfo"), Row(Button("检查版本网页…", async () => await Call("releaseAction", "latest")), Button("发行说明…", async () => await Call("releaseAction", "notes")), Button("复制校验说明", async () => await Call("releaseAction", "copy")))));

    private void MinimizeToTray(object sender, RoutedEventArgs args)
    {
        if (closing || closePending != null) return;
        if (tray.Show()) AppWindow.Hide();
        else Error("无法添加通知区图标，窗口仍可正常使用。");
    }

    private void RestoreWindow()
    {
        if (closing) return;
        tray.Hide();
        if (!AppWindow.IsVisible) AppWindow.Show();
        if (AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized } presenter)
            presenter.Restore();
        Activate();
    }

    private async void OnClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (closing) return;
        if (!backend.Connected && closePending == null) { closing = true; return; }
        args.Cancel = true;
        await RequestCloseAsync();
    }

    private async Task RequestCloseAsync()
    {
        if (closing || closePending != null) return;
        var decision = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        closePending = decision;
        try
        {
            if (!backend.Connected) { closing = true; Close(); return; }
            var status = (await Call("close"))?.ToString();
            if (status == "true") { await FinishCloseAsync(); return; }
            if (status != "confirm") return;

            manualDialog?.Hide();
            bool submittingExam = J.S(backend.State["exam"], "operation") == "exam_submit";
            string closeMessage = submittingExam
                ? "当前考试正在交卷。退出后可在「考试」中核验交卷结果。放弃并退出会丢弃未保存的设置和草稿修改。"
                : "放弃并退出会丢弃未保存的设置、考试答卷和作业草稿修改。退出会停止正在运行的后台任务。";
            var dialog = Dialog("确认退出", Text(closeMessage), "返回工作台");
            dialog.PrimaryButtonText = "保存并退出";
            dialog.SecondaryButtonText = "放弃并退出";
            var result = await ShowModal(dialog);
            var method = result == ContentDialogResult.Primary ? "saveAndClose" :
                result == ContentDialogResult.Secondary ? "discardAndClose" : null;
            if (method != null && (await Call(method))?.ToString() == "true")
                await FinishCloseAsync();
        }
        catch (Exception e) { Error(e.Message); }
        finally
        {
            if (!closing && backend.Connected) await Call("cancelClose");
            closePending = null;
            decision.SetResult();
        }
    }

    private async Task FinishCloseAsync()
    {
        HideForClose();
        try { await backend.ShutdownAsync(); }
        finally { Close(); }
    }

    private void HideForClose()
    {
        closing = true;
        tray.Hide();
        dialogs.Clear();
        currentDialog?.Hide();
        manualDialog?.Hide();
        AppWindow.Hide();
    }

    private void EnqueueDialog(Func<Task> action) { if (closing) return; dialogs.Enqueue(action); if (!processingDialogs) _ = DrainDialogs(); }
    private async Task DrainDialogs()
    {
        processingDialogs = true;
        try { while (dialogs.TryDequeue(out var action)) { try { await action(); } catch (Exception e) { Error(e.Message); } } }
        finally { processingDialogs = false; }
    }
    private ContentDialog Dialog(string title, UIElement content, string close = "关闭")
    {
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = title, Content = content, CloseButtonText = close, RequestedTheme = Root.RequestedTheme, DefaultButton = ContentDialogButton.Close };
        dialog.Opened += (_, _) => AttachInteractionMotion(dialog);
        // Limit the dialog surface, not the full-window popup layout root.
        dialog.Resources["ContentDialogMaxWidth"] = 800d;
        return dialog;
    }
    private async Task<bool> Confirm(string title, string body, string primary)
    {
        var dialog = Dialog(title, Scroll(Text(body)), "取消"); dialog.PrimaryButtonText = primary;
        return await ShowModal(dialog) == ContentDialogResult.Primary;
    }
    private async Task ShowText(string title, string body, UIElement? actions = null)
    {
        var text = ReadOnlyBody(body);
        var dialog = Dialog(title, actions == null ? text : Column(text, actions));
        await ShowModal(dialog);
    }

    private async Task<ContentDialogResult> ShowModal(ContentDialog dialog, bool manual = false, Func<bool>? stillWanted = null)
    {
        await modalGate.WaitAsync();
        try
        {
            if (closing || (manual && closePending != null) || stillWanted?.Invoke() == false)
                return ContentDialogResult.None;
            currentDialog = dialog;
            if (manual) manualDialog = dialog;
            return await dialog.ShowAsync();
        }
        finally
        {
            if (currentDialog == dialog) currentDialog = null;
            if (manualDialog == dialog) manualDialog = null;
            modalGate.Release();
        }
    }

    private static ScrollViewer ReadOnlyBody(string body) => new()
    {
        Content = new TextBlock { Text = string.IsNullOrWhiteSpace(body) ? "暂无详情。" : body, IsTextSelectionEnabled = true, TextWrapping = TextWrapping.Wrap },
        MinWidth = 480, MaxHeight = 430,
        HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        HorizontalContentAlignment = HorizontalAlignment.Stretch,
        VerticalContentAlignment = VerticalAlignment.Top,
    };

    private async Task FileAction(string method, object?[] args, bool save, string extension, string suggested = "export")
    {
        string path;
        if (save)
        {
            var picker = new FileSavePicker { SuggestedFileName = suggested };
            picker.FileTypeChoices.Add(extension.ToUpperInvariant() + " 文件", new[] { "." + extension });
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            var file = await picker.PickSaveFileAsync(); if (file == null) return; path = file.Path;
        }
        else
        {
            var picker = new FileOpenPicker(); picker.FileTypeFilter.Add("." + extension); picker.FileTypeFilter.Add("*");
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            var file = await picker.PickSingleFileAsync(); if (file == null) return; path = file.Path;
        }
        try { await backend.Call(method, args, path); } catch (Exception e) { Error(e.Message); }
    }
}
