using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private void HandleEvent(string name, JsonArray args)
    {
        if (name == "coursesLoaded") coursesLoadedPending = true;
        else if (name == "pageRequested") Navigation.SelectedIndex = args[0]?.GetValue<int>() ?? 0;
        else if (name == "fieldRequested")
        {
            string section = args[0]?.ToString() ?? "", key = args[1]?.ToString() ?? "";
            if (fieldGroups.TryGetValue(section + "." + key, out var group))
            {
                Navigation.SelectedIndex = group == "learning" ? 1 : group == "advanced" ? 3 : 2;
                if (Navigation.SelectedIndex == 2) revealServiceGroup?.Invoke(group);
            }
            if (fields.TryGetValue(section + "." + key, out var input))
                DispatcherQueue.TryEnqueue(() => { input.IsEnabled = true; input.StartBringIntoView(); input.Focus(FocusState.Programmatic); });
        }
        else if (name == "startConfirmationRequested") EnqueueDialog(async () => await Call(await Confirm("确认本轮提交策略", args[0]?.ToString() ?? "", "按此策略开始") ? "confirmStartTasks" : "cancelStartTasks"));
        else if (name == "definitionPreviewRequested") EnqueueDialog(async () =>
        {
            bool valid = args[1]?.ToString() == "true";
            if (valid) await Call("finishDefinitionImport", await Confirm("导入前检查目标与映射", args[0]?.ToString() ?? "", "导入此定义"));
            else { await ShowText("定义被拒绝（未导入）", args[0]?.ToString() ?? ""); await Call("finishDefinitionImport", false); }
        });
        else if (name == "draftOpened" && args[0] is JsonNode copy) EnqueueDialog(() => ShowDraft(copy));
    }

    private void RefreshInput()
    {
        var input = backend.State["input"];
        string signature = input?.ToJsonString() ?? "{}";
        if (signature == inputSignature) return;
        inputSignature = signature;
        if (input is not JsonObject obj || obj.Count == 0) { manualDialog?.Hide(); return; }
        if (smokeDestination != null) { _ = Call("reply", "", false); return; }
        manualDialog?.Hide();
        var clone = input.DeepClone();
        EnqueueDialog(() => ShowInput(clone, signature));
    }
    private async Task ShowInput(JsonNode input, string signature)
    {
        if (inputSignature != signature) return;
        var panel = Column(Text(J.S(input, "message", "检查题干与选项，填写答案后继续。"), 12, true), Text(J.S(input, "title"), 17));
        foreach (var url in J.A(input["images"]))
        {
            if (Uri.TryCreate(url.ToString(), UriKind.Absolute, out var uri) && uri.Scheme is "https" or "http")
            {
                var image = new Image { Source = new BitmapImage(uri), MaxHeight = 220, Stretch = Microsoft.UI.Xaml.Media.Stretch.Uniform };
                var failed = Text("图片加载失败，请在学习平台查看原题。", 12, true); failed.Visibility = Visibility.Collapsed;
                image.ImageFailed += (_, _) => failed.Visibility = Visibility.Visible;
                panel.Children.Add(image); panel.Children.Add(failed);
            }
        }
        var selected = new HashSet<string>();
        string group = Guid.NewGuid().ToString();
        foreach (var option in J.A(input["options"]))
        {
            var value = J.S(option, "value"); var text = value + " " + J.S(option, "label");
            if (J.S(input, "type") == "multiple")
            {
                var check = new CheckBox { Content = Text(text) };
                check.Click += (_, _) => { if (check.IsChecked == true) selected.Add(value); else selected.Remove(value); }; panel.Children.Add(check);
            }
            else
            {
                var radio = new RadioButton { Content = Text(text), GroupName = group };
                radio.Checked += (_, _) => { selected.Clear(); selected.Add(value); }; panel.Children.Add(radio);
            }
        }
        var blanks = new List<TextBox>();
        if (J.S(input, "type") == "completion" && J.I(input, "blankCount") > 0)
            for (int i = 0; i < J.I(input, "blankCount"); i++) { var blank = new TextBox { Header = $"第 {i + 1} 空", AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 72 }; blanks.Add(blank); panel.Children.Add(blank); }
        var answer = new TextBox { PlaceholderText = "填写答案", AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 100 };
        if (!J.A(input["options"]).Any() && blanks.Count == 0 && J.S(input, "kind") != "choice") panel.Children.Add(answer);
        var feedback = new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Warning, Message = J.B(input, "random") ? "跳过后，选择题和判断题将按随机补全设置处理。" : "跳过后，此题保持待处理。" };
        panel.Children.Add(feedback);
        var dialog = Dialog(J.S(input, "kind") == "choice" ? "章节处理" : "人工补全", Scroll(panel), "停止任务");
        if (J.S(input, "kind") == "choice")
        {
            foreach (var choice in J.A(input["choices"])) panel.Children.Add(Button(J.S(choice, "label"), async () => { await Call("reply", J.S(choice, "value"), false); dialog.Hide(); }));
        }
        else
        {
            dialog.PrimaryButtonText = "使用答案"; dialog.SecondaryButtonText = "跳过此题"; dialog.DefaultButton = ContentDialogButton.Primary;
            dialog.PrimaryButtonClick += async (_, args) =>
            {
                args.Cancel = true;
                string value = blanks.Count > 0 ? JsonSerializer.Serialize(blanks.Select(b => b.Text.Trim()).ToArray()) : selected.Count > 0 ? string.Concat(selected.OrderBy(s => s)) : answer.Text.Trim();
                if (blanks.Any(b => b.Text.Trim().Length == 0) || value.Length == 0) { feedback.Message = "请先选择或填写完整答案，也可跳过此题。"; return; }
                await Call("reply", value, false); dialog.Hide();
            };
            dialog.SecondaryButtonClick += async (_, _) => await Call("reply", "", false);
        }
        dialog.CloseButtonClick += async (_, _) => await Call("reply", "", true);
        manualDialog = currentDialog = dialog;
        dialog.Closing += (_, args) => { if (inputSignature == signature && backend.State["input"] is JsonObject active && active.Count > 0) args.Cancel = true; };
        try { await dialog.ShowAsync(); } finally { if (manualDialog == dialog) manualDialog = null; currentDialog = null; }
    }

    private async Task ShowDraft(JsonNode copy)
    {
        var feedback = new InfoBar { IsOpen = true, IsClosable = false, Message = J.S(backend.State, "draftFeedback") };
        var panel = Column(Text("独立的本机人工副本。保存后可复制或导出。", 12, true), Text(J.S(copy["source"], "title"), 17));
        var editorControls = new List<Control>();
        if (J.S(copy, "kind") == "discussion")
        {
            var editor = new TextBox { Text = J.S(copy, "content"), AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = 270 };
            editor.TextChanged += async (_, _) => await Call("setDraftContent", editor.Text); panel.Children.Add(editor); editorControls.Add(editor);
        }
        else
        {
            foreach (var question in J.A(copy["source"]?["questions"]))
            {
                string id = J.S(question, "id"); var initial = await Call("draftAnswer", id);
                panel.Children.Add(Text(J.S(question, "title"), 15));
                if (J.S(question, "type") == "text")
                {
                    var text = new TextBox { Text = J.S(initial, "text"), AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 100 };
                    text.TextChanged += async (_, _) => await Call("setDraftAnswer", id, new { text = text.Text }); panel.Children.Add(text); editorControls.Add(text);
                }
                else
                {
                    var selected = J.A(initial?["option_ids"]).Select(n => n.ToString()).ToHashSet();
                    var boxes = new List<(string Id, CheckBox Box)>(); bool changing = false;
                    foreach (var option in J.A(question["options"]))
                    {
                        string optionId = J.S(option, "id"); var check = new CheckBox { Content = Text(J.S(option, "text")), IsChecked = selected.Contains(optionId) };
                        boxes.Add((optionId, check)); panel.Children.Add(check); editorControls.Add(check);
                        check.Click += async (_, _) =>
                        {
                            if (changing) return; changing = true;
                            if (J.S(question, "type") == "single" && check.IsChecked == true) foreach (var pair in boxes) if (pair.Box != check) pair.Box.IsChecked = false;
                            changing = false; await Call("setDraftAnswer", id, new { option_ids = boxes.Where(p => p.Box.IsChecked == true).Select(p => p.Id).ToArray() });
                        };
                    }
                }
            }
        }
        var update = new Action(() => { feedback.Message = J.S(backend.State, "draftFeedback"); foreach (var control in editorControls) control.IsEnabled = J.B(backend.State, "draftEditable"); });
        refreshers.Add(update); update();
        panel.Children.Add(feedback);
        panel.Children.Add(Row(Button("复制副本", async () => await Call("exportDraftCopy", true)), Button("导出副本…", () => FileAction("exportDraftCopy", [false], true, "txt", "draft-copy"))));
        var dialog = Dialog("人工编辑副本 · 仅本地", Scroll(panel)); dialog.PrimaryButtonText = "保存本机副本";
        dialog.PrimaryButtonClick += async (_, args) => { args.Cancel = true; await Call("saveDraftCopy"); };
        bool discard = false;
        dialog.CloseButtonClick += (_, args) =>
        {
            if (J.B(backend.State, "draftDirty") && !discard)
            {
                args.Cancel = true; feedback.Message = "尚有未保存修改。请先保存，或点击下方「放弃本次修改」。";
                if (!panel.Children.OfType<Button>().Any()) panel.Children.Add(Button("放弃本次修改", () => { discard = true; dialog.Hide(); return Task.CompletedTask; }));
            }
        };
        dialog.Closing += (_, args) => { if (J.B(backend.State, "draftDirty") && !discard) { args.Cancel = true; feedback.Message = "尚有未保存修改。请先保存，或点击「关闭」选择放弃修改。"; } };
        currentDialog = dialog; try { await dialog.ShowAsync(); } finally { refreshers.Remove(update); currentDialog = null; }
    }
}
