using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Media;

namespace ChaoxingDesktop;

public sealed partial class MainWindow
{
    private FrameworkElement BuildCourses()
    {
        accounts = new ComboBox { Header = "当前学习账号", PlaceholderText = "尚未选择账号", DisplayMemberPath = "Display", HorizontalAlignment = HorizontalAlignment.Stretch };
        idleControls.Add(accounts);
        accounts.SelectionChanged += async (_, _) => { if (!refreshing && accounts.SelectedItem is AccountItem item) await Call("switchAccount", item.Id); };
        refreshers.Add(() =>
        {
            var rows = J.A(backend.State["accountRows"]).Select(n => new AccountItem(J.S(n, "id"), J.S(n, "display"))).ToArray();
            var old = (accounts.ItemsSource as AccountItem[]) ?? [];
            if (!old.SequenceEqual(rows)) accounts.ItemsSource = rows;
            accounts.SelectedItem = rows.FirstOrDefault(n => n.Id == J.S(Summary, "activeAccount"));
        });
        var add = Button("添加账号", async () => await Call("addAccount"), true);
        var remove = Button("删除账号…", async () =>
        {
            if (accounts.SelectedItem is not AccountItem account || account.Id != J.S(Summary, "activeAccount")) return;
            if (await Confirm("删除学习账号？", $"将从本机删除「{account.Display}」及其 Cookie。再次使用该账号时需要重新登录。", "删除此账号"))
                await Call("removeAccount", account.Id);
        }, true);
        remove.Name = "RemoveAccount";
        refreshers.Add(() => remove.IsEnabled = !Busy && accounts.SelectedItem is AccountItem account && account.Id == J.S(Summary, "activeAccount"));
        var cancelLogin = Button("取消浏览器登录", async () => await Call("cancelBrowserLogin"));
        refreshers.Add(() => cancelLogin.Visibility = J.B(Summary, "browserLogin") ? Visibility.Visible : Visibility.Collapsed);
        var user = BoundText("手机号 / 账号", "common", "username");
        refreshers.Add(() => user.IsReadOnly = J.S(Summary, "activeAccount") != "");
        var password = BoundPassword("登录密码", "common", "password");
        var label = new TextBox { Name = "AccountNote", Header = "当前账号备注", MaxLength = 80 };
        string noteAccountId = "", storedNote = "";
        var saveNote = Button("保存备注", async () =>
        {
            if (noteAccountId.Length == 0 || J.S(Summary, "activeAccount") != noteAccountId ||
                (accounts.SelectedItem as AccountItem)?.Id != noteAccountId)
            {
                Error("请先选择要管理的账号，等待账号切换完成后再保存备注。");
                return;
            }
            await Call("renameAccount", label.Text);
        });
        saveNote.VerticalAlignment = VerticalAlignment.Bottom;
        refreshers.Add(() =>
        {
            string activeId = J.S(Summary, "activeAccount");
            var account = J.A(backend.State["accountRows"]).FirstOrDefault(row => J.S(row, "id") == activeId);
            string savedLabel = J.S(account, "label");
            bool changed = activeId != noteAccountId;
            if (changed || savedLabel != storedNote && label.FocusState == FocusState.Unfocused)
                label.Text = account == null ? "" : savedLabel;
            noteAccountId = activeId;
            storedNote = savedLabel;
            string username = J.S(account, "username");
            accounts.Header = username.Length > 0 && username != savedLabel ? $"当前学习账号（{username}）" : "当前学习账号";
            label.IsEnabled = saveNote.IsEnabled = account != null && !Busy;
        });
        var savedCookieLogin = Button("用 Cookie 读取课程", async () =>
        {
            await backend.Call("setValue", ["common", "use_cookies", "true"]);
            await Call("fetchCourses");
        }, true);
        savedCookieLogin.Name = "SavedCookieLogin";
        var passwordLogin = Button("密码登录并读取课程", async () =>
        {
            await backend.Call("setValue", ["common", "use_cookies", "false"]);
            await Call("fetchCourses");
        }, true);
        passwordLogin.VerticalAlignment = VerticalAlignment.Bottom;
        var passwordForm = new Grid { ColumnSpacing = 8, ChildrenTransitions = LayoutTransitions() };
        passwordForm.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        passwordForm.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        passwordForm.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        passwordForm.Children.Add(user); passwordForm.Children.Add(password); passwordForm.Children.Add(passwordLogin);
        Grid.SetColumn(password, 1); Grid.SetColumn(passwordLogin, 2);
        var clearCookie = Button("清除 Cookie…", async () =>
        {
            if (await Confirm("清除 Cookie？", "清除当前账号的本机会话，下次使用需要重新登录。", "清除 Cookie"))
                await Call("fileAction", "clear_cookie");
        }, true);
        var cookieStatus = Text("未保存 Cookie", 12, true);
        cookieStatus.VerticalAlignment = VerticalAlignment.Center;
        var importCookie = Button("导入 Cookie…", () => FileAction("fileAction", ["import_cookie"], false, "txt"), true);
        var browserLogin = Button("浏览器登录并读取课程", async () => await Call("browserLogin"), true);
        var accentStyle = (Style)Application.Current.Resources["AccentButtonStyle"];
        savedCookieLogin.Style = accentStyle;
        refreshers.Add(() =>
        {
            bool saved = J.B(Summary, "cookie");
            savedCookieLogin.Visibility = clearCookie.Visibility = saved ? Visibility.Visible : Visibility.Collapsed;
            cookieStatus.Visibility = saved ? Visibility.Collapsed : Visibility.Visible;
            if (saved) browserLogin.ClearValue(FrameworkElement.StyleProperty);
            else browserLogin.Style = accentStyle;
        });
        var accountActions = Row(add, remove);
        accountActions.VerticalAlignment = VerticalAlignment.Bottom;
        var loginHeading = Text("登录方式", 16);
        AutomationProperties.SetHeadingLevel(loginHeading, AutomationHeadingLevel.Level2);
        returnToCourses = Button("返回我的课程 →", () => { ShowCourseView(true); return Task.CompletedTask; });
        returnToCourses.Visibility = Visibility.Collapsed;
        var accountCard = Card(Column(StretchRow(accounts, accountActions),
            StretchRow(label, saveNote),
            new Border { Height = 1, Background = ThemeBrush("BorderBrush"), Margin = new Thickness(0, 6, 0, 6) },
            loginHeading,
            Row(browserLogin, Button("离线演示", async () => await Call("loadDemo"), true), cancelLogin),
            passwordForm,
            Row(savedCookieLogin, cookieStatus, importCookie, clearCookie),
            StretchRow(BoundToggle("启动时使用已保存会话登录", "desktop", "auto_login"), returnToCourses)));
        accountCard.MaxWidth = 880;
        accountCard.HorizontalAlignment = HorizontalAlignment.Center;
        accountCard.VerticalAlignment = VerticalAlignment.Center;
        var accountScroll = Scroll(accountCard);
        accountScroll.VerticalContentAlignment = VerticalAlignment.Center;
        // ScrollViewer measures scrollable content differently from Grid.
        // Give the card a viewport-bounded width before centering it, so its
        // natural control widths cannot push the card outside the viewport.
        void FitAccountCard() => accountCard.Width = Math.Min(880, Math.Max(0, accountScroll.ActualWidth - 20));
        accountScroll.SizeChanged += (_, _) => FitAccountCard();
        FitAccountCard();
        var accountPage = new Grid();
        accountPage.Children.Add(accountScroll);
        courseCount = Text("", 16);
        var search = new TextBox { PlaceholderText = "搜索课程名称或 ID", Width = 210 };
        AutomationProperties.SetName(search, "搜索课程");
        courseSearch = search;
        search.TextChanged += async (_, _) => { if (!refreshing) await Call("searchCourses", search.Text); };
        // The backend clears its course search when the account or run scope
        // changes; the box must show that instead of a stale local query.
        refreshers.Add(() =>
        {
            string query = J.S(Summary, "courseSearch");
            if (search.Text != query) search.Text = query;
        });
        var ids = new ToggleButtonCompat("显示 ID", value => { showIds = value; RefreshCourses(); });
        var toolbar = StretchRow(courseCount, Row(search, Button("全选可见", async () => await Call("selectVisible", true), true), Button("取消选择", async () => await Call("selectVisible", false), true), ids));
        courses = new ListView { SelectionMode = ListViewSelectionMode.None, ItemTemplate = (DataTemplate)Root.Resources["CourseTemplate"], HorizontalContentAlignment = HorizontalAlignment.Stretch };
        courses.ItemContainerStyle = StretchListStyle();
        idleControls.Add(courses);
        emptyCourses = Text("", 16, true);
        emptyCourses.HorizontalAlignment = HorizontalAlignment.Center; emptyCourses.VerticalAlignment = VerticalAlignment.Center;
        var courseArea = new Grid(); courseArea.Children.Add(courses); courseArea.Children.Add(emptyCourses);
        selectedText = Text("已选择 0 门课程", 16);
        var hint = Text("", 12, true);
        var start = Button("开始所选课程", async () => await Call("startTasks"), false, "#2563EB");
        refreshers.Add(() =>
        {
            int selected = J.I(Summary, "selectedCount"), hidden = J.I(Summary, "hiddenCount");
            selectedText.Text = $"已选择 {selected} 门课程";
            courseCount.Text = $"{J.I(Summary, "courseCount")} 门课程";
            start.IsEnabled = !Busy && selected > 0;
            hint.Text = hidden > 0 ? $"含 {hidden} 门被搜索隐藏的已选课程" : "";
            hint.Visibility = hidden > 0 ? Visibility.Visible : Visibility.Collapsed;
        });
        var selection = Column(selectedText, hint);
        selection.Spacing = 4;
        selection.VerticalAlignment = VerticalAlignment.Center;
        var courseActions = Row(Button("查看本轮策略", async () => await ShowText("本轮策略摘要", J.S(backend.State, "strategySummary"))), start);
        courseActions.VerticalAlignment = VerticalAlignment.Center;
        var bottom = Card(StretchRow(selection, courseActions));
        var accountName = Text("", 13, true);
        refreshers.Add(() => accountName.Text = J.B(Summary, "demo") ? "示例课程" : "当前账号：" + (accounts.SelectedItem is AccountItem active ? active.Display : "已连接"));
        var courseHeader = StretchRow(accountName, Row(Button("← 账号管理", () => { ShowCourseView(false); return Task.CompletedTask; }, true), Button("重新读取课程", async () => await Call("fetchCourses"), true)));
        var grid = new Grid { RowSpacing = 14 };
        foreach (var height in new[] { GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto }) grid.RowDefinitions.Add(new RowDefinition { Height = height });
        FrameworkElement[] rows2 = [courseHeader, toolbar, courseArea, bottom];
        for (int i = 0; i < rows2.Length; i++) { grid.Children.Add(rows2[i]); Grid.SetRow(rows2[i], i); }
        courseHost = new Grid();
        courseViews = [accountPage, grid];
        foreach (var view in courseViews) courseHost.Children.Add(view);
        ClipContent(courseHost);
        SwitchContent(courseHost, courseViews, 0, animate: false);
        return courseHost;
    }

