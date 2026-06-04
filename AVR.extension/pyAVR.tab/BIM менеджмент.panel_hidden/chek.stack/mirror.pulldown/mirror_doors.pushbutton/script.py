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


__doc__ = "Selects All Door Instances that have been Mirrored."
__title__ = "Відзеркалені\nДвері"

from rpw import doc, uidoc, DB, UI, db, ui

doors = db.Collector(of_category='Doors').elements
mirrored_door = [door for door in doors if getattr(door, 'Mirrored', False)]

msg = "Mirrored: {} of {} Doors".format(len(mirrored_door), len(doors))
ui.forms.Alert(msg, title="Mirrored Doors")

selection = ui.Selection(mirrored_door)
