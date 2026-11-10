using Microsoft.UI.Xaml.Media;

namespace ChaoxingDesktop;

/// <summary>One retained log line, kept as an item so appends never reset the view.</summary>
public sealed class LogEntry
{
    public string Text { get; }
    public SolidColorBrush Brush { get; }

    public LogEntry(string text, SolidColorBrush brush)
    {
        Text = text;
        Brush = brush;
    }
}
