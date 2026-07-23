# -*- coding: utf-8 -*-

import os
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System")

from pyrevit import forms
from System.Windows import (
    Window, Application, Visibility, Thickness, MessageBox,
    VerticalAlignment, GridLength, GridUnitType
)

from System.Windows.Controls import (
    StackPanel, Orientation, Border, Grid, ColumnDefinition,
    TextBlock, ComboBox, Button
)

from System.Windows.Media import Brushes, ColorConverter, SolidColorBrush

from pyrevit import forms, script

# configure xaml path
XAML_PATH = os.path.join(os.path.dirname(__file__), "form.xaml")

# configure debugging
logger = script.get_logger()


def brush(hex_color):
    return SolidColorBrush(ColorConverter.ConvertFromString(hex_color))

def _auto_col():
    cd = ColumnDefinition()
    cd.Width = GridLength(1, GridUnitType.Auto)
    return cd

def _star_col():
    cd = ColumnDefinition()
    cd.Width = GridLength(1, GridUnitType.Star)
    return cd

class RebarRow:

    """
    Один рядок (бокс) списку проміжних стержнів: стрілки вгору/вниз,
    combobox типу арматури, кнопка "Видалити".
 
    is_corner_bar завжди False для рядків цього класу — кутовий стержень
    (секція 1.1 у формі) не є екземпляром RebarRow, а окремим статичним
    ComboBox, визначеним прямо в XAML (LowerCornerRebarCombo /
    UpperCornerRebarCombo). Прапорець тут присутній лише для
    узгодженості формату даних, що повертає get_data().
    """

    REBAR_TYPES = None

    def __init__(self, rebar_panel, row_list, min_rows):
        self.rebar_panel = rebar_panel
        self.min_rows = min_rows
        self.row_list = row_list
        self.is_corner_bar = False

        self.item = self._build_ui()
    
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

        # up/down arrows
        arrows_panel = StackPanel()
        arrows_panel.Orientation = Orientation.Vertical
        arrows_panel.VerticalAlignment = VerticalAlignment.Center
        arrows_panel.Margin = Thickness(0, 0, 12, 0)

        self.up_btn = Button()
        self.up_btn.Content = u"\u25B2"
        self.up_btn.Padding = Thickness(4, 0, 4, 0)
        self.up_btn.Margin = Thickness(0, 0, 0, 2)
        self.up_btn.Click += self.move_up

        self.down_btn = Button()
        self.down_btn.Content = u"\u25BC"
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
        for item in sorted(self.REBAR_TYPES):
            self.rebar_combo.Items.Add(item)
        
        self.rebar_combo.SelectedIndex = 0
        rebar_box.Children.Add(rebar_label)
        rebar_box.Children.Add(self.rebar_combo)
        Grid.SetColumn(rebar_box, 1)
        grid.Children.Add(rebar_box)

        # # --- combobox: hook type ---
        # bend_box = StackPanel()
        # bend_box.Margin = Thickness(0, 0, 8, 0)
        # bend_label = TextBlock()
        # bend_label.Text = "Тип відгину"
        # bend_label.FontSize = 11
        # bend_label.Foreground = brush("#777777")
        # bend_label.Margin = Thickness(0, 0, 0, 2)
        # self.bend_combo = ComboBox()
        # for item in BEND_TYPES:
        #     self.bend_combo.Items.Add(item)
        # self.bend_combo.SelectedIndex = 0
        # bend_box.Children.Add(bend_label)
        # bend_box.Children.Add(self.bend_combo)
        # Grid.SetColumn(bend_box, 2)
        # grid.Children.Add(bend_box)

        # --- delete button ---
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

    def move_up(self, sender, args):
        idx = self.rebar_panel.Children.IndexOf(self.item)
        if idx > 0:
            self.rebar_panel.Children.RemoveAt(idx)
            # likewise remove item in row list
            self.row_list.remove(self)

            self.rebar_panel.Children.Insert(idx - 1, self.item)
            self.row_list.insert(idx - 1, self)
    
    def move_down(self, sender, args):
        idx = self.rebar_panel.Children.IndexOf(self.item)
        if idx < self.rebar_panel.Children.Count - 1:
            self.rebar_panel.Children.RemoveAt(idx)
            # likewise remove item in row list
            self.row_list.remove(self)

            self.rebar_panel.Children.Insert(idx + 1, self.item)
            self.row_list.insert(idx + 1, self)
    
    def delete_self(self, sender, args):
        if self.rebar_panel.Children.Count <= self.min_rows:
            MessageBox.Show(
                "Мінімальна кількість стержнів у списку: {0}".format(self.min_rows),
                "Неможливо видалити"
            )
            return
        self.rebar_panel.Children.Remove(self.item)
        self.row_list.remove(self)
    
    def get_data(self):
        return {
            "rebar_type": self.rebar_combo.SelectedItem,
        }


