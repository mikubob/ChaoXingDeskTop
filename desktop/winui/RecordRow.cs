using System.Text.Json.Nodes;
using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace ChaoxingDesktop;

public sealed class RecordRow : INotifyPropertyChanged
{
    public JsonNode Data { get; private set; }
    public string Title { get; private set; }
    public string Subtitle { get; private set; }
    public string Status { get; private set; }
    public bool Selected { get; private set; }
    public double Progress { get; private set; }
    public Visibility ProgressVisibility { get; private set; }
    public SolidColorBrush Background { get; private set; }
    public SolidColorBrush StatusForeground { get; private set; }
    public event PropertyChangedEventHandler? PropertyChanged;
    public string Identity => Data["id"]?.ToString() ?? string.Join("/", new[] { "courseId", "clazzId", "cpi" }.Select(k => J.S(Data, k)));

    public void UpdateFrom(RecordRow next)
    {
        // Preserve the item and its native ProgressBar between media ticks.
        Data = next.Data;
        if (Title != next.Title) { Title = next.Title; Changed(nameof(Title)); }
        if (Subtitle != next.Subtitle) { Subtitle = next.Subtitle; Changed(nameof(Subtitle)); }
        if (Status != next.Status) { Status = next.Status; Changed(nameof(Status)); }
        if (Selected != next.Selected) { Selected = next.Selected; Changed(nameof(Selected)); }
        if (Progress != next.Progress) { Progress = next.Progress; Changed(nameof(Progress)); }
        if (ProgressVisibility != next.ProgressVisibility) { ProgressVisibility = next.ProgressVisibility; Changed(nameof(ProgressVisibility)); }
        if (Background.Color != next.Background.Color) { Background = next.Background; Changed(nameof(Background)); }
        if (StatusForeground.Color != next.StatusForeground.Color) { StatusForeground = next.StatusForeground; Changed(nameof(StatusForeground)); }
    }

    private void Changed(string property) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(property));
    public RecordRow(JsonNode data, bool course = false, bool ids = false)
    {
        Data = data;
        Title = J.S(data, "title", J.S(data, "question"));
        Selected = J.B(data, "selected");
        StatusForeground = (SolidColorBrush)Application.Current.Resources[J.S(data, "status") switch
        {
            "success" => "SuccessBrush",
            "failed" => "ErrorBrush",
            "pending" or "processed" or "retry" or "stopped" => "WarningBrush",
            "waiting" or "skipped" => "MutedBrush",
            _ => "AccentBrush",
        }];
        Background = (SolidColorBrush)Application.Current.Resources[Selected ? "CourseSelectionBrush" : "SurfaceBrush"];
        var progress = data[course ? "progress" : "media"];
        ProgressVisibility = progress?[course ? "total" : "duration"] != null ? Visibility.Visible : Visibility.Collapsed;
        Progress = course ? 100.0 * J.I(progress, "completed") / Math.Max(1, J.I(progress, "total")) : J.I(progress, "percent");
        if (course)
        {
            Subtitle = J.S(data, "teacher") + " · " + J.S(data, "desc", "班级 " + J.S(data, "clazzId"));
            if (ids) Subtitle += " · 课程 " + J.S(data, "courseId") + " / 班级 " + J.S(data, "clazzId");
            Status = progress?["total"] != null ? $"已完成 {J.I(progress, "completed")} / {J.I(progress, "total")} 章" : J.B(progress, "loading") ? "统计中…" : J.B(progress, "failed") ? "统计失败" : "待统计";
        }
        else
        {
            Subtitle = string.Join(" · ", new[] { J.S(data, "course"), J.S(data, "chapter"), J.S(data, "reason"), J.S(progress, "name") }.Where(s => s.Length > 0));
            Status = J.S(data, "statusText", J.S(data, "stageText", J.S(data, "status", J.S(data, "stage"))));
            if (progress != null)
            {
                if (J.I(progress, "mediaTotal") > 1)
                    Status += $" · 媒体 {J.I(progress, "mediaIndex")}/{J.I(progress, "mediaTotal")}";
                Status += $" · 当前媒体 {Progress:0}% · {J.S(progress, "statusText")}";
            }
        }
    }
}
