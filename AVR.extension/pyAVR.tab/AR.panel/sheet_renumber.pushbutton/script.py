# -*- coding: utf-8 -*-
__title__   = "Перенумерація"
__doc__     = """Version = 1.0.0
Date = 09.07.2026
Розробник: Черкашина Катерина
Група: G-BIM
________________________________________________________________
Опис:
Інструмент для масової перенумерації аркушів із підтримкою префіксів/суфіксів, пошуку й заміни, зміщення номерів та повної перенумерації. Перед застосуванням відображає попередній перегляд результатів і автоматично перевіряє дублікати та некоректні номери."""

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
from Autodesk.Revit.DB import *

from pyrevit import forms, script

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

import re
import time

from System import TimeSpan
from System.Windows import Thickness, CornerRadius, Visibility, FontWeights
from System.Windows.Controls import TreeViewItem, Border, StackPanel, Orientation, TextBlock
from System.Windows.Media import SolidColorBrush, Color, Brushes
from System.Windows.Input import Keyboard, ModifierKeys, Key
from System.Windows.Threading import DispatcherTimer


# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
doc    = __revit__.ActiveUIDocument.Document #type:Document
uidoc  = __revit__.ActiveUIDocument
app    = __revit__.Application
output = script.get_output()

FORBIDDEN_SYMBOLS = "\\:{}[]|;<>?`~"

OPERATION_PREFIX_SUFFIX = u"prefix_suffix"
OPERATION_FIND_REPLACE  = u"find_replace"
OPERATION_OFFSET        = u"offset"
OPERATION_RENUMBER      = u"renumber"


# ╔═╗╔═╗╦═╗╔╦╗
# ╠╣ ║ ║╠╦╝║║║
# ╚  ╚═╝╩╚═╩ ╩
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
COLOR_TEXT           = Color.FromRgb(0x68, 0x68, 0x68)
COLOR_HEADER         = Color.FromRgb(0x4E, 0x4E, 0x4E)
COLOR_ACCENT         = Color.FromRgb(0xAC, 0xB7, 0xF5)
COLOR_HOVER_BG       = Color.FromRgb(0xEE, 0xF2, 0xFF)
COLOR_PREVIEW        = Color.FromRgb(0x01, 0x15, 0x8F)
COLOR_UNCHANGED_FG   = Color.FromRgb(0x9E, 0x9E, 0x9E)
COLOR_WARN_FG        = Color.FromRgb(0xC0, 0x2A, 0x2A)
COLOR_WARN_BG        = Color.FromRgb(0xFF, 0xEC, 0xEC)
COLOR_UNCHANGED_BG   = Color.FromRgb(0xF7, 0xF7, 0xF7)


def brush(color):
    return SolidColorBrush(color)


STATUS_FG = {
    "changed":   brush(COLOR_PREVIEW),
    "unchanged": brush(COLOR_UNCHANGED_FG),
    "duplicate": brush(COLOR_WARN_FG),
    "invalid":   brush(COLOR_WARN_FG),
}
STATUS_BG = {
    "changed":   Brushes.Transparent,
    "unchanged": brush(COLOR_UNCHANGED_BG),
    "duplicate": brush(COLOR_WARN_BG),
    "invalid":   brush(COLOR_WARN_BG),
}

DEFAULT_ICON = u"📁"


# ╦ ╦╔═╗╦  ╔═╗╔═╗╦═╗╔═╗
# ╠═╣║╣ ║  ╠═╝║╣ ╠╦╝╚═╗
# ╩ ╩╚═╝╩═╝╩  ╚═╝╩╚═╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
_NUM_RE = re.compile(r'^(.*?)(\d+)([^\d]*)$')     # ③ RegEx-розбір "текст + число + хвіст"
_SPLIT_RE = re.compile(r'(\d+)')                   # для natural sort


def clean_forbidden(text):
    for symbol in FORBIDDEN_SYMBOLS:
        if symbol in text:
            text = text.replace(symbol, '')
    return text


def split_alpha_numeric(s):
    m = _NUM_RE.match(s)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def natural_sort_key(s):
    """⑥ Natural Sort: AR1, AR2, AR10 замість AR1, AR10, AR2."""
    return [int(t) if t.isdigit() else t.lower() for t in _SPLIT_RE.split(s)]


def format_with_leading_zero(original_num_str, new_val):

    natural = str(new_val)
    if len(natural) == 1:
        return natural.zfill(2)
    return natural


