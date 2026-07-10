# -*- coding: utf-8 -*-
"""
Форма армування балки.
Побудована на XAML (MainWindow.xaml) + pythonnet (WPF).

Встановлення залежностей:
    pip install pythonnet wpf

Запуск (тільки Windows, .NET/WPF):
    python main.py
"""

import json
import os

import clr
import wpf

from System.Windows import (
    Window, Application, Visibility, Thickness, MessageBox,
    VerticalAlignment, GridLength, GridUnitType
)
from System.Windows.Controls import (
    StackPanel, Orientation, Border, Grid, ColumnDefinition,
    TextBlock, ComboBox, Button
)
from System.Windows.Media import Brushes, ColorConverter, SolidColorBrush


# ---------------------------------------------------------------------------
#  Довідники
# ---------------------------------------------------------------------------

REBAR_CLASSES = ["А240С", "А400С", "А500С", "А600", "А600С", "А800", "А1000"]

BEND_TYPES = [
    "Без відгину",
    "Гак стандартний",
    "Гак 90°",
    "Гак 135°",
    "Лапка",
    "Петля",
]

MIN_ROWS_MAIN = 2          # мінімум боксів для секцій 2 і 3
MIN_ROWS_CONSTRUCTIVE = 1  # мінімум боксів для секції 4


def brush(hex_color):
    return SolidColorBrush(ColorConverter.ConvertFromString(hex_color))


# ---------------------------------------------------------------------------
#  Клас одного "стержня" (рядка) у списку арматури
# ---------------------------------------------------------------------------

