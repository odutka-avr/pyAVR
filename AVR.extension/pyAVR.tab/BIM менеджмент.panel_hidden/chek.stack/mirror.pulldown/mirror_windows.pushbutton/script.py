# coding: utf8
##################################################
## Python script in pyRevit
##################################################
## Author: Vladyslav Mashchenko
## Copyright: Copyright 2022
## Credits: [Vladyslav Mashchenko]
## Version: 1.0.0
## Email: vladyslav.mashchenko@outlook.com
##################################################

#pylint: disable=E0401,W0621,W0631,C0413,C0111,C0103
__doc__ = "Selects All Window Instances that have been Mirrored."
__author__ = '@gtalarico'
__title__ = "Відзеркалені\nВікна"

from rpw import db, ui

windows = db.Collector(of_category='Windows').elements
mirrored_windows = [x for x in windows if getattr(x, 'Mirrored', False)]

msg = "Mirrored: {} of {} Windows".format(len(mirrored_windows), len(windows))
ui.forms.Alert(msg, title="Mirrored Windows")

selection = ui.Selection(mirrored_windows)
