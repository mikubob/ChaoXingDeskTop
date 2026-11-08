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
    private readonly Dictionary<ScrollViewer, CancellationTokenSource> scrollAnimations = new();

    private void ScrollTo(ScrollViewer viewer, double target)
    {
        if (scrollAnimations.Remove(viewer, out var previous)) previous.Cancel();
        target = Math.Clamp(target, 0, viewer.ScrollableHeight);
        if (!motionSettings.AnimationsEnabled)
        {
            viewer.ChangeView(null, target, null, disableAnimation: true);
            return;
        }
        var cancellation = new CancellationTokenSource();
        scrollAnimations[viewer] = cancellation;
        _ = AnimateScroll(viewer, target, cancellation);
    }

    private async Task AnimateScroll(ScrollViewer viewer, double target, CancellationTokenSource cancellation)
    {
        double start = viewer.VerticalOffset;
        var elapsed = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            while (!closing)
            {
                cancellation.Token.ThrowIfCancellationRequested();
                double phase = Math.Min(1, elapsed.Elapsed.TotalMilliseconds / ContentMotionMilliseconds);
                double eased = 1 - Math.Pow(1 - phase, 3);
                viewer.ChangeView(null, Math.Clamp(start + (target - start) * eased, 0, viewer.ScrollableHeight), null, disableAnimation: true);
                if (phase >= 1) break;
                await Task.Delay(16, cancellation.Token);
            }
        }
        catch (OperationCanceledException) { }
        finally
        {
            if (scrollAnimations.GetValueOrDefault(viewer) == cancellation) scrollAnimations.Remove(viewer);
            cancellation.Dispose();
        }
    }

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
        void Complete()
        {
            if (contentAnimations.GetValueOrDefault(host) != storyboard) return;
            contentAnimations.Remove(host);
            outgoing.Visibility = Visibility.Collapsed;
            storyboard.Stop();
            outgoing.Opacity = incoming.Opacity = 1;
        }
        storyboard.Completed += (_, _) => Complete();
        contentAnimations[host] = storyboard;
        storyboard.Begin();
        // Occluded/minimized windows may stop delivering rendering callbacks.
        // Always settle on the latest page even if Completed is deferred.
        _ = SettleAfterDelay();
        async Task SettleAfterDelay() { await Task.Delay(ContentMotionMilliseconds + 80); Complete(); }
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