class RebarRow:
    """
    Один бокс зі стрілочками вгору/вниз, combobox типу арматури,
    combobox типу відгину і кнопкою "Видалити".
    """

    def __init__(self, panel, min_rows, on_changed=None):
        self.panel = panel
        self.min_rows = min_rows
        self.on_changed = on_changed

        self.border = self._build_ui()

    # ---- побудова UI одного рядка -----------------------------------
    def _build_ui(self):
        border = Border()
        border.BorderBrush = brush("#DDDDDD")
        border.BorderThickness = Thickness(1)
        border.Padding = Thickness(10)
        border.Margin = Thickness(0, 0, 0, 8)
        border.Tag = self

        grid = Grid()
        grid.ColumnDefinitions.Add(_auto_col())   # 0: стрілочки вгору/вниз
        grid.ColumnDefinitions.Add(_star_col())   # 1: тип арматури
        grid.ColumnDefinitions.Add(_star_col())   # 2: тип відгину
        grid.ColumnDefinitions.Add(_auto_col())   # 3: кнопка "Видалити"

        # --- стрілочки вгору/вниз ---
        arrows_panel = StackPanel()
        arrows_panel.Orientation = Orientation.Vertical
        arrows_panel.VerticalAlignment = VerticalAlignment.Center
        arrows_panel.Margin = Thickness(0, 0, 12, 0)

        self.up_btn = Button()
        self.up_btn.Content = "\u25B2"
        self.up_btn.Padding = Thickness(4, 0, 4, 0)
        self.up_btn.Margin = Thickness(0, 0, 0, 2)
        self.up_btn.Click += self.move_up

        self.down_btn = Button()
        self.down_btn.Content = "\u25BC"
        self.down_btn.Padding = Thickness(4, 0, 4, 0)
        self.down_btn.Click += self.move_down

        arrows_panel.Children.Add(self.up_btn)
        arrows_panel.Children.Add(self.down_btn)
        Grid.SetColumn(arrows_panel, 0)
        grid.Children.Add(arrows_panel)

        # --- combobox: Тип арматури ---
        rebar_box = StackPanel()
        rebar_box.Margin = Thickness(0, 0, 8, 0)
        rebar_label = TextBlock()
        rebar_label.Text = "Тип арматури"
        rebar_label.FontSize = 11
        rebar_label.Foreground = brush("#777777")
        rebar_label.Margin = Thickness(0, 0, 0, 2)
        self.rebar_combo = ComboBox()
        for item in REBAR_CLASSES:
            self.rebar_combo.Items.Add(item)
        self.rebar_combo.SelectedIndex = 0
        rebar_box.Children.Add(rebar_label)
        rebar_box.Children.Add(self.rebar_combo)
        Grid.SetColumn(rebar_box, 1)
        grid.Children.Add(rebar_box)

        # --- combobox: Тип відгину ---
        bend_box = StackPanel()
        bend_box.Margin = Thickness(0, 0, 8, 0)
        bend_label = TextBlock()
        bend_label.Text = "Тип відгину"
        bend_label.FontSize = 11
        bend_label.Foreground = brush("#777777")
        bend_label.Margin = Thickness(0, 0, 0, 2)
        self.bend_combo = ComboBox()
        for item in BEND_TYPES:
            self.bend_combo.Items.Add(item)
        self.bend_combo.SelectedIndex = 0
        bend_box.Children.Add(bend_label)
        bend_box.Children.Add(self.bend_combo)
        Grid.SetColumn(bend_box, 2)
        grid.Children.Add(bend_box)

        # --- кнопка Видалити ---
        self.delete_btn = Button()
        self.delete_btn.Content = "Видалити"
        self.delete_btn.Foreground = brush("#CC3333")
        self.delete_btn.Background = Brushes.White
        self.delete_btn.BorderBrush = brush("#CC3333")
        self.delete_btn.Padding = Thickness(10, 4, 10, 4)
        self.delete_btn.VerticalAlignment = VerticalAlignment.Center
        self.delete_btn.Click += self.delete_self
        Grid.SetColumn(self.delete_btn, 3)
        grid.Children.Add(self.delete_btn)

        border.Child = grid
        return border

    # ---- дії -----------------------------------------------------------
    def move_up(self, sender, args):
        idx = self.panel.Children.IndexOf(self.border)
        if idx > 0:
            self.panel.Children.RemoveAt(idx)
            self.panel.Children.Insert(idx - 1, self.border)

    def move_down(self, sender, args):
        idx = self.panel.Children.IndexOf(self.border)
        if idx < self.panel.Children.Count - 1:
            self.panel.Children.RemoveAt(idx)
            self.panel.Children.Insert(idx + 1, self.border)

    def delete_self(self, sender, args):
        if self.panel.Children.Count <= self.min_rows:
            MessageBox.Show(
                "Мінімальна кількість стержнів у списку: {0}".format(self.min_rows),
                "Неможливо видалити"
            )
            return
        self.panel.Children.Remove(self.border)
        if self.on_changed:
            self.on_changed()

    # ---- дані ------------------------------------------------------
    def to_dict(self):
        return {
            "rebar_class": self.rebar_combo.SelectedItem,
            "bend_type": self.bend_combo.SelectedItem,
        }


def _auto_col():
    cd = ColumnDefinition()
    cd.Width = GridLength(1, GridUnitType.Auto)
    return cd


def _star_col():
    cd = ColumnDefinition()
    cd.Width = GridLength(1, GridUnitType.Star)
    return cd


# ---------------------------------------------------------------------------
#  Головне вікно
# ---------------------------------------------------------------------------

