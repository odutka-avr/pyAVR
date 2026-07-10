# -*- coding: utf-8 -*-
__title__   = "Редактор \nназв"
__doc__     = """Version = 1.0.0
Date = 09.07.2026
Розробник: Черкашина Катерина
Група: G-BIM
________________________________________________________________
Опис:
Інструмент для масового перейменування видів і аркушів із підтримкою префікса, суфікса та пошуку/заміни тексту. Перед застосуванням змін відображає попередній перегляд результату, автоматично усуває дублікати та перевіряє коректність назв."""

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
from Autodesk.Revit.DB import *

#pyRevit
from pyrevit import forms, script

#.NET Imports
import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

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
uidoc  = __revit__.ActiveUIDocument          # __revit__ is internal variable in pyRevit
app    = __revit__.Application
output = script.get_output()                 # pyRevit Output Menu

FORBIDDEN_SYMBOLS = "\\:{}[]|;<>?`~"
MAX_NAME_LENGTH   = 150


# ╔═╗╔═╗╦═╗╔╦╗
# ╠╣ ║ ║╠╦╝║║║
# ╚  ╚═╝╩╚═╩ ╩
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
COLOR_TEXT           = Color.FromRgb(0x68, 0x68, 0x68)
COLOR_HEADER         = Color.FromRgb(0x4E, 0x4E, 0x4E)
COLOR_ACCENT         = Color.FromRgb(0xAC, 0xB7, 0xF5)
COLOR_ACCENT_HOVER   = Color.FromRgb(0x9C, 0xAA, 0xF3)
COLOR_ACCENT_PRESSED = Color.FromRgb(0x87, 0x96, 0xF0)
COLOR_HOVER_BG       = Color.FromRgb(0xEE, 0xF2, 0xFF)
COLOR_PREVIEW        = Color.FromRgb(0x01, 0x15, 0x8F)
COLOR_BORDER         = Color.FromRgb(0xD7, 0xD7, 0xD7)
COLOR_UNCHANGED_FG   = Color.FromRgb(0x9E, 0x9E, 0x9E)
COLOR_WARN_FG        = Color.FromRgb(0xC0, 0x2A, 0x2A)
COLOR_WARN_BG        = Color.FromRgb(0xFF, 0xEC, 0xEC)
COLOR_UNCHANGED_BG   = Color.FromRgb(0xF7, 0xF7, 0xF7)


def brush(color):
    return SolidColorBrush(color)


# 3.2 / 3.3 — колір рядків/тексту залежно від статусу
STATUS_FG = {
    "changed":   brush(COLOR_PREVIEW),
    "unchanged": brush(COLOR_UNCHANGED_FG),
    "duplicate": brush(COLOR_WARN_FG),
    "empty":     brush(COLOR_WARN_FG),
}
STATUS_BG = {
    "changed":   Brushes.Transparent,
    "unchanged": brush(COLOR_UNCHANGED_BG),
    "duplicate": brush(COLOR_WARN_BG),
    "empty":     brush(COLOR_WARN_BG),
}

ICONS = {
    u"Види":                 u"🗂",
    u"Views":                u"🗂",
    u"Аркуші":               u"📄",
    u"Sheets":               u"📄",
    u"Floor Plans":          u"📐",
    u"Поверхові плани":      u"📐",
    u"Ceiling Plans":        u"🏚",
    u"Плани стель":          u"🏚",
    u"Sections":             u"📏",
    u"Розрізи":              u"📏",
    u"Elevations":           u"🏠",
    u"Фасади":               u"🏠",
    u"3D Views":             u"🧊",
    u"3D види":              u"🧊",
    u"Legends":              u"🗒",
    u"Легенди":              u"🗒",
    u"Schedules":            u"📊",
    u"Schedules/Quantities": u"📊",
    u"Специфікації":         u"📊",
    u"Drafting Views":       u"✏️",
    u"Креслярські види":     u"✏️",
    u"Detail Views":         u"🔍",
    u"Деталі":               u"🔍",
}
DEFAULT_ICON = u"📁"

