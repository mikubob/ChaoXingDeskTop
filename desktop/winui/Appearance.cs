using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private readonly RadioButtons ThemeSelector = new()
    {
        ItemsSource = new[] { "跟随系统", "浅色", "深色" },
        MaxColumns = 3,
    };
    private bool appearanceReady;
    private string appearancePath = "";
    private Storyboard? paletteAnimation;
    private static readonly Dictionary<string, (string Light, string Dark)> Palette = new()
    {
        ["WindowBrush"] = ("#F6F7FB", "#191B20"),
        ["SidebarBrush"] = ("#EEF2F8", "#20232A"),
        ["SurfaceBrush"] = ("#FFFFFF", "#272B33"),
        ["BorderBrush"] = ("#E2E7EF", "#414752"),
        ["InkBrush"] = ("#17243A", "#EDF1F7"),
        ["MutedBrush"] = ("#627086", "#B4C0D2"),
        ["AccentBrush"] = ("#2563EB", "#8AB4FF"),
        ["HoverBrush"] = ("#E5EBF5", "#313B4B"),
        ["PressedBrush"] = ("#D0DDEE", "#1C293C"),
        ["SelectionBrush"] = ("#DCE8FF", "#30476B"),
        ["CourseSelectionBrush"] = ("#F0F5FF", "#283B58"),
        ["SoftBrush"] = ("#E3EAF5", "#2A3444"),
        ["BannerBrush"] = ("#EDF2FC", "#263347"),
        ["SuccessBrush"] = ("#15803D", "#6DDC94"),
        ["WarningBrush"] = ("#B45309", "#F5BE68"),
        ["ErrorBrush"] = ("#DC2626", "#FF9292"),
    };

    private static SolidColorBrush ThemeBrush(string key) => (SolidColorBrush)Application.Current.Resources[key];

    private void InitializeAppearance()
    {
        string folder = smokeDestination != null ? smokeDestination :
            Environment.GetEnvironmentVariable("CHAOXING_DESKTOP_DATA") ??
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ChaoxingDesktop");
        appearancePath = Path.Combine(folder, "appearance.json");
        int selected = 0;
        try
        {
            if (File.Exists(appearancePath))
                selected = JsonSerializer.Deserialize<int>(File.ReadAllText(appearancePath));
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException) { }
        selected = selected is >= 0 and <= 2 ? selected : 0;
        Root.ActualThemeChanged += (_, _) => ApplyPalette(Root.IsLoaded);
        Root.RequestedTheme = selected switch { 1 => ElementTheme.Light, 2 => ElementTheme.Dark, _ => ElementTheme.Default };
        ThemeSelector.SelectedIndex = selected;
        ApplyPalette(false);
        appearanceReady = true;
        ThemeSelector.SelectionChanged += ThemeChanged;
        ThemeSelector.Loaded += (_, _) => ThemeSelector.SelectedIndex = Root.RequestedTheme switch
        {
            ElementTheme.Light => 1, ElementTheme.Dark => 2, _ => 0,
        };
    }

    private Border BuildAppearance() => Card(Column(
        Text("外观", 18),
        Text("选择工作台主题，立即生效并自动保存。跟随系统会随设备主题自动切换。", 12, true),
        ThemeSelector));

    private void ThemeChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!appearanceReady) return;
        Root.RequestedTheme = ThemeSelector.SelectedIndex switch { 1 => ElementTheme.Light, 2 => ElementTheme.Dark, _ => ElementTheme.Default };
        if (currentDialog != null) currentDialog.RequestedTheme = Root.RequestedTheme;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(appearancePath)!);
            File.WriteAllText(appearancePath + ".tmp", JsonSerializer.Serialize(ThemeSelector.SelectedIndex));
            File.Move(appearancePath + ".tmp", appearancePath, overwrite: true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { Error("外观偏好未能保存，请检查本机数据目录权限。"); }
    }

    private void ApplyPalette(bool animate)
    {
        bool dark = Root.ActualTheme == ElementTheme.Dark;
        var previous = Palette.Keys.ToDictionary(key => key, key => ThemeBrush(key).Color);
        paletteAnimation?.Stop();
        var storyboard = new Storyboard();
        foreach (var pair in Palette)
        {
            var brush = ThemeBrush(pair.Key);
            var target = ParseColor(dark ? pair.Value.Dark : pair.Value.Light);
            brush.Color = target;
            if (animate && motionSettings.AnimationsEnabled)
            {
                var animation = new ColorAnimation { From = previous[pair.Key], To = target, Duration = TimeSpan.FromMilliseconds(220), EnableDependentAnimation = true };
                Storyboard.SetTarget(animation, brush); Storyboard.SetTargetProperty(animation, "Color");
                storyboard.Children.Add(animation);
            }
        }
        paletteAnimation = storyboard;
        storyboard.Completed += (_, _) => { if (paletteAnimation == storyboard) { storyboard.Stop(); paletteAnimation = null; } };
        if (storyboard.Children.Count > 0) storyboard.Begin();
        var title = AppWindow.TitleBar;
        title.BackgroundColor = title.ButtonBackgroundColor = ParseColor(dark ? "#191B20" : "#F6F7FB");
        title.ForegroundColor = title.ButtonForegroundColor = ParseColor(dark ? "#EDF1F7" : "#17243A");
        title.InactiveBackgroundColor = title.ButtonInactiveBackgroundColor = title.BackgroundColor;
        title.InactiveForegroundColor = title.ButtonInactiveForegroundColor = title.ForegroundColor;
    }
}
