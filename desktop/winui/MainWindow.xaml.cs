using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
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
    private readonly List<FrameworkElement> pages = new();
    private readonly List<Action> refreshers = new();
    private readonly List<Control> idleControls = new();
    private readonly Dictionary<string, Control> fields = new();
    private readonly Dictionary<string, string> fieldGroups = new();
    private Action<string>? revealServiceGroup;
    private readonly Dictionary<string, ListView> resultLists = new();
    private readonly Dictionary<string, string> listSignatures = new();
    private readonly Dictionary<string, ObservableCollection<RecordRow>> rowCollections = new();
    private ComboBox? accounts;
    private TextBlock? selectedText, courseCount, emptyCourses;
    private ListView? courses;
    private TextBox? logText;
    private bool refreshing, closing, closePending, showIds;
    private ContentDialog? currentDialog, manualDialog;
    private string inputSignature = "";
    private readonly Queue<Func<Task>> dialogs = new();
    private bool processingDialogs;
    private JsonNode? Summary => backend.State["summary"];
    private bool Busy => J.B(Summary, "busy");
    private string Value(string section, string key) => J.S(backend.State["values"]?[section], key);
    private static readonly string[] Titles = ["课程与账号", "学习设置", "题库与 AI", "高级设置", "任务与日志"];
    private static readonly string[] Descriptions = ["连接账户，选择课程，开启学习任务。", "设置学习节奏与章节处理策略。", "连接答题服务，配置你的补全流程。", "管理请求参数、识别规则与配置备份。", "关注处理进度，检查结果与待处理事项。"];

    public MainWindow()
    {
        InitializeComponent();
        ConfigureSmoke();
        InitializeAppearance();
        Title = "超星学习工作台 · WinUI 3";
        var hwnd = WindowNative.GetWindowHandle(this);
        var appWindow = AppWindow.GetFromWindowId(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(hwnd));
        double scale = GetDpiForWindow(hwnd) / 96.0;
        appWindow.Resize(new Windows.Graphics.SizeInt32((int)(1280 * scale), (int)(860 * scale)));
        appWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "app.ico"));
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
        Closed += (_, _) => backend.Dispose();
        Root.Loaded += (_, _) => { try { backend.Start(); } catch (Exception e) { Error(e.Message); } };
        Root.Loaded += (_, _) => AttachInteractionMotion(Root);
    }

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    private void ResizeLayout()
    {
        bool narrow = Root.ActualWidth < 1100;
        SidebarColumn.Width = new GridLength(narrow ? 184 : 216);
        PrivacyCard.Visibility = PageDescription.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;
        Workspace.Margin = new Thickness(Root.ActualHeight < 780 ? 16 : 24);
        Grid.SetRow(HeaderActions, 0);
        Grid.SetColumn(HeaderActions, 1);
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
        AutomationProperties.SetName(button, title);
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
            if (smokeDestination != null && !smokeStarted) _ = RunSmoke();
        }
        else if (type == "clipboard") { var data = new DataPackage(); data.SetText(J.S(message, "text")); Clipboard.SetContent(data); }
        else if (type == "openUrl") _ = Windows.System.Launcher.LaunchUriAsync(new Uri(J.S(message, "url")));
        else if (type == "activate") Activate();
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
        foreach (var page in pages) PageHost.Children.Add(page);
        DispatcherQueue.TryEnqueue(() => AttachInteractionMotion(Root));
        Navigate(Navigation, null!);
    }

    private void Refresh(JsonObject update)
    {
        refreshing = true;
        try
        {
            Notice.Message = J.S(Summary, "message");
            Notice.Severity = J.S(Summary, "tone") switch { "danger" => InfoBarSeverity.Error, "warning" => InfoBarSeverity.Warning, "success" => InfoBarSeverity.Success, _ => InfoBarSeverity.Informational };
            SaveState.Text = J.S(Summary, "saveState"); ModeText.Text = J.S(Summary, "mode");
            BusyRing.IsActive = Busy;
            PauseButton.Visibility = StopButton.Visibility = Busy ? Visibility.Visible : Visibility.Collapsed;
            PauseButton.Content = J.B(Summary, "paused") ? "恢复" : "暂停后续";
            StopButton.Content = J.B(Summary, "stopping") ? "停止中…" : "停止任务";
            PauseButton.IsEnabled = StopButton.IsEnabled = !J.B(Summary, "stopping");
            SaveButton.IsEnabled = !Busy;
            foreach (var control in idleControls) control.IsEnabled = !Busy;
            foreach (var refresh in refreshers) refresh();
            Footer.Text = J.B(Summary, "demo") ? "离线演示 · 所有课程与任务均为示例" : "学习工作台 · WinUI 3";
            if (update.ContainsKey("courses")) RefreshCourses();
            foreach (var pair in resultLists)
                if (update.ContainsKey(pair.Key)) RefreshRecords(pair.Key, pair.Value);
            if (update.ContainsKey("logs") && logText != null) logText.Text = string.Join(Environment.NewLine, J.A(backend.State["logs"]).Select(n => n.ToString()));
            if (update.ContainsKey("input")) RefreshInput();
            RefreshCourseNavigation();
        }
        finally { refreshing = false; }
    }

    private async void Save(object sender, RoutedEventArgs e) => await Call("save");
    private async void Pause(object sender, RoutedEventArgs e) => await Call("pause");
    private async void Stop(object sender, RoutedEventArgs e) => await Call("stop");
    private void ShowRelease(object sender, RoutedEventArgs e) => EnqueueDialog(async () => await ShowText("版本、发行说明与校验信息", J.S(backend.State, "releaseInfo"), Row(Button("检查版本网页…", async () => await Call("releaseAction", "latest")), Button("发行说明…", async () => await Call("releaseAction", "notes")), Button("复制校验说明", async () => await Call("releaseAction", "copy")))));

    private async void OnClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (closing) return;
        if (!backend.Connected) { closing = true; return; }
        args.Cancel = true;
        if (closePending) return;
        closePending = true;
        if ((await Call("close"))?.ToString() == "true") { await FinishCloseAsync(); return; }
        EnqueueDialog(async () =>
        {
            if (await Confirm("退出前保存未完成", J.S(Summary, "message") + "\n返回修正后可再次保存。", "放弃修改并退出"))
                if ((await Call("discardAndClose"))?.ToString() == "true") await FinishCloseAsync();
            closePending = false;
        });
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
        currentDialog = dialog;
        try { return await dialog.ShowAsync() == ContentDialogResult.Primary; }
        finally { currentDialog = null; }
    }
    private async Task ShowText(string title, string body, UIElement? actions = null)
    {
        var text = ReadOnlyBody(body);
        var dialog = Dialog(title, actions == null ? text : Column(text, actions));
        currentDialog = dialog;
        try { await dialog.ShowAsync(); } finally { currentDialog = null; }
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