# 1.1 — резервне групування, якщо BrowserOrganization недоступна для елемента
FALLBACK_GROUPS = {
    ViewType.FloorPlan:        u"Поверхові плани",
    ViewType.CeilingPlan:      u"Плани стель",
    ViewType.Elevation:        u"Фасади",
    ViewType.Section:          u"Розрізи",
    ViewType.ThreeD:           u"3D види",
    ViewType.Detail:           u"Деталі",
    ViewType.DraftingView:     u"Креслярські види",
    ViewType.Legend:           u"Легенди",
    ViewType.EngineeringPlan:  u"Інженерні плани",
    ViewType.AreaPlan:         u"Плани площ",
    ViewType.Schedule:         u"Специфікації",
    ViewType.DrawingSheet:     u"Аркуші",
    ViewType.Walkthrough:      u"Обхід",
    ViewType.Rendering:        u"Рендери",
}


def collect_views():
    collector = FilteredElementCollector(doc).OfClass(View)
    return [v for v in collector if not v.IsTemplate]


def clean_forbidden(text):
    for symbol in FORBIDDEN_SYMBOLS:
        if symbol in text:
            text = text.replace(symbol, '')
    return text


def build_new_name_verbose(old_name, prefix, find, replace, suffix):
    new_name = old_name
    if find:
        new_name = new_name.replace(find, replace)
    new_name = prefix + new_name + suffix
    cleaned = clean_forbidden(new_name)
    had_forbidden = (cleaned != new_name)
    return cleaned, had_forbidden


def build_new_name(old_name, prefix, find, replace, suffix):
    cleaned, _ = build_new_name_verbose(old_name, prefix, find, replace, suffix)
    return cleaned


def resolve_unique_name(name, used_names):
    if name not in used_names:
        return name
    i = 1
    candidate = u"{} ({})".format(name, i)
    while candidate in used_names:
        i += 1
        candidate = u"{} ({})".format(name, i)
    return candidate


def compute_final_names(views, other_view_names, prefix, find, replace, suffix):

    raw_results = []
    for v in views:
        cleaned, had_forbidden = build_new_name_verbose(v.Name, prefix, find, replace, suffix)
        raw_results.append((v, cleaned, had_forbidden))

    # Дублікати рахуємо лише серед звичайних видів — аркуші сюди не входять
    raw_counts = {}
    for v, cleaned, _ in raw_results:
        if isinstance(v, ViewSheet):
            continue
        raw_counts[cleaned] = raw_counts.get(cleaned, 0) + 1

    used = set(other_view_names)  # імена інших ЗВИЧАЙНИХ видів поза виділенням (без аркушів)
    output_list = []
    for v, cleaned, had_forbidden in raw_results:
        stripped = cleaned.strip()
        is_sheet = isinstance(v, ViewSheet)

        # 1.2 — порожнє ім'я (актуально і для аркушів, і для видів)
        if stripped == u"":
            output_list.append({
                "view": v, "final_name": v.Name, "status": "empty",
                "had_forbidden": had_forbidden, "too_long": False,
            })
            continue

        if is_sheet:
            # Аркуші: унікальність не потрібна — Revit дозволяє однакові назви
            final_name = cleaned
            too_long = len(final_name) > MAX_NAME_LENGTH
            status = "unchanged" if final_name == v.Name else "changed"
        else:
            was_dup_in_batch = raw_counts.get(cleaned, 0) > 1
            final_name = resolve_unique_name(cleaned, used)
            used.add(final_name)
            too_long = len(final_name) > MAX_NAME_LENGTH

            if final_name == v.Name:
                status = "unchanged"
            elif was_dup_in_batch or final_name != cleaned:
                status = "duplicate"
            else:
                status = "changed"

        output_list.append({
            "view": v, "final_name": final_name, "status": status,
            "had_forbidden": had_forbidden, "too_long": too_long,
        })
    return output_list