def collect_sheets():
    collector = FilteredElementCollector(doc).OfClass(ViewSheet)
    return list(collector)


def get_sheet_folder_path(sheet):

    path = None
    try:
        bo = BrowserOrganization.GetCurrentBrowserOrganizationForSheets(doc)
        folder_items = bo.GetFolderItems(sheet.Id)
        sub_path = [fi.Name for fi in folder_items if fi.Name]
        if sub_path:
            path = sub_path
    except Exception:
        path = None

    if not path:
        parts = split_alpha_numeric(sheet.SheetNumber)
        prefix = parts[0].strip(u" -_") if parts else u""
        path = [prefix if prefix else u"Інше"]

    return path


class TreeNode(object):
    def __init__(self, name):
        self.name = name
        self.children = {}
        self.sheets = []

    def get_or_create_child(self, name):
        if name not in self.children:
            self.children[name] = TreeNode(name)
        return self.children[name]

    def total_count(self):
        c = len(self.sheets)
        for child in self.children.values():
            c += child.total_count()
        return c


# ╔═╗╔═╗╔╦╗╔═╗╦ ╦╔╦╗╔═╗
# ║  ║ ║║║║╠═╝║ ║ ║ ║╣
# ╚═╝╚═╝╩ ╩╩  ╚═╝ ╩ ╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
def compute_renumber_results(sheets, other_numbers, operation, params):

    raw = []  # (sheet, new_number_or_None, note_or_None)

    if operation == OPERATION_RENUMBER:
        # ⑤ Повна перенумерація — спершу Natural Sort обраних аркушів
        ordered = sorted(sheets, key=lambda s: natural_sort_key(s.SheetNumber))
        start_text = params.get('start', u'1') or u'1'
        try:
            start_val = int(start_text)
            width = len(start_text)
        except Exception:
            start_val = None
            width = 1
        try:
            increment = int(params.get('increment', u'1') or u'1')
        except Exception:
            increment = 1
        keep_prefix = params.get('keep_prefix', True)

        for i, sheet in enumerate(ordered):
            if start_val is None:
                raw.append((sheet, None, u"Некоректний стартовий номер"))
                continue
            val = start_val + i * increment
            if val < 0:
                raw.append((sheet, None, u"Від'ємний номер"))
                continue
            num_str = str(val).zfill(width)   # ④ збереження ведучих нулів
            if keep_prefix:
                parts = split_alpha_numeric(sheet.SheetNumber)
                prefix = parts[0] if parts else u""
                suffix = parts[2] if parts else u""
                new_number = prefix + num_str + suffix
            else:
                new_number = num_str
            raw.append((sheet, clean_forbidden(new_number), None))
    else:
        for sheet in sheets:
            old = sheet.SheetNumber
            note = None
            new_number = old

            if operation == OPERATION_PREFIX_SUFFIX:
                new_number = (params.get('prefix', u'') or u'') + old + (params.get('suffix', u'') or u'')

            elif operation == OPERATION_FIND_REPLACE:
                find = params.get('find', u'') or u''
                replace = params.get('replace', u'') or u''
                new_number = old.replace(find, replace) if find else old

            elif operation == OPERATION_OFFSET:
                parts = split_alpha_numeric(old)
                if parts is None:
                    raw.append((sheet, None, u"Не знайдено числову частину"))
                    continue
                prefix, num_str, suffix = parts
                try:
                    offset = int(params.get('offset', u'0') or u'0')
                except Exception:
                    raw.append((sheet, None, u"Некоректне зміщення"))
                    continue
                new_val = int(num_str) + offset
                if new_val < 0:
                    raw.append((sheet, None, u"Від'ємний номер"))
                    continue
                new_num_str = format_with_leading_zero(num_str, new_val)   # ④ ведучі нулі
                new_number = prefix + new_num_str + suffix

            raw.append((sheet, clean_forbidden(new_number), note))

    # ⑦⑧ Duplicate / Invalid detection — рахуємо ДО транзакції
    counts = {}
    for sheet, new_number, note in raw:
        if new_number is not None:
            counts[new_number] = counts.get(new_number, 0) + 1

    results = []
    for sheet, new_number, note in raw:
        if new_number is None:
            results.append({
                "sheet": sheet, "old_number": sheet.SheetNumber, "new_number": sheet.SheetNumber,
                "status": "invalid", "note": note or u"Некоректне значення",
            })
            continue

        if new_number.strip() == u"":
            results.append({
                "sheet": sheet, "old_number": sheet.SheetNumber, "new_number": sheet.SheetNumber,
                "status": "invalid", "note": u"Порожній номер",
            })
            continue

        is_dup = (counts.get(new_number, 0) > 1) or \
                 (new_number in other_numbers and new_number != sheet.SheetNumber)

        if is_dup:
            status = "duplicate"
            note = note or u"Номер уже використовується"
        elif new_number == sheet.SheetNumber:
            status = "unchanged"
        else:
            status = "changed"

        results.append({
            "sheet": sheet, "old_number": sheet.SheetNumber, "new_number": new_number,
            "status": status, "note": note,
        })
    return results


