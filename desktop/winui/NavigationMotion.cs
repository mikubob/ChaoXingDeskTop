using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Windows.Foundation;
using Windows.UI.ViewManagement;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private readonly UISettings motionSettings = new();
    private readonly Dictionary<Grid, Storyboard> contentAnimations = new();
    private readonly Dictionary<Grid, int> contentPositions = new();
    private Storyboard? indicatorAnimation;
    private int mainPageIndex = -1;
    private double indicatorTarget;
    private const int ContentMotionMilliseconds = 260;

    private static void Animate(Storyboard storyboard, DependencyObject target, string property,
                                double from, double to, int milliseconds)
    {
        var animation = new DoubleAnimation
        {
            From = from, To = to, Duration = TimeSpan.FromMilliseconds(milliseconds),
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
        };
        Storyboard.SetTarget(animation, target);
        Storyboard.SetTargetProperty(animation, property);
        storyboard.Children.Add(animation);
    }

    // Each host owns its animation. Interruptions always settle on the newest
    // selection and never leave an old page clickable or partly transparent.
    private void SwitchContent(Grid host, IReadOnlyList<FrameworkElement> views,
                               int selected, double offset = 24, bool animate = true)
    {
        if (selected < 0 || selected >= views.Count) return;
        int previous = contentPositions.GetValueOrDefault(host, -1);
        if (previous == selected) return;
        if (contentAnimations.Remove(host, out var running)) running.Stop();
        contentPositions[host] = selected;
        for (int i = 0; i < views.Count; i++)
        {
            views[i].Opacity = 1;
            views[i].RenderTransform = new TranslateTransform();
            views[i].Visibility = i == selected ? Visibility.Visible : Visibility.Collapsed;
            views[i].IsHitTestVisible = i == selected;
        }
        if (!animate || previous < 0 || !motionSettings.AnimationsEnabled || !host.IsLoaded) return;
        var outgoing = views[previous]; var incoming = views[selected];
        outgoing.Visibility = Visibility.Visible;
        Canvas.SetZIndex(outgoing, 0); Canvas.SetZIndex(incoming, 1);
        var storyboard = new Storyboard();
        Animate(storyboard, incoming.RenderTransform, "X", offset, 0, ContentMotionMilliseconds);
        Animate(storyboard, incoming, "Opacity", 0, 1, ContentMotionMilliseconds);
        Animate(storyboard, outgoing.RenderTransform, "X", 0, -offset, ContentMotionMilliseconds);
        Animate(storyboard, outgoing, "Opacity", 1, 0, ContentMotionMilliseconds);
        storyboard.Completed += (_, _) =>
        {
            if (contentAnimations.GetValueOrDefault(host) != storyboard) return;
            contentAnimations.Remove(host);
            outgoing.Visibility = Visibility.Collapsed;
            storyboard.Stop();
            outgoing.Opacity = incoming.Opacity = 1;
        };
        contentAnimations[host] = storyboard;
        storyboard.Begin();
    }

    private static void ClipContent(Grid host)
    {
        void Update() => host.Clip = new RectangleGeometry { Rect = new Rect(0, 0, host.ActualWidth, host.ActualHeight) };
        host.SizeChanged += (_, _) => Update(); Update();
    }

    private void MoveCategoryIndicator(bool animate)
    {
        if (CategoryIndicator == null || CategoryHost == null || !Navigation.IsLoaded ||
            Navigation.SelectedItem is not ListViewItem item || item.ActualHeight <= 0) return;
        var point = item.TransformToVisual(CategoryHost).TransformPoint(new Point(0, 0));
        double target = point.Y + (item.ActualHeight - CategoryIndicator.Height) / 2;
        if (Math.Abs(indicatorTarget - target) < .1 && CategoryIndicator.Opacity == 1) return;
        var transform = (TranslateTransform)CategoryIndicator.RenderTransform;
        double current = transform.Y;
        indicatorAnimation?.Stop(); indicatorAnimation = null;
        transform.Y = target; indicatorTarget = target;
        bool visible = CategoryIndicator.Opacity == 1;
        CategoryIndicator.Opacity = 1;
        if (!animate || !visible || !motionSettings.AnimationsEnabled) return;
        var storyboard = new Storyboard();
        Animate(storyboard, transform, "Y", current, target, 240);
        indicatorAnimation = storyboard;
        storyboard.Completed += (_, _) => { if (indicatorAnimation == storyboard) { storyboard.Stop(); indicatorAnimation = null; transform.Y = target; } };
        storyboard.Begin();
    }

    private void UpdatePageHeading()
    {
        int index = Navigation.SelectedIndex;
        if (index < 0) return;
        PageTitle.Text = index == 0 ? (showingMyCourses ? "我的课程" : "账号连接") : Titles[index];
        PageDescription.Text = index == 0 ? (showingMyCourses ? "选择课程，按当前策略开启学习任务。" : "连接学习账号，登录并读取后进入我的课程。") : Descriptions[index];
    }
}
