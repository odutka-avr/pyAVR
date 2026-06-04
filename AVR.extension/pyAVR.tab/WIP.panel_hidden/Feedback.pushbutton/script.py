


import clr
import webbrowser  


clr.AddReference('System.Net')
import System.Net
from decimal import Decimal

from datetime import datetime
now = datetime.now()
data = now.strftime("%y%m%d")

clr.AddReference('System.Net')
import System.Net
from decimal import Decimal

def send_msg(text):
    token = "5801309479:AAEUQXyuWqEakSdZcQjwaAQFWDKNT_OPFpw"
    chat_id = "588589629"
    url_req = "https://api.telegram.org/bot" + token + "/sendMessage" + "?chat_id=" + chat_id + "&text=" + text
    webclient = System.Net.WebClient()
    return webclient.DownloadString(url_req)


uiapp = __revit__
app = uiapp.Application
username = app.Username


from pyrevit import forms

ask = forms.ask_for_string(
    default='Feedback',
    prompt='Feedback:',
    title='Feedback'
)


send_msg(username +" - '"+ ask + "'")