class PreviewItem(object):
    def __init__(self, old_name, new_name, status):
        self.OldName = old_name
        self.NewName = new_name
        self.RowBackground = STATUS_BG.get(status, Brushes.Transparent)
        self.NewNameColor = STATUS_FG.get(status, brush(COLOR_PREVIEW))


class TreeNode(object):
    """Проміжна модель дерева: папки (як у Project Browser) + листя-види."""
    def __init__(self, name):
        self.name = name
        self.children = {}   # name -> TreeNode
        self.views = []

    def get_or_create_child(self, name):
        if name not in self.children:
            self.children[name] = TreeNode(name)
        return self.children[name]

    def total_count(self):
        c = len(self.views)
        for child in self.children.values():
            c += child.total_count()
        return c


def get_browser_path(view):

    is_sheet = isinstance(view, ViewSheet)
    top = u"Аркуші" if is_sheet else u"Види"

    path = None
    try:
        if is_sheet:
            bo = BrowserOrganization.GetCurrentBrowserOrganizationForSheets(doc)
        else:
            bo = BrowserOrganization.GetCurrentBrowserOrganizationForViews(doc)
        folder_items = bo.GetFolderItems(view.Id)
        sub_path = [fi.Name for fi in folder_items if fi.Name]
        if sub_path:
            path = [top] + sub_path
    except Exception:
        path = None

    if not path:
        group = FALLBACK_GROUPS.get(getattr(view, "ViewType", None), u"Інше")
        path = [top, group]

    return path


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
            <Setter Property="RenderTransformOrigin" Value="0.5,0.5"/>
            <Setter Property="RenderTransform">
                <Setter.Value><ScaleTransform ScaleX="1" ScaleY="1"/></Setter.Value>
            </Setter>
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
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Trigger.EnterActions>
                        <BeginStoryboard>
                            <Storyboard>
                                <DoubleAnimation Storyboard.TargetProperty="RenderTransform.ScaleX" To="1.03" Duration="0:0:0.12"/>
                                <DoubleAnimation Storyboard.TargetProperty="RenderTransform.ScaleY" To="1.03" Duration="0:0:0.12"/>
                            </Storyboard>
                        </BeginStoryboard>
                    </Trigger.EnterActions>
                    <Trigger.ExitActions>
                        <BeginStoryboard>
                            <Storyboard>
                                <DoubleAnimation Storyboard.TargetProperty="RenderTransform.ScaleX" To="1" Duration="0:0:0.12"/>
                                <DoubleAnimation Storyboard.TargetProperty="RenderTransform.ScaleY" To="1" Duration="0:0:0.12"/>
                            </Storyboard>
                        </BeginStoryboard>
                    </Trigger.ExitActions>
                </Trigger>
            </Style.Triggers>
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
        Title="Вибір видів" Height="660" Width="520"
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

        <TextBlock Grid.Row="0" Text="Перейменування видів/аркушів" FontSize="18" FontWeight="Bold"
                   Foreground="#4E4E4E" Margin="0,0,0,2"/>
        <TextBlock Grid.Row="1" Name="tb_counter" Text="Вибрано: 0 вид(ів)"
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

        <TreeView Name="tree_views" Grid.Row="3" BorderThickness="0" Background="White"
                  VirtualizingPanel.IsVirtualizing="True"
                  VirtualizingPanel.VirtualizationMode="Recycling"
                  VirtualizingPanel.ScrollUnit="Pixel">
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
            <Button Name="btn_ok" Content="OK" Style="{StaticResource AccentButton}"
                    Margin="0,0,8,0" Click="ok_click" IsDefault="True"/>
            <Button Name="btn_cancel" Content="Скасувати" Style="{StaticResource SecondaryButton}"
                    Click="cancel_click" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