class PreviewItem(object):
    def __init__(self, old_number, new_number, sheet_name, status):
        self.OldNumber = old_number
        self.NewNumber = new_number
        self.SheetName = sheet_name
        self.RowBackground = STATUS_BG.get(status, Brushes.Transparent)
        self.NewNumberColor = STATUS_FG.get(status, brush(COLOR_PREVIEW))


# ╔═╗╔╦╗╦╦  ╦
# ╚═╗ ║ ║║  ║
# ╚═╝ ╩ ╩╩═╝╩═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
STYLES_XAML = u"""
    <Window.Resources>
        <Style x:Key="AccentButton" TargetType="Button">
            <Setter Property="Background" Value="#ACB7F5"/>
            <Setter Property="Foreground" Value="White"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Padding" Value="18,9"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="bd" Background="{TemplateBinding Background}" CornerRadius="8">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"
                                               Margin="{TemplateBinding Padding}"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="bd" Property="Background" Value="#9CAAF3"/>
                            </Trigger>
                            <Trigger Property="IsPressed" Value="True">
                                <Setter TargetName="bd" Property="Background" Value="#8796F0"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter TargetName="bd" Property="Background" Value="#D6D6D6"/>
                                <Setter Property="Foreground" Value="#9E9E9E"/>
                                <Setter Property="Cursor" Value="Arrow"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="SecondaryButton" TargetType="Button">
            <Setter Property="Background" Value="White"/>
            <Setter Property="Foreground" Value="#686868"/>
            <Setter Property="BorderBrush" Value="#D7D7D7"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="18,9"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="bd" Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="8">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"
                                               Margin="{TemplateBinding Padding}"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="bd" Property="Background" Value="#F5F5F5"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="LinkButton" TargetType="Button">
            <Setter Property="Background" Value="Transparent"/>
            <Setter Property="Foreground" Value="#ACB7F5"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="FontSize" Value="12"/>
            <Setter Property="Padding" Value="6,4"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"
                                           Margin="{TemplateBinding Padding}"/>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="ModernTextBox" TargetType="TextBox">
            <Setter Property="Foreground" Value="#686868"/>
            <Setter Property="Padding" Value="8,6"/>
            <Setter Property="BorderBrush" Value="#D7D7D7"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="TextBox">
                        <Border Background="White" BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="6">
                            <ScrollViewer x:Name="PART_ContentHost" Margin="{TemplateBinding Padding}"
                                          VerticalAlignment="Center"/>
                        </Border>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </Window.Resources>
"""


# ╔═╗╔═╗╦═╗╔╦╗  ╦  ╦╦╔╗ ╦═╗╔═╗╦═╗╦╔═╗╔═╗
# ╠╣ ║ ║╠╦╝║║║  ║  ║║╠╩╗╠╦╝╠═╣╠╦╝║║╣ ╚═╗
# ╚  ╚═╝╩╚═╩ ╩  ╩═╝╩╩╚═╝╩╚═╩ ╩╩╚═╩╚═╝╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
XAML_SELECT = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Вибір аркушів" Height="660" Width="520"
        WindowStartupLocation="CenterScreen" Background="#FFFFFF"
        KeyDown="window_key_down">