    private static Style StretchListStyle()
    {
        var style = new Style(typeof(ListViewItem));
        style.Setters.Add(new Setter(Control.HorizontalContentAlignmentProperty, HorizontalAlignment.Stretch));
        style.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(0)));
        return style;
    }
    private void RefreshCourses()
    {
        if (courses == null) return;
        SyncRows("courses", courses, J.A(backend.State["courses"]).Select(n => new RecordRow(n, true, showIds)));
        if (emptyCourses != null)
        {
            emptyCourses.Visibility = courses.Items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            emptyCourses.Text = J.I(Summary, "courseCount") > 0
                ? "没有匹配的课程。换个关键词试试，已选课程会保留。"
                : "没有可显示的课程。可以重新读取课程或切换账号。";
        }
    }
    private async void CourseChecked(object sender, RoutedEventArgs e)
    {
        if (!refreshing && sender is CheckBox { Tag: RecordRow row } check)
            await Call("selectCourse", J.S(row.Data, "courseId"), J.S(row.Data, "clazzId"), check.IsChecked == true);
    }

    private async void CourseCardTapped(object sender, Microsoft.UI.Xaml.Input.TappedRoutedEventArgs e)
    {
        if (refreshing || Busy || sender is not Border { Tag: RecordRow row } card) return;
        // A checkbox owns its click (including Space). Its bubbled tap must not
        // toggle the same course for a second time.
        for (DependencyObject? source = e.OriginalSource as DependencyObject; source != null && source != card; source = VisualTreeHelper.GetParent(source))
            if (source is CheckBox) return;
        e.Handled = true;
        await Call("selectCourse", J.S(row.Data, "courseId"), J.S(row.Data, "clazzId"), !row.Selected);
    }

    private TextBox BoundText(string title, string section, string key, bool multiline = false)
    {
        var input = new TextBox { Header = title, Text = Value(section, key), HorizontalAlignment = HorizontalAlignment.Stretch, AcceptsReturn = multiline, TextWrapping = multiline ? TextWrapping.Wrap : TextWrapping.NoWrap, MinHeight = multiline ? 130 : 38 };
        AutomationProperties.SetName(input, title); fields[section + "." + key] = input; idleControls.Add(input);
        input.TextChanged += async (_, _) => { if (!refreshing) await Call("setValue", section, key, input.Text); };
        refreshers.Add(() => { if (!correctingFields.Contains(section + "." + key) && input.FocusState == FocusState.Unfocused && input.Text != Value(section, key)) input.Text = Value(section, key); });
        return input;
    }
    private PasswordBox BoundPassword(string title, string section, string key)
    {
        var input = new PasswordBox { Header = title, Password = Value(section, key), HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(input, title); fields[section + "." + key] = input; idleControls.Add(input);
        input.PasswordChanged += async (_, _) => { if (!refreshing) await Call("setValue", section, key, input.Password); };
        refreshers.Add(() => { if (input.FocusState == FocusState.Unfocused && input.Password != Value(section, key)) input.Password = Value(section, key); });
        return input;
    }
    private ToggleSwitch BoundToggle(string title, string section, string key)
    {
        var input = new ToggleSwitch { Header = title, IsOn = Value(section, key).Equals("true", StringComparison.OrdinalIgnoreCase), OnContent = "已启用", OffContent = "已关闭" };
        string fieldId = section + "." + key;
        fields[fieldId] = input;
        idleControls.Add(input);
        string? confirmation = fieldId switch
        {
            "tiku.submit" => "运行所选真实课程时，填完的章节测验将提交到平台，可能包含随机答案。",
            "discussion.auto_submit" => "启用讨论任务处理后，生成的回复将发布到所选真实课程的讨论区。",
            "activity.auto_submit" => "启用投票／问卷处理后，通过整卷校验的答案将提交到所选真实课程。",
            _ => null,
        };
        bool resetting = false;
        input.Toggled += async (_, _) =>
        {
            if (refreshing || resetting) return;
            bool enable = input.IsOn;
            if (enable && confirmation != null)
            {
                resetting = true;
                input.IsOn = false;
                resetting = false;
                if (!await Confirm(title + "？", confirmation + "首次按新策略开始时，会显示当前账号及所选课程数量供确认。", "启用自动提交")) return;
            }
            await Call("setValue", section, key, enable ? "true" : "false");
        };
        refreshers.Add(() => input.IsOn = Value(section, key).Equals("true", StringComparison.OrdinalIgnoreCase)); return input;
    }
    private StackPanel BuildGroups(string name, FrameworkElement? headerAction = null, UIElement? footer = null, bool showHeading = true)
    {
        var stack = Column();
        foreach (var group in J.A(backend.State["schema"]?[name]))
        {
            var content = Column();
            if (showHeading)
            {
                var title = Text(J.S(group, "title"), 18);
                AutomationProperties.SetHeadingLevel(title, AutomationHeadingLevel.Level2);
                content.Children.Add(headerAction != null && stack.Children.Count == 0 ? StretchRow(title, headerAction) : title);
            }
            if (J.S(group, "description") is { Length: > 0 } description) content.Children.Add(Text(description, 12, true));
            var form = new Grid { ColumnSpacing = 28, RowSpacing = 18, ChildrenTransitions = LayoutTransitions() };
            foreach (var spec in J.A(group["fields"])) { fieldGroups[J.S(spec, "section") + "." + J.S(spec, "key")] = name; form.Children.Add(BuildField(spec)); }
            void Arrange()
            {
                int columns = form.ActualWidth > 780 ? 2 : 1;
                if (form.ColumnDefinitions.Count == columns) return;
                form.ColumnDefinitions.Clear(); form.RowDefinitions.Clear();
                for (int i = 0; i < columns; i++) form.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                for (int i = 0; i < (form.Children.Count + columns - 1) / columns; i++) form.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                for (int i = 0; i < form.Children.Count; i++) { Grid.SetRow((FrameworkElement)form.Children[i], i / columns); Grid.SetColumn((FrameworkElement)form.Children[i], i % columns); }
            }
            form.SizeChanged += (_, _) => Arrange(); Arrange(); content.Children.Add(form);
            stack.Children.Add(Card(content));
        }
        if (footer != null && stack.Children.LastOrDefault() is Border card && card.Child is StackPanel panel)
            panel.Children.Add(footer);
        return stack;
    }
    private FrameworkElement BuildField(JsonNode spec)
    {
        var title = J.S(spec, "title"); var section = J.S(spec, "section"); var key = J.S(spec, "key"); var kind = J.S(spec, "kind");
        Control input;
        if (kind == "bool") input = BoundToggle(title, section, key);
        else if (kind == "secret") input = BoundPassword(title, section, key);
        else if (kind == "choice")
        {
            var choices = J.A(spec["choices"]).Select(n => new ChoiceItem(J.S(n, "label"), J.S(n, "value"))).ToArray();
            var combo = new ComboBox { Header = title, ItemsSource = choices, DisplayMemberPath = "Label", SelectedItem = choices.FirstOrDefault(n => n.Value == Value(section, key)) };
            AutomationProperties.SetName(combo, title);
            combo.SelectionChanged += async (_, _) => { if (!refreshing && combo.SelectedItem is ChoiceItem choice) await Call("setValue", section, key, choice.Value); };
            refreshers.Add(() => combo.SelectedItem = choices.FirstOrDefault(n => n.Value == Value(section, key)));
            fields[section + "." + key] = combo; idleControls.Add(combo); input = combo;
        }
        else input = BoundText(title, section, key, kind == "multiline");
        // Keep raw numeric text, including incomplete edits, in the shared
        // validation path. Save/start report and focus the offending field.
        var panel = Column(input);
        panel.Spacing = 5;
        if (kind == "directory") panel.Children.Add(Button("选择目录…", async () =>
        {
            var picker = new Windows.Storage.Pickers.FolderPicker(); picker.FileTypeFilter.Add("*");
            WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
            var folder = await picker.PickSingleFolderAsync();
            if (folder != null) await Call("setValue", section, key, folder.Path);
        }, true));
        string fieldId = section + "." + key;
        if (kind is "integer" or "number") panel.Children.Add(Text($"范围：{J.S(spec, "low")}–{J.S(spec, "high")}", 11, true));
        if (J.S(spec, "hint") is { Length: > 0 } hint)
        {
            var help = new Button
            {
                Content = new FontIcon { Glyph = "\uE946", FontSize = 14 },
                VerticalAlignment = VerticalAlignment.Bottom,
                Flyout = new Flyout { Content = Text(hint) },
            };
            AutomationProperties.SetName(help, title + "说明");
            ToolTipService.SetToolTip(help, hint);
            panel.Children.RemoveAt(0);
            panel.Children.Insert(0, StretchRow(input, help));
        }
        if (spec["depends"] is JsonObject dependency && J.S(dependency, "key") != "")
        {
            var reason = Text(J.S(dependency, "reason"), 12, true);
            panel.Children.Add(reason);
            refreshers.Add(() =>
            {
                bool available = correctingFields.Contains(fieldId) || Value(J.S(dependency, "section"), J.S(dependency, "key"))
                    .Equals(J.S(dependency, "value"), StringComparison.OrdinalIgnoreCase);
                input.IsEnabled = !Busy && available;
                reason.Visibility = !available && reason.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            });
        }
        // Every field can be targeted by validation, including fields without
        // dependencies. Release correction on blur so later imports can refresh it.
        void FinishCorrection()
        {
            string value = input is TextBox text ? text.Text : Value(section, key);
            // A navigation blur can still see the old displayed value.
            if (value != Value(section, key)) return;
            bool valid = kind is "integer" or "number"
                ? double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out double number)
                    && double.IsFinite(number) && (kind != "integer" || number == Math.Truncate(number))
                    && number >= double.Parse(J.S(spec, "low"), System.Globalization.CultureInfo.InvariantCulture)
                    && number <= double.Parse(J.S(spec, "high"), System.Globalization.CultureInfo.InvariantCulture)
                : value.Length > 0;
            if (valid) correctingFields.Remove(fieldId);
        }
        finishCorrections[fieldId] = FinishCorrection;
        input.LostFocus += (_, _) => FinishCorrection();
        return panel;
    }

    private FrameworkElement BuildServices()
    {
        var bankStack = Column(StretchRow(Text("题库服务", 18), Button("离线检查配置", async () => await Call("offlineCheck"), true)));
        var bankMap = new Dictionary<string, string> { ["TikuGo"] = "bank_go", ["TikuYanxi"] = "bank_yanxi", ["TikuLike"] = "bank_like", ["TikuAdapter"] = "bank_adapter", ["TikuDeclarative"] = "bank_declarative" };
        var bankExpanders = new Dictionary<string, Expander>();
        foreach (var provider in J.A(backend.State["providers"]))
        {
            string key = J.S(provider, "key"), title = J.S(provider, "title"), secret = J.S(provider, "secret");
            var enabled = new ToggleSwitch { Header = title, IsOn = J.B(provider, "enabled") }; idleControls.Add(enabled);
            enabled.Toggled += async (_, _) => { if (!refreshing) await Call("toggleProvider", key, enabled.IsOn); };
            var health = Text(J.S(backend.State["serviceHealth"], key), 12, true);
            health.Name = key + "Health";
            refreshers.Add(() => { var row = J.A(backend.State["providers"]).FirstOrDefault(n => J.S(n, "key") == key); enabled.IsOn = J.B(row, "enabled"); health.Text = J.S(backend.State["serviceHealth"], key); });
            var up = Button("上移", async () => await Call("moveProvider", J.A(backend.State["providers"]).Select(n => J.S(n, "key")).ToList().IndexOf(key), -1), true);
            var down = Button("下移", async () => await Call("moveProvider", J.A(backend.State["providers"]).Select(n => J.S(n, "key")).ToList().IndexOf(key), 1), true);
            AutomationProperties.SetName(up, $"上移{title}");
            AutomationProperties.SetName(down, $"下移{title}");
            var content = Column(BoundPassword("服务凭据", "question_bank", secret), BuildGroups(bankMap[key], showHeading: false),
                Button("真实连接检查…", async () => { if (await Confirm("真实服务连接检查", "将向「" + title + "」发送一道最小测试题，可能消耗服务额度。", "发送测试题")) await Call("checkBank", key); }, true, "#7C3AED"));
            fieldGroups["question_bank." + secret] = bankMap[key];
            if (key == "TikuDeclarative") content.Children.Add(Row(Button("导入 JSON", () => FileAction("definitionAction", ["import"], false, "json"), true), Button("导出模板", () => FileAction("definitionAction", ["export"], true, "json", "question-bank"), true)));
            var header = Column(StretchRow(enabled, Row(up, down)), health);
            header.Margin = new Thickness(0, 14, 0, 14);
            header.Spacing = 14;
            var expander = new Expander
            {
                Header = header,
                Content = new Border { Padding = new Thickness(20, 16, 20, 20), Child = content },
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Stretch,
            };
            AutomationProperties.SetName(expander, title + "设置");
            bankExpanders.Add(key, expander); bankStack.Children.Add(expander);
        }
        refreshers.Add(() => { int position = 1; foreach (var p in J.A(backend.State["providers"])) { var expander = bankExpanders[J.S(p, "key")]; if (bankStack.Children.IndexOf(expander) != position) { bankStack.Children.Remove(expander); bankStack.Children.Insert(position, expander); } position++; } });
        var aiHealth = Text("尚未测试连接", 12, true);
        refreshers.Add(() => aiHealth.Text = J.S(backend.State["serviceHealth"], "ai") + "\n" + J.S(backend.State, "aiWarning"));
        var profileButton = Button("管理 AI 档案…", () => { EnqueueDialog(ShowProfiles); return Task.CompletedTask; }, true);
        var aiFooter = Column(Button("测试 AI 连接…", async () => { if (await Confirm("主动测试 AI 服务", "将向当前配置的 AI 服务发送“回复 OK”，可能消耗额度。", "发送测试请求")) await Call("checkAi"); }, true, "#7C3AED"), aiHealth);
        var ai = BuildGroups("ai", profileButton, aiFooter);
        var serviceGrid = new Grid { Name = "ServiceColumns", ColumnSpacing = 16, RowSpacing = 20, ChildrenTransitions = LayoutTransitions() };
        serviceGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }); serviceGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        serviceGrid.RowDefinitions.Add(new RowDefinition());
        serviceGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var banksScroll = Scroll(Card(bankStack)); var aiScroll = Scroll(ai);
        banksScroll.Name = "BankSettings"; aiScroll.Name = "AiSettings";
        serviceGrid.Children.Add(banksScroll); serviceGrid.Children.Add(aiScroll);
        var servicesScroll = Scroll(serviceGrid); servicesScroll.Name = "ServicesScroll";
        bool aiSelected = false;
        void ArrangeServices()
        {
            bool wide = servicesScroll.ActualWidth > 880;
            // A single scroll surface keeps AI reachable below the banks when
            // narrow; wide windows retain two independently scrolling columns.
            servicesScroll.VerticalScrollMode = wide ? ScrollMode.Disabled : ScrollMode.Enabled;
            servicesScroll.VerticalScrollBarVisibility = wide ? ScrollBarVisibility.Disabled : ScrollBarVisibility.Auto;
            serviceGrid.Height = wide ? servicesScroll.ActualHeight : double.NaN;
            serviceGrid.RowDefinitions[0].Height = wide ? new GridLength(1, GridUnitType.Star) : GridLength.Auto;
            foreach (var pane in new[] { banksScroll, aiScroll })
            {
                pane.VerticalScrollMode = wide ? ScrollMode.Enabled : ScrollMode.Disabled;
                pane.VerticalScrollBarVisibility = wide ? ScrollBarVisibility.Auto : ScrollBarVisibility.Disabled;
                Grid.SetColumnSpan(pane, wide ? 1 : 2);
            }
            Grid.SetColumn(aiScroll, wide ? 1 : 0);
            Grid.SetRow(aiScroll, wide ? 0 : 1);
        }
        servicesScroll.SizeChanged += (_, _) => ArrangeServices(); ArrangeServices();
        void RevealService(bool showAi)
        {
            aiSelected = showAi;
            DispatcherQueue.TryEnqueue(() =>
            {
                servicesScroll.UpdateLayout();
                if (servicesScroll.VerticalScrollMode == ScrollMode.Enabled)
                {
                    var target = showAi ? aiScroll : banksScroll;
                    double top = target.TransformToVisual(serviceGrid).TransformPoint(new Windows.Foundation.Point()).Y;
                    ScrollTo(servicesScroll, top);
                }
                else ScrollTo(showAi ? aiScroll : banksScroll, 0);
            });
        }
        var tabs = Tabs(("服务连接", servicesScroll), ("答案处理", Scroll(BuildGroups("answers"))), ("结束通知", Scroll(BuildGroups("notification"))), ("讨论任务", PreviewSettings("discussion")), ("投票／问卷", PreviewSettings("activity")));
        var selector = (Pivot)tabs.Children[0]; selector.Margin = new Thickness(-12, 0, 110, 8);
        Button? aiSwitch = null;
        aiSwitch = Button("AI 设置", () => { RevealService(!aiSelected); aiSwitch!.Content = aiSelected ? "题库列表" : "AI 设置"; return Task.CompletedTask; });
        aiSwitch.HorizontalAlignment = HorizontalAlignment.Right; aiSwitch.VerticalAlignment = VerticalAlignment.Center; tabs.Children.Add(aiSwitch);
        revealServiceGroup = group =>
        {
            selector.SelectedIndex = group switch { "answers" => 1, "notification" => 2, "discussion" => 3, "activity" => 4, _ => 0 };
            RevealService(group == "ai"); aiSwitch.Content = aiSelected ? "题库列表" : "AI 设置";
            foreach (var bank in bankExpanders) if (bankMap[bank.Key] == group) bank.Value.IsExpanded = true;
        };
        selector.SelectionChanged += (_, _) => aiSwitch.Visibility = selector.SelectedIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
        return tabs;
    }
    private UIElement PreviewSettings(string kind)
    {
        int limit = kind == "discussion" ? 12000 : 2000;
        var topic = new TextBox { Header = $"示例主题（必填，最多 {limit} 字符）", AcceptsReturn = true, Height = 100, TextWrapping = TextWrapping.Wrap };
        var output = new TextBox { Header = "预览结果", IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 120 };
        var preview = Button("生成预览…", async () =>
        {
            if (await Confirm("生成示例预览", "AI 策略会请求当前服务，可能消耗额度。固定回复和本地随机策略只在本机运行。", "生成预览"))
                await Call(kind == "discussion" ? "previewDiscussion" : "previewActivity", topic.Text);
        });
        void UpdatePreviewButton() => preview.IsEnabled = !Busy && !string.IsNullOrWhiteSpace(topic.Text);
        topic.TextChanged += (_, _) => UpdatePreviewButton();
        refreshers.Add(() =>
        {
            UpdatePreviewButton();
            output.Text = J.S(backend.State[kind + "Preview"], "content") + "\n" + J.S(backend.State[kind + "Preview"], "reason");
        });
        return Scroll(Column(BuildGroups(kind, showHeading: false), Card(Column(Text("示例预览", 18), topic, preview, output))));
    }
    private UIElement BuildBackup() => Card(Column(Text("配置与数据", 18),
        Row(Button("导入 INI 配置", () => FileAction("fileAction", ["import_config"], false, "ini"), true), Button("导出排障配置", () => FileAction("fileAction", ["export_diagnostics"], true, "ini", "diagnostics"), true)),
        Row(Button("导出完整 INI（含凭据）…", async () => { if (await Confirm("导出完整 INI", "文件包含密码与密钥，请仅作为个人备份保管。", "选择保存位置")) await FileAction("fileAction", ["export_config"], true, "ini", "config"); }, true),
            Button("数据与容量说明", async () => await ShowText("本地数据与备份", J.S(backend.State, "dataExplanation"))))));

    private Grid Tabs(params (string Title, UIElement Content)[] items)
    {
        var selector = new Pivot { Margin = new Thickness(-12, 0, 0, 8) };
        var host = new Grid(); var grid = new Grid(); grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(selector); grid.Children.Add(host); Grid.SetRow(host, 1);
        foreach (var item in items)
        {
            var tab = new PivotItem { Header = new TextBlock { Text = item.Title, FontSize = 14 } };
            AutomationProperties.SetName(tab, item.Title);
            selector.Items.Add(tab);
            host.Children.Add(item.Content);
        }
        var views = host.Children.Cast<FrameworkElement>().ToArray();
        ClipContent(host);
        int previous = 0;
        selector.SelectionChanged += (_, _) => { int selected = selector.SelectedIndex; SwitchContent(host, views, selected, selected >= previous ? 18 : -18); previous = selected; };
        selector.SelectedIndex = 0; SwitchContent(host, views, 0, animate: false);
        return grid;
    }
    private sealed record ChoiceItem(string Label, string Value);
    private sealed record AccountItem(string Id, string Display);
}

internal sealed class ToggleButtonCompat : Microsoft.UI.Xaml.Controls.Primitives.ToggleButton
{
    public ToggleButtonCompat(string title, Action<bool> action) { Content = title; Click += (_, _) => action(IsChecked == true); }
}