class ViewSelectWindow(forms.WPFWindow):
    def __init__(self, xaml_source, views):
        forms.WPFWindow.__init__(self, xaml_source, literal_string=True)
        self.all_leaf = []
        self.leaf_index = {}
        self.selected = set()
        self.last_anchor = None
        self.selected_views = []

        self._search_timer = DispatcherTimer()
        self._search_timer.Interval = TimeSpan.FromMilliseconds(200)
        self._search_timer.Tick += self._on_search_timer_tick

        root = self._build_data_tree(views)
        self._render_tree(root, self.tree_views.Items)
        self._update_counter()

    # ---------- побудова дерева ----------
    def _build_data_tree(self, views):
        root = TreeNode(u"root")
        for v in views:
            path = get_browser_path(v)
            node = root
            for part in path:
                node = node.get_or_create_child(part)
            node.views.append(v)
        return root

    def _render_tree(self, node, items_collection):
        for name in sorted(node.children.keys()):
            child = node.children[name]
            tvi = self._make_group_item(name, child.total_count())
            items_collection.Add(tvi)
            self._render_tree(child, tvi.Items)
        for v in sorted(node.views, key=lambda x: x.Name):
            items_collection.Add(self._make_leaf_item(v))

    def _make_group_item(self, name, count):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal

        icon_tb = TextBlock()
        icon_tb.Text = ICONS.get(name, DEFAULT_ICON) + u"  "
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

    def _make_leaf_item(self, view):
        border = Border()
        border.Padding = Thickness(8, 4, 8, 4)
        border.CornerRadius = CornerRadius(4)
        border.Background = Brushes.Transparent
        border.Tag = view

        tb = TextBlock()
        tb.Text = view.Name
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

    # ---------- вибір мишею (Ctrl / Shift / Ctrl+A / Ctrl+D) ----------
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
        self.tb_counter.Text = u"Вибрано: {} вид(ів)".format(len(self.selected))

    def select_all_click(self, sender, args):
        for b in self.all_leaf:
            if b.Visibility == Visibility.Visible:
                self._select_border(b)
        self._update_counter()

    def clear_all_click(self, sender, args):
        self._clear_selection()
        self._update_counter()

    def expand_all_click(self, sender, args):
        self._set_expanded(self.tree_views.Items, True)

    def collapse_all_click(self, sender, args):
        self._set_expanded(self.tree_views.Items, False)

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

    # ---------- живий пошук (з debounce) ----------
    def on_search_changed(self, sender, args):
        self._search_timer.Stop()
        self._search_timer.Start()

    def _on_search_timer_tick(self, sender, args):
        self._search_timer.Stop()
        query = (self.tb_search.Text or u"").strip().lower()
        self._filter_tree(self.tree_views.Items, query)

    def _filter_tree(self, items, query):
        any_visible = False
        for item in items:
            header = item.Header
            if isinstance(header, Border):
                view = header.Tag
                visible = (query == u"") or (query in view.Name.lower())
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

    def ok_click(self, sender, args):
        self.selected_views = [b.Tag for b in self.selected]
        self.Close()

    def cancel_click(self, sender, args):
        self.selected_views = []
        self.Close()