""" + STYLES_XAML + u"""
    <Grid Margin="16">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <TextBlock Grid.Row="0" Text="Перенумерація" FontSize="18" FontWeight="Bold"
                   Foreground="#4E4E4E" Margin="0,0,0,2"/>
        <TextBlock Grid.Row="1" Name="tb_counter" Text="Вибрано: 0 аркуш(ів)"
                   Foreground="#686868" Margin="0,0,0,10"/>

        <Border Grid.Row="2" BorderBrush="#D7D7D7" BorderThickness="1" CornerRadius="6" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="Auto"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="🔍" Margin="8,0,0,0" VerticalAlignment="Center" FontSize="14"/>
                <TextBox Grid.Column="1" Name="tb_search" BorderThickness="0" Padding="8,7"
                         Foreground="#686868" TextChanged="on_search_changed"/>
            </Grid>
        </Border>

        <TreeView Name="tree_sheets" Grid.Row="3" BorderThickness="0" Background="White"
                  VirtualizingPanel.IsVirtualizing="True"
                  VirtualizingPanel.VirtualizationMode="Recycling">
            <TreeView.ItemsPanel>
                <ItemsPanelTemplate>
                    <VirtualizingStackPanel/>
                </ItemsPanelTemplate>
            </TreeView.ItemsPanel>
        </TreeView>

        <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
            <Button Name="btn_select_all" Content="Обрати все (Ctrl+A)" Style="{StaticResource LinkButton}"
                    Margin="0,0,12,0" Click="select_all_click"/>
            <Button Name="btn_clear_all" Content="Зняти все (Ctrl+D)" Style="{StaticResource LinkButton}"
                    Margin="0,0,12,0" Click="clear_all_click"/>
            <Button Name="btn_expand_all" Content="Розгорнути все" Style="{StaticResource LinkButton}"
                    Margin="0,0,12,0" Click="expand_all_click"/>
            <Button Name="btn_collapse_all" Content="Згорнути все" Style="{StaticResource LinkButton}"
                    Click="collapse_all_click"/>
        </StackPanel>

        <StackPanel Grid.Row="5" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
            <Button Name="btn_next" Content="Далі →" Style="{StaticResource AccentButton}"
                    Margin="0,0,8,0" Click="next_click" IsDefault="True"/>
            <Button Name="btn_cancel" Content="Скасувати" Style="{StaticResource SecondaryButton}"
                    Click="cancel_click" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


