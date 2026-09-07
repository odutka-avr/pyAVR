# -*- coding: utf-8 -*-
__title__ = "Вимоги до інсоляції"
__doc__     = """Версія = 1.0   
Дата створення 28.07.2026
________________________________________________________________

Кнопка виводить вікно з інформацією про розрахунок 
інсоляції відповідно до чинних норм і з принципом 
роботи інших кнопок розділу.
________________________________________________________________

Інсоляція - пряме сонячне опроміненння поверхонь чи просторів
"""

"""Window explaining direct sun hours calculations and 
requirements according to Ukranian Building norms.    

Engine: IronPython 2.7 (pyRevit default).
"""

from pyrevit import forms, script


XAML = u"""

"""
 
 
class HelpWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, 'panel.xaml')
        self.result = None
 
    def expand_all(self, sender, args):
        for child in self.sections.Children:
            try:
                child.IsExpanded = True
            except AttributeError:
                pass  # заголовки/нотатки, що не є Expander
 
    def collapse_all(self, sender, args):
        for child in self.sections.Children:
            try:
                child.IsExpanded = False
            except AttributeError:
                pass
 
 
HelpWindow().show()
 