# ╔═╗╔═╗╦═╗╔╦╗  ╦═╗╔═╗╔╗╔╔═╗╔╦╗╔═╗
# ╠╣ ║ ║╠╦╝║║║  ╠╦╝║╣ ║║║╠═╣║║║║╣
# ╚  ╚═╝╩╚═╩ ╩  ╩╚═╚═╝╝╚╝╩ ╩╩ ╩╚═╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
XAML_RENAME = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Перейменування" Height="740" Width="680"
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

        <StackPanel Grid.Row="0" Margin="0,0,0,14">
            <TextBlock Text="Перейменування видів" FontSize="18" FontWeight="Bold" Foreground="#4E4E4E"/>
            <TextBlock Name="tb_counter2" Text="Вибрано: 0 вид(ів)" Foreground="#686868" Margin="0,2,0,0"/>
        </StackPanel>

        <Grid Grid.Row="1" Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <Border Grid.Column="0" BorderBrush="#D7D7D7" BorderThickness="1" CornerRadius="10" Padding="14">
                <StackPanel>
                    <TextBlock Text="➕ Додавання" FontWeight="SemiBold" Foreground="#4E4E4E" Margin="0,0,0,10"/>
                    <TextBlock Text="Префікс" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                    <TextBox Name="tb_prefix" Style="{StaticResource ModernTextBox}" Margin="0,0,0,10"
                             TextChanged="on_text_changed"/>
                    <TextBlock Text="Суфікс" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                    <TextBox Name="tb_suffix" Style="{StaticResource ModernTextBox}" TextChanged="on_text_changed"/>
                </StackPanel>
            </Border>

            <Border Grid.Column="2" BorderBrush="#D7D7D7" BorderThickness="1" CornerRadius="10" Padding="14">
                <StackPanel>
                    <TextBlock Text="🔄 Заміна" FontWeight="SemiBold" Foreground="#4E4E4E" Margin="0,0,0,10"/>
                    <TextBlock Text="Знайти" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                    <TextBox Name="tb_find" Style="{StaticResource ModernTextBox}" Margin="0,0,0,10"
                             TextChanged="on_text_changed"/>
                    <TextBlock Text="Замінити на" Foreground="#686868" FontSize="11" Margin="0,0,0,3"/>
                    <TextBox Name="tb_replace" Style="{StaticResource ModernTextBox}" TextChanged="on_text_changed"/>
                </StackPanel>
            </Border>
        </Grid>

        <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,0,0,8">
            <CheckBox Name="cb_only_changed" Content="Показати лише змінені / попередження"
                       Foreground="#686868" Checked="on_filter_changed" Unchecked="on_filter_changed"/>
        </StackPanel>

        <Border Grid.Row="3" BorderBrush="#D7D7D7" BorderThickness="1" CornerRadius="10">
            <ListBox Name="lv_preview" BorderThickness="0" Background="Transparent"
                     VirtualizingPanel.IsVirtualizing="True"
                     VirtualizingPanel.VirtualizationMode="Recycling">
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
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="30"/>
                                <ColumnDefinition Width="*"/>
                            </Grid.ColumnDefinitions>
                            <TextBlock Grid.Column="0" Text="{Binding OldName}" Foreground="#686868"
                                       TextTrimming="CharacterEllipsis" VerticalAlignment="Center"/>
                            <TextBlock Grid.Column="1" Text="➜" Foreground="#ACB7F5" FontWeight="Bold"
                                       HorizontalAlignment="Center" VerticalAlignment="Center"/>
                            <TextBlock Grid.Column="2" Text="{Binding NewName}" Foreground="{Binding NewNameColor}"
                                       FontWeight="Bold" TextTrimming="CharacterEllipsis" VerticalAlignment="Center"/>
                        </Grid>
                    </DataTemplate>
                </ListBox.ItemTemplate>
            </ListBox>
        </Border>

        <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
            <Button Name="btn_cancel" Content="Скасувати" Style="{StaticResource SecondaryButton}"
                    Margin="0,0,10,0" Click="cancel_click" IsCancel="True"/>
            <Button Name="btn_rename" Content="Перейменувати" Style="{StaticResource AccentButton}"
                    Click="rename_click" IsDefault="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