class SheetSelectWindow(forms.WPFWindow):
    def __init__(self, xaml_source, sheets):
        forms.WPFWindow.__init__(self, xaml_source, literal_string=True)
        self.all_leaf = []
        self.leaf_index = {}
        self.selected = set()
        self.last_anchor = None
        self.selected_sheets = []

        self._search_timer = DispatcherTimer()
        self._search_timer.Interval = TimeSpan.FromMilliseconds(200)
        self._search_timer.Tick += self._on_search_timer_tick

        root = self._build_data_tree(sheets)
        self._render_tree(root, self.tree_sheets.Items)
        self._update_counter()

    def _build_data_tree(self, sheets):
        root = TreeNode(u"root")
        for s in sheets:
            path = get_sheet_folder_path(s)
            node = root
            for part in path:
                node = node.get_or_create_child(part)
            node.sheets.append(s)
        return root

    def _render_tree(self, node, items_collection):
        for name in sorted(node.children.keys()):
            child = node.children[name]
            tvi = self._make_group_item(name, child.total_count())
            items_collection.Add(tvi)
            self._render_tree(child, tvi.Items)
        for s in sorted(node.sheets, key=lambda x: natural_sort_key(x.SheetNumber)):
            items_collection.Add(self._make_leaf_item(s))

    def _make_group_item(self, name, count):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal

        icon_tb = TextBlock()
        icon_tb.Text = DEFAULT_ICON + u"  "
        icon_tb.FontSize = 14

        name_tb = TextBlock()
        name_tb.Text = u"{} ({})".format(name, count)
        name_tb.Foreground = brush(COLOR_HEADER)
        name_tb.FontWeight = FontWeights.SemiBold

        panel.Children.Add(icon_tb)
        panel.Children.Add(name_tb)

        tvi = TreeViewItem()
        tvi.Header = panel
        tvi.IsExpanded = False
        return tvi

    def _make_leaf_item(self, sheet):
        border = Border()
        border.Padding = Thickness(8, 4, 8, 4)
        border.CornerRadius = CornerRadius(4)
        border.Background = Brushes.Transparent
        border.Tag = sheet

        tb = TextBlock()
        tb.Text = u"{} - {}".format(sheet.SheetNumber, sheet.Name)
        tb.Foreground = brush(COLOR_TEXT)
        border.Child = tb

        border.PreviewMouseLeftButtonDown += self.on_leaf_click
        border.MouseEnter += self.on_leaf_enter
        border.MouseLeave += self.on_leaf_leave

        tvi = TreeViewItem()
        tvi.Header = border
        tvi.Focusable = False

        self.leaf_index[border] = len(self.all_leaf)
        self.all_leaf.append(border)
        return tvi

    def on_leaf_enter(self, sender, args):
        if sender not in self.selected:
            sender.Background = brush(COLOR_HOVER_BG)

    def on_leaf_leave(self, sender, args):
        if sender not in self.selected:
            sender.Background = Brushes.Transparent

    def on_leaf_click(self, sender, args):
        mods = Keyboard.Modifiers
        ctrl = (mods & ModifierKeys.Control) == ModifierKeys.Control
        shift = (mods & ModifierKeys.Shift) == ModifierKeys.Shift

        if shift and self.last_anchor in self.leaf_index:
            i1 = self.leaf_index[self.last_anchor]
            i2 = self.leaf_index[sender]
            lo, hi = min(i1, i2), max(i1, i2)
            self._clear_selection()
            for b in self.all_leaf[lo:hi + 1]:
                self._select_border(b)
        elif ctrl:
            if sender in self.selected:
                self._deselect_border(sender)
            else:
                self._select_border(sender)
            self.last_anchor = sender
        else:
            self._clear_selection()
            self._select_border(sender)
            self.last_anchor = sender

        self._update_counter()
        args.Handled = True

    def _select_border(self, border):
        border.Background = brush(COLOR_ACCENT)
        border.Child.Foreground = Brushes.White
        self.selected.add(border)

    def _deselect_border(self, border):
        border.Background = Brushes.Transparent
        border.Child.Foreground = brush(COLOR_TEXT)
        self.selected.discard(border)

    def _clear_selection(self):
        for b in list(self.selected):
            self._deselect_border(b)

    def _update_counter(self):
        self.tb_counter.Text = u"Вибрано: {} аркуш(ів)".format(len(self.selected))

    def select_all_click(self, sender, args):
        for b in self.all_leaf:
            if b.Visibility == Visibility.Visible:
                self._select_border(b)
        self._update_counter()

    def clear_all_click(self, sender, args):
        self._clear_selection()
        self._update_counter()

    def expand_all_click(self, sender, args):
        self._set_expanded(self.tree_sheets.Items, True)

    def collapse_all_click(self, sender, args):
        self._set_expanded(self.tree_sheets.Items, False)

    def _set_expanded(self, items, expanded):
        for item in items:
            item.IsExpanded = expanded
            self._set_expanded(item.Items, expanded)

    def window_key_down(self, sender, args):
        ctrl = (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control
        if ctrl and args.Key == Key.A:
            for b in self.all_leaf:
                if b.Visibility == Visibility.Visible:
                    self._select_border(b)
            self._update_counter()
            args.Handled = True
        elif ctrl and args.Key == Key.D:
            self._clear_selection()
            self._update_counter()
            args.Handled = True

    def on_search_changed(self, sender, args):
        self._search_timer.Stop()
        self._search_timer.Start()

    def _on_search_timer_tick(self, sender, args):
        self._search_timer.Stop()
        query = (self.tb_search.Text or u"").strip().lower()
        self._filter_tree(self.tree_sheets.Items, query)

    def _filter_tree(self, items, query):
        any_visible = False
        for item in items:
            header = item.Header
            if isinstance(header, Border):
                sheet = header.Tag
                haystack = u"{} {}".format(sheet.SheetNumber, sheet.Name).lower()
                visible = (query == u"") or (query in haystack)
                item.Visibility = Visibility.Visible if visible else Visibility.Collapsed
                any_visible = any_visible or visible
            else:
                child_visible = self._filter_tree(item.Items, query)
                item.Visibility = Visibility.Visible if child_visible else Visibility.Collapsed
                if query and child_visible:
                    item.IsExpanded = True
                elif not query:
                    item.IsExpanded = False
                any_visible = any_visible or child_visible
        return any_visible

    def next_click(self, sender, args):
        self.selected_sheets = [b.Tag for b in self.selected]
        self.Close()

    def cancel_click(self, sender, args):
        self.selected_sheets = []
        self.Close()


# ╔═╗╔═╗╦═╗╔╦╗  ╦═╗╔═╗╔╗╔╦ ╦╔╦╗
# ╠╣ ║ ║╠╦╝║║║  ╠╦╝║╣ ║║║║ ║║║║
# ╚  ╚═╝╩╚═╩ ╩  ╩╚═╚═╝╝╚╝╚═╝╩ ╩
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
XAML_RENUMBER = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Перенумерація" Height="760" Width="700"
        WindowStartupLocation="CenterScreen" Background="#FFFFFF">
""" + STYLES_XAML + u"""
    <Grid Margin="18">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,12">
            <TextBlock Text="Перенумерація" FontSize="18" FontWeight="Bold" Foreground="#4E4E4E"/>
            <TextBlock Name="tb_counter2" Text="Вибрано: 0 аркуш(ів)" Foreground="#686868" Margin="0,2,0,0"/>
        </StackPanel>

        <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,12">
            <RadioButton Name="rb_prefix_suffix" GroupName="op" Content="➕ Префікс / Суфікс" IsChecked="True"
                         Tag="prefix_suffix" Checked="on_operation_changed" Margin="0,0,18,0" Foreground="#4E4E4E"/>
            <RadioButton Name="rb_find_replace" GroupName="op" Content="🔄 Знайти / Замінити"
                         Tag="find_replace" Checked="on_operation_changed" Margin="0,0,18,0" Foreground="#4E4E4E"/>
            <RadioButton Name="rb_offset" GroupName="op" Content="± Зміщення номера"
                         Tag="offset" Checked="on_operation_changed" Margin="0,0,18,0" Foreground="#4E4E4E"/>
            <RadioButton Name="rb_renumber" GroupName="op" Content="🔢 Повна перенумерація"
                         Tag="renumber" Checked="on_operation_changed" Foreground="#4E4E4E"/>
        </StackPanel>

        <Border Grid.Row="2" BorderBrush="#D7D7D7" BorderThickness="1" CornerRadius="10" Padding="14" Margin="0,0,0,10">
            <Grid>
                <!-- ① Префікс / Суфікс -->
                <StackPanel Name="panel_prefix_suffix" Orientation="Horizontal" Visibility="Visible">
                    <StackPanel Width="260" Margin="0,0,20,0">
                        <TextBlock Text="Префікс" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_ps_prefix" Style="{StaticResource ModernTextBox}" TextChanged="on_param_changed"/>
                    </StackPanel>
                    <StackPanel Width="260">
                        <TextBlock Text="Суфікс" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_ps_suffix" Style="{StaticResource ModernTextBox}" TextChanged="on_param_changed"/>
                    </StackPanel>
                </StackPanel>

                <!-- ② Знайти / Замінити -->
                <StackPanel Name="panel_find_replace" Orientation="Horizontal" Visibility="Collapsed">
                    <StackPanel Width="260" Margin="0,0,20,0">
                        <TextBlock Text="Знайти" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_fr_find" Style="{StaticResource ModernTextBox}" TextChanged="on_param_changed"/>
                    </StackPanel>
                    <StackPanel Width="260">
                        <TextBlock Text="Замінити на" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_fr_replace" Style="{StaticResource ModernTextBox}" TextChanged="on_param_changed"/>
                    </StackPanel>
                </StackPanel>

                <!-- ③ Зміщення номера -->
                <StackPanel Name="panel_offset" Orientation="Horizontal" Visibility="Collapsed">
                    <StackPanel Width="200">
                        <TextBlock Text="Зміщення (напр. +1 або -20)" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_offset" Style="{StaticResource ModernTextBox}" Text="1" TextChanged="on_param_changed"/>
                    </StackPanel>
                    <TextBlock Text="Змінюється лише числова частина: AR101 → AR102, П09 → П10, КЖ-005 → КЖ-006. Ведучі нулі зберігаються."
                               Foreground="#9E9E9E" FontSize="11" TextWrapping="Wrap" Width="380" Margin="20,18,0,0"/>
                </StackPanel>

                <!-- ④ Повна перенумерація -->
                <StackPanel Name="panel_renumber" Orientation="Horizontal" Visibility="Collapsed">
                    <StackPanel Width="140" Margin="0,0,20,0">
                        <TextBlock Text="Старт" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_start" Style="{StaticResource ModernTextBox}" Text="001" TextChanged="on_param_changed"/>
                    </StackPanel>
                    <StackPanel Width="140" Margin="0,0,20,0">
                        <TextBlock Text="Крок" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                        <TextBox Name="tb_increment" Style="{StaticResource ModernTextBox}" Text="1" TextChanged="on_param_changed"/>
                    </StackPanel>
                    <CheckBox Name="cb_keep_prefix" Content="Зберігати префікс" IsChecked="True"
                              Foreground="#686868" VerticalAlignment="Bottom" Margin="0,0,0,10"
                              Checked="on_param_changed" Unchecked="on_param_changed"/>
                </StackPanel>
            </Grid>
        </Border>

        <Border Grid.Row="3" BorderBrush="#D7D7D7" BorderThickness="1" CornerRadius="10">
            <ListBox Name="lv_preview" BorderThickness="0" Background="Transparent"
                     VirtualizingPanel.IsVirtualizing="True" VirtualizingPanel.VirtualizationMode="Recycling">
                <ListBox.ItemContainerStyle>
                    <Style TargetType="ListBoxItem">
                        <Setter Property="Background" Value="{Binding RowBackground}"/>
                        <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                        <Setter Property="Padding" Value="12,8"/>
                        <Setter Property="Template">
                            <Setter.Value>
                                <ControlTemplate TargetType="ListBoxItem">
                                    <Border Background="{TemplateBinding Background}"
                                            BorderBrush="#F0F0F0" BorderThickness="0,0,0,1"
                                            Padding="{TemplateBinding Padding}">
                                        <ContentPresenter/>
                                    </Border>
                                </ControlTemplate>
                            </Setter.Value>
                        </Setter>
                    </Style>
                </ListBox.ItemContainerStyle>
                <ListBox.ItemTemplate>
                    <DataTemplate>
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="120"/>
                                <ColumnDefinition Width="30"/>
                                <ColumnDefinition Width="140"/>
                                <ColumnDefinition Width="*"/>
                            </Grid.ColumnDefinitions>
                            <TextBlock Grid.Column="0" Text="{Binding OldNumber}" Foreground="#686868"
                                       VerticalAlignment="Center"/>
                            <TextBlock Grid.Column="1" Text="➜" Foreground="#ACB7F5" FontWeight="Bold"
                                       HorizontalAlignment="Center" VerticalAlignment="Center"/>
                            <TextBlock Grid.Column="2" Text="{Binding NewNumber}" Foreground="{Binding NewNumberColor}"
                                       FontWeight="Bold" VerticalAlignment="Center"/>
                            <TextBlock Grid.Column="3" Text="{Binding SheetName}" Foreground="#9E9E9E"
                                       TextTrimming="CharacterEllipsis" VerticalAlignment="Center" Margin="10,0,0,0"/>
                        </Grid>
                    </DataTemplate>
                </ListBox.ItemTemplate>
            </ListBox>
        </Border>

        <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
            <Button Name="btn_cancel" Content="Скасувати" Style="{StaticResource SecondaryButton}"
                    Margin="0,0,10,0" Click="cancel_click" IsCancel="True"/>
            <Button Name="btn_apply" Content="Застосувати" Style="{StaticResource AccentButton}"
                    Click="apply_click" IsDefault="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


class RenumberWindow(forms.WPFWindow):
    def __init__(self, xaml_source, sheets):
        forms.WPFWindow.__init__(self, xaml_source, literal_string=True)
        self.sheets = sheets
        self.operation = OPERATION_PREFIX_SUFFIX
        self.confirmed = False
        self.result_rows = []
        self._results_cache = []

        selected_ids = set(s.Id.IntegerValue for s in sheets)
        self.other_numbers = set(
            s.SheetNumber for s in collect_sheets() if s.Id.IntegerValue not in selected_ids
        )

        self.tb_counter2.Text = u"Вибрано: {} аркуш(ів)".format(len(sheets))
        self.update_preview()

    def _panels(self):
        return {
            OPERATION_PREFIX_SUFFIX: self.panel_prefix_suffix,
            OPERATION_FIND_REPLACE:  self.panel_find_replace,
            OPERATION_OFFSET:        self.panel_offset,
            OPERATION_RENUMBER:      self.panel_renumber,
        }

    def on_operation_changed(self, sender, args):
        op = str(sender.Tag)
        self.operation = op
        for key, panel in self._panels().items():
            panel.Visibility = Visibility.Visible if key == op else Visibility.Collapsed
        self.update_preview()

    def on_param_changed(self, sender, args):
        self.update_preview()

    def _gather_params(self):
        return {
            'prefix':      self.tb_ps_prefix.Text,
            'suffix':      self.tb_ps_suffix.Text,
            'find':        self.tb_fr_find.Text,
            'replace':     self.tb_fr_replace.Text,
            'offset':      self.tb_offset.Text,
            'start':       self.tb_start.Text,
            'increment':   self.tb_increment.Text,
            'keep_prefix': bool(self.cb_keep_prefix.IsChecked),
        }

    def update_preview(self):
        params = self._gather_params()
        results = compute_renumber_results(self.sheets, self.other_numbers, self.operation, params)
        self._results_cache = results

        items = []
        will_rename = 0
        unchanged = 0
        duplicates = 0
        invalid = 0

        for r in results:
            status = r["status"]
            note = r.get("note")
            display = r["new_number"]
            if status in ("duplicate", "invalid"):
                display = u"⚠ " + display + (u"  ({})".format(note) if note else u"")

            if status == "changed":
                will_rename += 1
            elif status == "unchanged":
                unchanged += 1
            elif status == "duplicate":
                duplicates += 1
            elif status == "invalid":
                invalid += 1

            items.append(PreviewItem(r["old_number"], display, r["sheet"].Name, status))

        self.lv_preview.ItemsSource = items
        self.tb_counter2.Text = (
            u"Вибрано: {}   Буде змінено: {}   Без змін: {}   Дублікати: {}   Некоректні: {}"
            .format(len(self.sheets), will_rename, unchanged, duplicates, invalid)
        )

        # ⑩ Apply активний, лише якщо немає дублікатів і некоректних номерів
        has_blocking = (duplicates > 0) or (invalid > 0)
        self.btn_apply.IsEnabled = not has_blocking

    def apply_click(self, sender, args):
        self.update_preview()
        has_blocking = any(r["status"] in ("duplicate", "invalid") for r in self._results_cache)
        if has_blocking:
            forms.alert(u"Спершу усуньте дублікати та некоректні номери (позначені червоним).")
            return
        self.result_rows = self._results_cache
        self.confirmed = True
        self.Close()

    def cancel_click(self, sender, args):
        self.confirmed = False
        self.Close()


# ╔╦╗╔═╗╦╔╗╔
# ║║║╠═╣║║║║
# ╩ ╩╩ ╩╩╝╚╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░

#1️⃣ Вибір аркушів
all_sheets = collect_sheets()
if not all_sheets:
    forms.alert(u'У проєкті немає аркушів.', exitscript=True)

select_win = SheetSelectWindow(XAML_SELECT, all_sheets)
select_win.ShowDialog()

selected_sheets = select_win.selected_sheets
if not selected_sheets:
    forms.alert(u'Аркуші не обрано. Спробуйте ще раз.', exitscript=True)

#2️⃣ Вибір режиму та живе прев'ю
renumber_win = RenumberWindow(XAML_RENUMBER, selected_sheets)
renumber_win.ShowDialog()

if not renumber_win.confirmed:
    script.exit()

#3️⃣ Застосування — у 2 фази, щоб уникнути колізій номерів під час перенумерації
t = Transaction(doc, "Sheet Number Manager")
t.Start()

start_time = time.time()
to_change = [r for r in renumber_win.result_rows if r["status"] == "changed"]

# Фаза 1: тимчасові унікальні номери (на основі ElementId — гарантовано унікальні)
for r in to_change:
    r["sheet"].SheetNumber = u"__TMP__{}".format(r["sheet"].Id.IntegerValue)

renamed_count = 0
error_rows = []
for r in to_change:
    try:
        r["sheet"].SheetNumber = r["new_number"]
        renamed_count += 1
    except Exception as e:
        error_rows.append(u"{}: {}".format(r["old_number"], str(e)))

t.Commit()
elapsed = time.time() - start_time

#4️⃣ Підсумковий звіт
unchanged_count = sum(1 for r in renumber_win.result_rows if r["status"] == "unchanged")
report_lines = [u"Успіх. Перенумеровано {} аркуш(ів).".format(renamed_count)]
if unchanged_count:
    report_lines.append(u"Без змін: {}".format(unchanged_count))
if error_rows:
    report_lines.append(u"Помилок: {}".format(len(error_rows)))

forms.alert(u"\n".join(report_lines))
#███████████████████████████████████████████████████████████████████████████