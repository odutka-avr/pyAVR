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


__doc__ = """Renamber rooms"""
#__title__ = "CreateOpening"
__author__ = "Vladyslav Mashchenko"

import clr
import os
from dotenv import load_dotenv

load_dotenv()

from pyrevit import forms

import clr
clr.AddReference("System.Net.Http")

import clr
import System
clr.AddReference("System.Net.Http")
import System.Net.Http

import System.Text

clr.AddReference("System.Threading.Tasks")
import System.Threading.Tasks
import clr
import System
clr.AddReference("System.Net.Http")
clr.AddReference("System.Web.Extensions")
clr.AddReference("System.Threading.Tasks")
from System.Net.Http import HttpClient, StringContent, Headers, AuthenticationHeaderValue
from System.Text import Encoding
from Newtonsoft.Json import JsonConvert

from Newtonsoft.Json import JsonConvert

# Встановлюємо ключ API OpenAI
api_key = api_key = os.getenv("OPEN_AI_API_KEY")

def get_openai_response(prompt):
    client = HttpClient()
    client.DefaultRequestHeaders.Authorization = AuthenticationHeaderValue("Bearer", api_key)

    response = client.PostAsync("https://api.openai.com/v1/engines/davinci-codex/completions", StringContent('{ "prompt": "' + prompt + '", "max_tokens": 150 }', Encoding.UTF8, "application/json")).Result

    try:
        response_string = response.Content.ReadAsStringAsync().Result
        response_json = JsonConvert.DeserializeObject(response_string)
        return response_json
    except :
        print('Invalid JSON in response')
    


input_text = forms.ask_for_string(default='Сформуй запит', prompt='Chat with GPT:', title='ChatGPT')

response = get_openai_response(input_text)

print(response)
