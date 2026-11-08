using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private Grid? courseHost;
    private Border? accountConnectionCard;
    private FrameworkElement[] courseViews = [];
    private Button? returnToCourses;
    private bool showingMyCourses;
    private bool coursesLoadedPending;

    private bool CoursesReady => J.B(Summary, "coursesReady");

    private void ShowCourseView(bool showCourses, bool animate = true)
    {
        if (courseHost == null || showCourses && !CoursesReady) return;
        showingMyCourses = showCourses;
        // Login success moves the new view to the right. Going back mirrors
        // that direction; accounts and courses never share a split viewport.
        double distance = Math.Max(64, courseHost.ActualWidth);
        SwitchContent(courseHost, courseViews, showCourses ? 1 : 0,
                      showCourses ? -distance : distance, animate);
        UpdatePageHeading();
    }

    private void RefreshCourseNavigation()
    {
        if (returnToCourses != null)
        {
            returnToCourses.Visibility = CoursesReady ? Visibility.Visible : Visibility.Collapsed;
            returnToCourses.IsEnabled = !Busy;
        }
        if (!CoursesReady)
        {
            if (showingMyCourses) ShowCourseView(false);
            return;
        }
        if (coursesLoadedPending && !Busy)
        {
            coursesLoadedPending = false;
            Navigation.SelectedIndex = 0;
            ShowCourseView(true);
        }
    }
}