class MainWindow(Window):

    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        wpf.LoadComponent(self, os.path.join(base_dir, "MainWindow.xaml"))

        # списки RebarRow-об'єктів для кожної панелі
        self.lower_left_rows = []
        self.lower_right_rows = []
        self.upper_left_rows = []
        self.upper_right_rows = []
        self.constructive_top_rows = []
        self.constructive_bottom_rows = []

        # заповнення combobox класу арматури хомутів
        for item in REBAR_CLASSES:
            self.StirrupClassCombo.Items.Add(item)
        self.StirrupClassCombo.SelectedIndex = 0

        # початкове наповнення списків (2 боксів для секцій 2 і 3)
        self._add_row(self.LowerLeftPanel, self.lower_left_rows, MIN_ROWS_MAIN)
        self._add_row(self.LowerLeftPanel, self.lower_left_rows, MIN_ROWS_MAIN)

        self._add_row(self.LowerRightPanel, self.lower_right_rows, MIN_ROWS_MAIN)
        self._add_row(self.LowerRightPanel, self.lower_right_rows, MIN_ROWS_MAIN)

        self._add_row(self.UpperLeftPanel, self.upper_left_rows, MIN_ROWS_MAIN)
        self._add_row(self.UpperLeftPanel, self.upper_left_rows, MIN_ROWS_MAIN)

        self._add_row(self.UpperRightPanel, self.upper_right_rows, MIN_ROWS_MAIN)
        self._add_row(self.UpperRightPanel, self.upper_right_rows, MIN_ROWS_MAIN)

        # секція 4: по 1 боксу для "верх" і "низ" створюються одразу,
        # хоча секція прихована, поки не позначено чекбокс
        self._add_row(self.ConstructiveTopPanel, self.constructive_top_rows, MIN_ROWS_CONSTRUCTIVE)
        self._add_row(self.ConstructiveBottomPanel, self.constructive_bottom_rows, MIN_ROWS_CONSTRUCTIVE)

    # ---- допоміжний метод додавання рядка ------------------------------
    def _add_row(self, panel, row_list, min_rows):
        row = RebarRow(panel, min_rows)
        row_list.append(row)
        panel.Children.Add(row.border)
        return row

    # ---- обробники кнопок "Додати стержень" ----------------------------
    def AddLowerLeft_Click(self, sender, args):
        self._add_row(self.LowerLeftPanel, self.lower_left_rows, MIN_ROWS_MAIN)

    def AddLowerRight_Click(self, sender, args):
        self._add_row(self.LowerRightPanel, self.lower_right_rows, MIN_ROWS_MAIN)

    def AddUpperLeft_Click(self, sender, args):
        self._add_row(self.UpperLeftPanel, self.upper_left_rows, MIN_ROWS_MAIN)

    def AddUpperRight_Click(self, sender, args):
        self._add_row(self.UpperRightPanel, self.upper_right_rows, MIN_ROWS_MAIN)

    def AddConstructiveTop_Click(self, sender, args):
        self._add_row(self.ConstructiveTopPanel, self.constructive_top_rows, MIN_ROWS_CONSTRUCTIVE)

    def AddConstructiveBottom_Click(self, sender, args):
        self._add_row(self.ConstructiveBottomPanel, self.constructive_bottom_rows, MIN_ROWS_CONSTRUCTIVE)

    # ---- чекбокс "конструктивні стержні" --------------------------------
    def ConstructiveCheckBox_Checked(self, sender, args):
        self.ConstructiveListsContainer.Visibility = Visibility.Visible

    def ConstructiveCheckBox_Unchecked(self, sender, args):
        self.ConstructiveListsContainer.Visibility = Visibility.Collapsed

    # ---- збір даних форми -------------------------------------------
    def collect_data(self):
        def rows_to_list(rows):
            return [r.to_dict() for r in rows]

        data = {
            "protective_layer": {
                "c_top": self.CTopBox.Text,
                "c_bottom": self.CBottomBox.Text,
                "c_side": self.CSideBox.Text,
            },
            "lower_longitudinal": {
                "left_face": rows_to_list(self.lower_left_rows),
                "right_face": rows_to_list(self.lower_right_rows),
            },
            "upper_longitudinal": {
                "left_face": rows_to_list(self.upper_left_rows),
                "right_face": rows_to_list(self.upper_right_rows),
            },
            "constructive_rebar": {
                "enabled": bool(self.ConstructiveCheckBox.IsChecked),
                "top": rows_to_list(self.constructive_top_rows),
                "bottom": rows_to_list(self.constructive_bottom_rows),
            },
            "stirrups": {
                "rebar_class": self.StirrupClassCombo.SelectedItem,
                "span_zone_step": self.SpanStepBox.Text,
                "support_zone_step": self.SupportStepBox.Text,
                "l1": self.L1Box.Text,
                "l2": self.L2Box.Text,
            },
        }
        return data

    def ShowData_Click(self, sender, args):
        data = self.collect_data()
        MessageBox.Show(json.dumps(data, ensure_ascii=False, indent=2), "Дані форми")


if __name__ == "__main__":
    Application().Run(MainWindow())