class RenameWindow(forms.WPFWindow):
    def __init__(self, xaml_source, views):
        forms.WPFWindow.__init__(self, xaml_source, literal_string=True)
        self.views = views
        self.confirmed = False
        self.result_rows = []
        self._results_cache = []

        # 1.3 — уникнення колізій з видами, що НЕ входять у виділення.
        # ВАЖЛИВО: беремо лише звичайні види (не аркуші) — назви аркушів
        # не конфліктують з назвами видів і навпаки (різні простори імен).
        selected_ids = set(v.Id.IntegerValue for v in views)
        self.other_view_names = set(
            v.Name for v in collect_views()
            if v.Id.IntegerValue not in selected_ids and not isinstance(v, ViewSheet)
        )

        self.tb_counter2.Text = u"Вибрано: {} вид(ів)".format(len(views))
        self.update_preview()

    def update_preview(self):
        prefix  = self.tb_prefix.Text or ""
        find    = self.tb_find.Text or ""
        replace = self.tb_replace.Text or ""
        suffix  = self.tb_suffix.Text or ""

        results = compute_final_names(self.views, self.other_view_names, prefix, find, replace, suffix)
        self._results_cache = results

        try:
            only_changed = bool(self.cb_only_changed.IsChecked)
        except Exception:
            only_changed = False

        items = []
        will_rename = 0
        unchanged = 0
        warnings = 0

        for r in results:
            status = r["status"]
            icons = u""
            if r["had_forbidden"]:
                icons += u"✂ "
            if r["too_long"]:
                icons += u"📏 "

            if status == "empty":
                icons = u"⚠ "
                display_name = u"Порожнє ім'я — пропущено"
            elif status == "duplicate":
                icons = u"⚠ " + icons
                display_name = r["final_name"]
            else:
                display_name = r["final_name"]

            if status == "changed":
                will_rename += 1
            elif status == "unchanged":
                unchanged += 1
            if status in ("duplicate", "empty"):
                warnings += 1

            if only_changed and status == "unchanged":
                continue

            items.append(PreviewItem(r["view"].Name, icons + display_name, status))

        self.lv_preview.ItemsSource = items
        self.tb_counter2.Text = (
            u"Вибрано: {}   Буде перейменовано: {}   Без змін: {}   Попередження: {}"
            .format(len(self.views), will_rename, unchanged, warnings)
        )

    def on_text_changed(self, sender, args):
        self.update_preview()

    def on_filter_changed(self, sender, args):
        self.update_preview()

    def rename_click(self, sender, args):
        self.update_preview()
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

#1️Вибір видів (дерево, побудоване на реальній структурі Диспетчера проекту)
all_views = collect_views()
if not all_views:
    forms.alert(u'У проєкті немає видів для перейменування.', exitscript=True)

select_win = ViewSelectWindow(XAML_SELECT, all_views)
select_win.ShowDialog()

selected_views = select_win.selected_views
if not selected_views:
    forms.alert(u'Види не обрано. Спробуйте ще раз.', exitscript=True)

#Форма перейменування з живим прев'ю
rename_win = RenameWindow(XAML_RENAME, selected_views)
rename_win.ShowDialog()

if not rename_win.confirmed:
    script.exit()

#Зміна назв видів — використовуємо саме те, що показав прев'ю (WYSIWYG)
t = Transaction(doc, "Перейменування_BIM")
t.Start()

start_time = time.time()
renamed_count   = 0
skipped_count   = 0
duplicate_count = 0
error_rows      = []

for row in rename_win.result_rows:
    view = row["view"]
    status = row["status"]
    final_name = row["final_name"]

    if status == "empty":
        skipped_count += 1
        continue

    if status == "duplicate":
        duplicate_count += 1

    if final_name != view.Name:
        try:
            view.Name = final_name
            renamed_count += 1
        except Exception as e:
            error_rows.append(u"{}: {}".format(view.Name, str(e)))

t.Commit()
elapsed = time.time() - start_time

#Підсумковий звіт
report_lines = [u"Успіх. Перейменовано {} вид(ів).".format(renamed_count)]
if duplicate_count:
    report_lines.append(u"Виправлено дублікатів (додано (1), (2)...): {}".format(duplicate_count))
if skipped_count:
    report_lines.append(u"Пропущено через порожнє ім'я: {}".format(skipped_count))
if error_rows:
    report_lines.append(u"Помилок: {}".format(len(error_rows)))

forms.alert(u"\n".join(report_lines))
#███████████████████████████████████████████████████████████████████████████