class Form(forms.WPFWindow):

    SIDE_MIN_BARS = 1

    def __init__(self, rebar_types, rebar_shapes):
        forms.WPFWindow.__init__(self, XAML_PATH)

        self.rebar_types = rebar_types
        self.rebar_shapes = rebar_shapes

        RebarRow.REBAR_TYPES = rebar_types.keys()
        logger.debug(self.rebar_types)

        # rebar container lists
        self.corner_bars = list()
        self.top_bottom_bars = list()
        self.side_bars = list()
        
        # заповнення кутових combobox-ів (секція 1.1, симетричні кутові стержні)
        for key in sorted(self.rebar_types):
            self.CornerRebarCombo.Items.Add(key)
        self.CornerRebarCombo.SelectedIndex = 0

        for key in sorted(self.rebar_types):
            # key is name (string) of rebar type
            self.StirrupClassCombo.Items.Add(key)
        self.StirrupClassCombo.SelectedIndex = 0


    def _add_row(self, rebar_panel, row_list, min_rows):
        row = RebarRow(rebar_panel, row_list, min_rows)
        row_list.append(row)
        rebar_panel.Children.Add(row.item)
        return row


    # ---- button handlers for adding additional bars between corner bars ----
    def AddSideAdditionalRebar_Click(self, sender, args):
        self._add_row(self.SideAdditionalPanel, self.side_bars, self.SIDE_MIN_BARS)
    
    def AddTopBottomAdditionalRebar_Click(self, sender, args):
        self._add_row(self.TopBottomAdditionalPanel, self.top_bottom_bars, self.SIDE_MIN_BARS)
    

    # ---- секція 1.2/1.3: right+left bars ----
    def SideAdditionalCheckBox_Checked(self, sender, args):
        self.SideAdditionalContainer.Visibility = Visibility.Visible
        # автоматично створити один row з дефолтним типом при першому
        # увімкненні (якщо список ще порожній)
        if not self.side_bars:
            self._add_row(self.SideAdditionalPanel, self.side_bars, self.SIDE_MIN_BARS)
 
    def SideAdditionalCheckBox_Unchecked(self, sender, args):
        self.SideAdditionalContainer.Visibility = Visibility.Collapsed
 

    # ---- секція 1.2/1.3: top+bottom bars ----
    def TopBottomAdditionalCheckBox_Checked(self, sender, args):
        self.TopBottomAdditionalContainer.Visibility = Visibility.Visible
        if not self.top_bottom_bars:
            self._add_row(self.TopBottomAdditionalPanel, self.top_bottom_bars, self.SIDE_MIN_BARS)
 
    def TopBottomAdditionalCheckBox_Unchecked(self, sender, args):
        self.TopBottomAdditionalContainer.Visibility = Visibility.Collapsed

    
    def _validate_empty_offset_field(self, field_value):
        if not field_value:
            return 0
        return field_value
    
    def collect_data(self):
        def rows_to_list(rows):
            return [r.get_data() for r in rows]
        
        def corner_bar_to_list(corner_combo):
            """
            Form a list of 4 seperate bars that will be located in 4 corners of the column.
            Bars have identical type and diameter. 
            """
            corner_bar = {"rebar_type": corner_combo.SelectedItem}
            return [corner_bar for _ in range(4)]
 
        data = {
            "protective_layer": {
                "c_top_bottom": self.CVerticalBox.Text,
                "c_side": self.CSideBox.Text,
            },
            "corner": {
                "bars": corner_bar_to_list(self.CornerRebarCombo),
                "top_offset": self.CornerOffsetBox.Text,
            },
            "top_bottom": {
                "bars": rows_to_list(self.top_bottom_bars) if bool(self.TopBottomAdditionalCheckBox.IsChecked) else [],
                "top_offset": self.TopBottomAdditionalOffsetBox.Text,
            },
            "sides": {
                "bars": rows_to_list(self.side_bars) if bool(self.SideAdditionalCheckBox.IsChecked) else [],
                "top_offset": self.SideAdditionalOffsetBox.Text,
            },
            "stirrups": {
                "rebar_type":  self.StirrupClassCombo.SelectedItem,
                "span_zone_step": self.SpanStepBox.Text,
                "support_zone_step": self.SupportStepBox.Text,
            },
        }
        return data


    def PushData_Click(self, sender, args):      
        try:
            self.DialogResult = True
        except Exception as e:
            logger.debug("Push data error, {}".format(e))
    
    def show(self):
        dialog_result = self.ShowDialog()

        if dialog_result:
            # all data filled, continue on with table generation
            return self.collect_data()
        
        # user exited form, not all fields are set, do not generate table
        return False
    


