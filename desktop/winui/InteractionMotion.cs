using System.Runtime.CompilerServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private readonly ConditionalWeakTable<FrameworkElement, object> motionAttached = new();
    private static TransitionCollection LayoutTransitions() => new() { new RepositionThemeTransition() };

    private void AttachInteractionMotion(DependencyObject root)
    {
        if (root is FrameworkElement element && !motionAttached.TryGetValue(element, out _))
        {
            motionAttached.Add(element, new object());
            if (element is Border border)
                border.BackgroundTransition = new BrushTransition { Duration = TimeSpan.FromMilliseconds(160) };
            if (element is ButtonBase button) AttachButtonMotion(button);
            if (element is Expander expander)
                expander.Expanding += (_, _) => DispatcherQueue.TryEnqueue(() => AttachInteractionMotion(expander));
            if (element is ListViewBase list)
                list.ContainerContentChanging += (_, args) =>
                {
                    if (!args.InRecycleQueue)
                    {
                        var container = args.ItemContainer;
                        DispatcherQueue.TryEnqueue(() => AttachInteractionMotion(container));
                    }
                };
        }
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
            AttachInteractionMotion(VisualTreeHelper.GetChild(root, i));
    }

    private void AttachButtonMotion(ButtonBase button)
    {
        if (button is Button accent && accent.ReadLocalValue(Control.BackgroundProperty) != DependencyProperty.UnsetValue &&
            accent.Background is SolidColorBrush fill && accent.Foreground is SolidColorBrush ink && ink.Color == Microsoft.UI.Colors.White)
        {
            var color = fill.Color;
            Windows.UI.Color Blend(double factor) => Windows.UI.Color.FromArgb(255,
                (byte)(color.R + (255 - color.R) * factor), (byte)(color.G + (255 - color.G) * factor), (byte)(color.B + (255 - color.B) * factor));
            foreach (string prefix in new[] { "Button", "AccentButton" })
            {
                accent.Resources[prefix + "BackgroundPointerOver"] = new SolidColorBrush(Blend(.10));
                accent.Resources[prefix + "BackgroundPressed"] = new SolidColorBrush(Windows.UI.Color.FromArgb(255, (byte)(color.R * .8), (byte)(color.G * .8), (byte)(color.B * .8)));
                accent.Resources[prefix + "ForegroundPointerOver"] = accent.Resources[prefix + "ForegroundPressed"] = new SolidColorBrush(Microsoft.UI.Colors.White);
            }
        }
        var scale = new ScaleTransform();
        button.RenderTransform = scale;
        button.RenderTransformOrigin = new Windows.Foundation.Point(.5, .5);
        Storyboard? active = null;
        bool hover = false, pressed = false;
        void SetScale(double target)
        {
            double x = scale.ScaleX, y = scale.ScaleY;
            active?.Stop();
            scale.ScaleX = scale.ScaleY = target;
            if (!motionSettings.AnimationsEnabled) return;
            var storyboard = new Storyboard();
            Animate(storyboard, scale, "ScaleX", x, target, 140);
            Animate(storyboard, scale, "ScaleY", y, target, 140);
            active = storyboard;
            storyboard.Completed += (_, _) => { if (active == storyboard) { storyboard.Stop(); active = null; } };
            storyboard.Begin();
        }
        button.PointerEntered += (_, _) => { hover = true; if (!pressed && button.IsEnabled) SetScale(1); };
        button.PointerExited += (_, _) => { hover = false; if (!pressed) SetScale(1); };
        button.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler((_, _) => { if (button.IsEnabled) { pressed = true; SetScale(.975); } }), true);
        button.AddHandler(UIElement.PointerReleasedEvent, new PointerEventHandler((_, _) => { pressed = false; SetScale(1); }), true);
        button.PointerCaptureLost += (_, _) => { pressed = false; SetScale(1); };
        button.PointerCanceled += (_, _) => { pressed = false; hover = false; SetScale(1); };
        button.KeyDown += (_, args) => { if (args.Key is Windows.System.VirtualKey.Space or Windows.System.VirtualKey.Enter) { pressed = true; SetScale(.975); } };
        button.KeyUp += (_, args) => { if (args.Key is Windows.System.VirtualKey.Space or Windows.System.VirtualKey.Enter) { pressed = false; SetScale(1); } };
        button.LostFocus += (_, _) => { pressed = false; SetScale(1); };
        button.IsEnabledChanged += (_, _) => { if (!button.IsEnabled) { hover = pressed = false; SetScale(1); } };
        button.Unloaded += (_, _) => { active?.Stop(); active = null; scale.ScaleX = scale.ScaleY = 1; hover = pressed = false; };
    }
}
