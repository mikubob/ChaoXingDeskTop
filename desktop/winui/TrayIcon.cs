using System.Runtime.InteropServices;

namespace ChaoxingDesktop;

internal sealed class TrayIcon : IDisposable
{
    private const uint Callback = 0x8000 + 51;
    private const uint TaskbarIconId = 1;
    private const uint NimAdd = 0, NimDelete = 2, NimSetFocus = 3, NimSetVersion = 4;
    private const uint WmContextMenu = 0x007B, WmNcDestroy = 0x0082, NinSelect = 0x0400, NinKeySelect = 0x0401;
    private readonly IntPtr hwnd;
    private readonly string iconPath;
    private readonly Action restore, exit;
    private readonly SubclassProc callback;
    private readonly uint taskbarCreated = RegisterWindowMessage("TaskbarCreated");
    private IntPtr icon;
    private bool installed, visible;

    public TrayIcon(IntPtr hwnd, string iconPath, Action restore, Action exit)
    {
        this.hwnd = hwnd;
        this.iconPath = iconPath;
        this.restore = restore;
        this.exit = exit;
        callback = OnMessage;
    }

    public bool Show()
    {
        if (visible) return true;
        if (!installed)
            installed = taskbarCreated != 0 && SetWindowSubclass(hwnd, callback, 1, 0);
        if (!installed) return false;
        if (icon == IntPtr.Zero)
            icon = LoadImage(IntPtr.Zero, iconPath, 1, GetSystemMetrics(49), GetSystemMetrics(50), 0x0010);
        if (icon == IntPtr.Zero) return false;

        var data = IconData();
        data.Flags = 0x0087; // Message, icon, tooltip and standard tooltip in version 4.
        data.CallbackMessage = Callback;
        data.Icon = icon;
        data.Tip = "超星学习工作台";
        if (!Shell_NotifyIcon(NimAdd, ref data)) return false;
        data.Version = 4;
        visible = Shell_NotifyIcon(NimSetVersion, ref data);
        if (!visible) Shell_NotifyIcon(NimDelete, ref data);
        return visible;
    }

    public void Hide()
    {
        if (!visible) return;
        var data = IconData();
        Shell_NotifyIcon(NimDelete, ref data);
        visible = false;
    }

    public void Dispose()
    {
        Hide();
        if (installed)
        {
            RemoveWindowSubclass(hwnd, callback, 1);
            installed = false;
        }
        if (icon != IntPtr.Zero)
        {
            DestroyIcon(icon);
            icon = IntPtr.Zero;
        }
    }

    private IntPtr OnMessage(IntPtr window, uint message, nuint wParam, nint lParam, nuint id, nuint data)
    {
        if (message == WmNcDestroy) Dispose();
        else if (visible && message == taskbarCreated)
        {
            visible = false;
            if (!Show()) restore();
        }
        else if (visible && message == Callback && ((uint)lParam >> 16) == TaskbarIconId)
        {
            switch ((uint)lParam & 0xFFFF)
            {
                case NinSelect:
                case NinKeySelect:
                    restore();
                    break;
                case WmContextMenu:
                    ShowMenu();
                    break;
            }
            return IntPtr.Zero;
        }
        return DefSubclassProc(window, message, wParam, lParam);
    }

    private void ShowMenu()
    {
        IntPtr menu = CreatePopupMenu();
        if (menu == IntPtr.Zero) return;
        try
        {
            if (!AppendMenu(menu, 0, 1, "显示窗口") || !AppendMenu(menu, 0, 2, "退出")) return;
            var identifier = new IconIdentifier
            {
                Size = (uint)Marshal.SizeOf<IconIdentifier>(), Window = hwnd, Id = TaskbarIconId
            };
            if (Shell_NotifyIconGetRect(ref identifier, out var rect) == 0)
                ShowMenuAt(menu, rect.Left, rect.Top);
            else if (GetCursorPos(out var cursor))
                ShowMenuAt(menu, cursor.X, cursor.Y);
        }
        finally { DestroyMenu(menu); }
    }

    private void ShowMenuAt(IntPtr menu, int x, int y)
    {
        SetForegroundWindow(hwnd);
        uint command = TrackPopupMenuEx(menu, 0x01A2, x, y, hwnd, IntPtr.Zero);
        PostMessage(hwnd, 0, 0, 0);
        if (command == 1) restore();
        else if (command == 2) exit();
        else
        {
            var data = IconData();
            Shell_NotifyIcon(NimSetFocus, ref data);
        }
    }

    private NotifyIconData IconData() => new()
    {
        Size = (uint)Marshal.SizeOf<NotifyIconData>(), Window = hwnd, Id = TaskbarIconId,
        Tip = "", Info = "", InfoTitle = ""
    };

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NotifyIconData
    {
        public uint Size;
        public IntPtr Window;
        public uint Id, Flags, CallbackMessage;
        public IntPtr Icon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string Tip;
        public uint State, StateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string Info;
        public uint Version;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string InfoTitle;
        public uint InfoFlags;
        public Guid Guid;
        public IntPtr BalloonIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IconIdentifier { public uint Size; public IntPtr Window; public uint Id; public Guid Guid; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X, Y; }

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate IntPtr SubclassProc(IntPtr hwnd, uint message, nuint wParam, nint lParam, nuint id, nuint data);
    [DllImport("comctl32.dll")] private static extern bool SetWindowSubclass(IntPtr hwnd, SubclassProc proc, nuint id, nuint data);
    [DllImport("comctl32.dll")] private static extern bool RemoveWindowSubclass(IntPtr hwnd, SubclassProc proc, nuint id);
    [DllImport("comctl32.dll")] private static extern IntPtr DefSubclassProc(IntPtr hwnd, uint message, nuint wParam, nint lParam);
    [DllImport("shell32.dll", EntryPoint = "Shell_NotifyIconW")] private static extern bool Shell_NotifyIcon(uint message, ref NotifyIconData data);
    [DllImport("shell32.dll")] private static extern int Shell_NotifyIconGetRect(ref IconIdentifier identifier, out Rect rect);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern uint RegisterWindowMessage(string text);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr LoadImage(IntPtr instance, string path, uint type, int cx, int cy, uint flags);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [DllImport("user32.dll")] private static extern IntPtr CreatePopupMenu();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool AppendMenu(IntPtr menu, uint flags, nuint item, string text);
    [DllImport("user32.dll")] private static extern bool DestroyMenu(IntPtr menu);
    [DllImport("user32.dll")] private static extern uint TrackPopupMenuEx(IntPtr menu, uint flags, int x, int y, IntPtr window, IntPtr parameters);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool PostMessage(IntPtr hwnd, uint message, nuint wParam, nint lParam);
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
}
