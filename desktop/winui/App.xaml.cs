using Microsoft.UI.Xaml;

namespace ChaoxingDesktop;

public partial class App : Application
{
    private MainWindow? window;
    public App()
    {
        UnhandledException += (_, args) =>
        {
            var command = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(command, "--smoke-test");
            if (index >= 0 && index + 1 < command.Length)
            {
                var directory = Path.GetFullPath(command[index + 1]);
                Directory.CreateDirectory(directory);
                File.WriteAllText(Path.Combine(directory, "result.json"), System.Text.Json.JsonSerializer.Serialize(new { success = false, message = args.Exception.ToString() }));
            }
        };
        InitializeComponent();
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        window = new MainWindow();
        window.Activate();
    }
}
