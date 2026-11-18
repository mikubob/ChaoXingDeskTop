using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
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
            {
                correctingFields.Add(section + "." + key);
                DispatcherQueue.TryEnqueue(async () =>
                {
                    // Navigation may still be revealing the destination page.
                    // Focus only after the transition has made it visible.
                    while (contentAnimations.ContainsKey(PageHost)) await Task.Delay(16);
                    input.IsEnabled = !Busy;
                    input.StartBringIntoView();
                    input.Focus(FocusState.Programmatic);
                });
            }
        }
        else if (name == "startConfirmationRequested") EnqueueDialog(async () => await Call(await Confirm("确认本轮提交策略", args[0]?.ToString() ?? "", "按此策略开始") ? "confirmStartTasks" : "cancelStartTasks"));
        else if (name == "definitionPreviewRequested") EnqueueDialog(async () =>
        {
            bool valid = args[1]?.ToString() == "true";
            if (valid) await Call("finishDefinitionImport", await Confirm("导入前检查目标与映射", args[0]?.ToString() ?? "", "导入此定义"));
            else { await ShowText("定义被拒绝（未导入）", args[0]?.ToString() ?? ""); await Call("finishDefinitionImport", false); }
        });
        else if (name == "draftOpened" && args[0] is JsonNode copy) EnqueueDialog(() => ShowDraft(copy));
        // The backend resets its question filter whenever the account or the
        // run scope changes; the retained result controls must follow it or
        // they would keep filtering (and sending stale RPCs) on their own.
        else if (name == "questionsReset") ResetResultFilters();
    }

    private void RefreshInput()
    {
        var input = backend.State["input"];
        string signature = input?.ToJsonString() ?? "{}";
        if (signature == inputSignature) return;
        inputSignature = signature;
        if (input is not JsonObject obj || obj.Count == 0) { manualDialog?.Hide(); return; }
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
                AutomationProperties.SetName(check, text);
                check.Click += (_, _) => { if (check.IsChecked == true) selected.Add(value); else selected.Remove(value); }; panel.Children.Add(check);
            }
            else
            {
                var radio = new RadioButton { Content = Text(text), GroupName = group };
                AutomationProperties.SetName(radio, text);
                radio.Checked += (_, _) => { selected.Clear(); selected.Add(value); }; panel.Children.Add(radio);
            }
        }
        var blanks = new List<TextBox>();
        if (J.S(input, "type") == "completion" && J.I(input, "blankCount") > 0)
            for (int i = 0; i < J.I(input, "blankCount"); i++) { var blank = new TextBox { Header = $"第 {i + 1} 空", AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 72 }; blanks.Add(blank); panel.Children.Add(blank); }
        var answer = new TextBox { Header = "答案", AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 100 };
        if (!J.A(input["options"]).Any() && blanks.Count == 0 && J.S(input, "kind") != "choice") panel.Children.Add(answer);
        var feedback = new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Warning, Message = J.B(input, "random") ? "跳过后，选择题和判断题将按随机补全设置处理。" : "跳过后，此题保持待处理。" };
        var dialog = Dialog(J.S(input, "kind") == "choice" ? "章节处理" : "人工补全", Scroll(panel), "停止任务");
        if (J.S(input, "kind") == "choice")
        {
            foreach (var choice in J.A(input["choices"])) panel.Children.Add(Button(J.S(choice, "label"), async () => { await Call("reply", J.S(choice, "value"), false); dialog.Hide(); }));
        }
        else
        {
            panel.Children.Add(feedback);
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
        bool StillWanted() => inputSignature == signature && backend.State["input"] is JsonObject active && active.Count > 0;
        bool suspended = false;
        dialog.Closing += (_, args) =>
        {
            if (!StillWanted() || closing) return;
            suspended = closePending != null;
            args.Cancel = !suspended;
        };
        do
        {
            if (closePending is { } decision) await decision.Task;
            suspended = false;
            await ShowModal(dialog, manual: true, stillWanted: StillWanted);
        } while ((suspended || closePending != null) && !closing && StillWanted());
    }

    private async Task ShowDraft(JsonNode copy)
    {
        var feedback = new InfoBar { IsOpen = true, IsClosable = false, Message = J.S(backend.State, "draftFeedback") };
        var panel = Column(Text(J.S(copy["source"], "title"), 17));
        var editorControls = new List<Control>();
        // Track edits before the bridge responds, including edits made while a save is in flight.
        int edits = 0, savedEdits = 0;
        bool HasUnsavedEdits() => edits != savedEdits || J.B(backend.State, "draftDirty");
        if (J.S(copy, "kind") == "discussion")
        {
            var editor = new TextBox { Header = "讨论回复", Text = J.S(copy, "content"), AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = 270 };
            editor.TextChanging += async (sender, _) => { edits++; await Call("setDraftContent", sender.Text); };
            panel.Children.Add(editor); editorControls.Add(editor);
        }
        else
        {
            foreach (var question in J.A(copy["source"]?["questions"]))
            {
                string id = J.S(question, "id"); var initial = await Call("draftAnswer", id);
                var questionTitle = Text(J.S(question, "title"), 15);
                panel.Children.Add(questionTitle);
                if (J.S(question, "type") == "text")
                {
                    var text = new TextBox { Text = J.S(initial, "text"), AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 100 };
                    AutomationProperties.SetLabeledBy(text, questionTitle);
                    text.TextChanging += async (sender, _) => { edits++; await Call("setDraftAnswer", id, new { text = sender.Text }); };
                    panel.Children.Add(text); editorControls.Add(text);
                }
                else
                {
                    var selected = J.A(initial?["option_ids"]).Select(n => n.ToString()).ToHashSet();
                    var boxes = new List<(string Id, CheckBox Box)>(); bool changing = false;
                    foreach (var option in J.A(question["options"]))
                    {
                        string optionId = J.S(option, "id"), optionText = J.S(option, "text");
                        var check = new CheckBox { Content = Text(optionText), IsChecked = selected.Contains(optionId) };
                        AutomationProperties.SetName(check, optionText);
                        boxes.Add((optionId, check)); panel.Children.Add(check); editorControls.Add(check);
                        check.Click += async (_, _) =>
                        {
                            if (changing) return; changing = true;
                            if (J.S(question, "type") == "single" && check.IsChecked == true) foreach (var pair in boxes) if (pair.Box != check) pair.Box.IsChecked = false;
                            changing = false; edits++;
                            await Call("setDraftAnswer", id, new { option_ids = boxes.Where(p => p.Box.IsChecked == true).Select(p => p.Id).ToArray() });
                        };
                    }
                }
            }
        }
        panel.Children.Add(Row(Button("复制副本", async () => await Call("exportDraftCopy", true)), Button("导出副本…", () => FileAction("exportDraftCopy", [false], true, "txt", "draft-copy"))));
        var content = new Grid { RowSpacing = 8 };
        content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        content.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        content.Children.Add(Scroll(panel));
        content.Children.Add(feedback);
        Grid.SetRow(feedback, 1);
        var dialog = Dialog("编辑本机副本", content);
        dialog.PrimaryButtonText = "保存本机副本";
        void Update()
        {
            if (dialog.SecondaryButtonText.Length == 0) UpdateDraftFeedback(feedback);
            foreach (var control in editorControls) control.IsEnabled = J.B(backend.State, "draftEditable");
        }
        refreshers.Add(Update);
        Update();
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            args.Cancel = true;
            var deferral = args.GetDeferral();
            var savingEdits = edits;
            try
            {
                dialog.SecondaryButtonText = "";
                if (await SaveDraftFromEditor(feedback)) savedEdits = Math.Max(savedEdits, savingEdits);
            }
            finally { deferral.Complete(); }
        };
        dialog.Closing += (_, args) =>
        {
            if (!HasUnsavedEdits() || args.Result == ContentDialogResult.Secondary) return;
            args.Cancel = true;
            feedback.Severity = InfoBarSeverity.Warning;
            feedback.Message = "放弃本次修改后，未保存的内容将丢失。";
            feedback.IsOpen = true;
            dialog.SecondaryButtonText = "放弃本次修改";
        };
        try { await ShowModal(dialog); } finally { refreshers.Remove(Update); }
    }

    private void UpdateDraftFeedback(InfoBar feedback)
    {
        var scoped = backend.State["uiFeedback"]?["draft"];
        feedback.Message = J.S(scoped, "message", J.S(backend.State, "draftFeedback"));
        feedback.IsOpen = feedback.Message.Length > 0;
        feedback.Severity = J.S(scoped, "tone", J.S(backend.State, "draftFeedbackTone")) switch
        {
            "danger" => InfoBarSeverity.Error,
            "warning" => InfoBarSeverity.Warning,
            "success" => InfoBarSeverity.Success,
            _ => InfoBarSeverity.Informational,
        };
    }

    private async Task<bool> SaveDraftFromEditor(InfoBar feedback)
    {
        bool saved = (await Call("saveDraftCopy"))?.ToString() == "true";
        UpdateDraftFeedback(feedback);
        if (!saved && feedback.Severity != InfoBarSeverity.Error)
        {
            feedback.Message = J.S(Summary, "message", "保存失败，请检查磁盘空间与副本版本后重试。");
            feedback.Severity = InfoBarSeverity.Error;
            feedback.IsOpen = true;
        }
        return saved;
    }
}
