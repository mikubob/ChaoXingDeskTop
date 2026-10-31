using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Dispatching;

namespace ChaoxingDesktop;

internal sealed class BackendClient : IDisposable
{
    private readonly DispatcherQueue dispatcher;
    private readonly Dictionary<int, TaskCompletionSource<JsonNode?>> pending = new();
    private readonly SemaphoreSlim writer = new(1);
    private Process? process;
    private int sequence;
    private bool disposed;
    private Task? shutdown;
    public bool Connected => !disposed && process is { HasExited: false };
    public JsonObject State { get; } = new();
    public event Action<JsonObject>? Received;
    public event Action<string>? Failed;

    public BackendClient(DispatcherQueue dispatcher) { this.dispatcher = dispatcher; }

    public void Start()
    {
        var root = Environment.GetEnvironmentVariable("CHAOXING_BACKEND_ROOT");
        if (string.IsNullOrEmpty(root))
        {
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
                if (File.Exists(Path.Combine(dir.FullName, "run.py"))) { root = dir.FullName; break; }
        }
        var companion = Path.Combine(AppContext.BaseDirectory, "ChaoxingWorker.exe");
        var python = Environment.GetEnvironmentVariable("CHAOXING_PYTHON");
        if (string.IsNullOrEmpty(python) && root != null)
            python = new[] { ".venv", ".venv-build", ".venv-ci" }.Select(v => Path.Combine(root, v, "Scripts", "python.exe")).FirstOrDefault(File.Exists);
        var info = new ProcessStartInfo
        {
            FileName = File.Exists(companion) ? companion : python ?? throw new InvalidOperationException("找不到 Python 环境，请按开发指南准备 .venv。"),
            WorkingDirectory = root ?? AppContext.BaseDirectory,
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
        };
        if (!File.Exists(companion))
        {
            info.ArgumentList.Add("-u");
            info.ArgumentList.Add(Path.Combine(root!, "run.py"));
        }
        info.ArgumentList.Add("--bridge");
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        var args = Environment.GetCommandLineArgs();
        int smokeIndex = Array.IndexOf(args, "--smoke-test");
        if (smokeIndex >= 0 && smokeIndex + 1 < args.Length)
            info.Environment["CHAOXING_DESKTOP_DATA"] = Path.Combine(Path.GetFullPath(args[smokeIndex + 1]), "profile-" + Guid.NewGuid().ToString("N"));
        process = Process.Start(info) ?? throw new InvalidOperationException("本地后台无法启动。");
        // Drain diagnostics without displaying raw traces or credentials.
        _ = Task.Run(async () => { while (await process.StandardError.ReadLineAsync() != null) { } });
        _ = ReadAsync();
    }

    private async Task ReadAsync()
    {
        try
        {
            while (process != null && await process.StandardOutput.ReadLineAsync() is { } line)
            {
                var message = JsonNode.Parse(line) as JsonObject ?? throw new InvalidDataException();
                dispatcher.TryEnqueue(() => Handle(message));
            }
        }
        catch { /* Report generic failure below; protocol contents can be secret. */ }
        dispatcher.TryEnqueue(() =>
        {
            foreach (var completion in pending.Values) completion.TrySetException(new IOException("本地后台已断开。"));
            pending.Clear();
            if (!disposed) Failed?.Invoke("本地后台已断开，请关闭窗口后重试。");
        });
    }

    private void Handle(JsonObject message)
    {
        var type = J.S(message, "type");
        if (type == "response")
        {
            if (pending.Remove(J.I(message, "id"), out var completion))
            {
                if (message["error"] != null) completion.TrySetException(new InvalidOperationException(J.S(message, "error")));
                else completion.TrySetResult(message["result"]?.DeepClone());
            }
            return;
        }
        if (type is "ready" or "state" && message["data"] is JsonObject data)
            foreach (var pair in data) State[pair.Key] = pair.Value?.DeepClone();
        Received?.Invoke(message);
    }

    public async Task<JsonNode?> Call(string method, object?[]? args = null, string? path = null)
    {
        if (disposed || process == null || process.HasExited) throw new IOException("本地后台尚未连接。");
        var id = ++sequence;
        var completion = new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending.Add(id, completion);
        var request = JsonSerializer.Serialize(new { id, method, args = args ?? Array.Empty<object>(), path = path ?? "" });
        await writer.WaitAsync();
        try { await process.StandardInput.WriteLineAsync(request); await process.StandardInput.FlushAsync(); }
        catch { pending.Remove(id); throw; }
        finally { writer.Release(); }
        try { return await completion.Task.WaitAsync(TimeSpan.FromSeconds(30)); }
        finally { pending.Remove(id); }
    }

    public Task ShutdownAsync() => shutdown ??= ShutdownCoreAsync();

    private async Task ShutdownCoreAsync()
    {
        disposed = true;
        if (process is { } child)
        {
            try
            {
                child.StandardInput.Close();
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
                try { await child.WaitForExitAsync(timeout.Token); }
                catch (OperationCanceledException) { child.Kill(entireProcessTree: true); await child.WaitForExitAsync(); }
            }
            catch (InvalidOperationException) { }
            finally { child.Dispose(); process = null; }
        }
    }

    public void Dispose()
    {
        // Closed must never wait on the UI thread. Normal closure already awaits
        // ShutdownAsync while the native window is hidden.
        if (disposed) return;
        disposed = true;
        try { if (process is { HasExited: false }) process.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
        finally { process?.Dispose(); process = null; }
    }
}

internal static class J
{
    public static string S(JsonNode? node, string key, string fallback = "") => node?[key]?.ToString() ?? fallback;
    public static bool B(JsonNode? node, string key) => node?[key]?.ToString().Equals("true", StringComparison.OrdinalIgnoreCase) == true;
    public static int I(JsonNode? node, string key) => int.TryParse(S(node, key), out var value) ? value : 0;
    public static IEnumerable<JsonNode> A(JsonNode? node) => (node as JsonArray)?.OfType<JsonNode>() ?? Enumerable.Empty<JsonNode>();
}
