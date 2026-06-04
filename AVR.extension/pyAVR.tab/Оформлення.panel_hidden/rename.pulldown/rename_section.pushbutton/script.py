import clr
from pyrevit import forms
from pyrevit import script
from System import Guid

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel #Import Microsoft.Office.Interop.Excel libery

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import OpenFileDialog, MessageBox

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import *

clr.AddReference('RevitAPIUI')
from Autodesk.Revit.UI import *

clr.AddReference('System')
from System.Collections.Generic import List

clr.AddReference('RevitNodes')
import Revit
clr.ImportExtensions(Revit.GeometryConversion)
clr.ImportExtensions(Revit.Elements)

clr.AddReference('RevitServices')
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

#from Autodesk.Revit import DB
#doc = DocumentManager.Instance.CurrentDBDocument
doc = __revit__.ActiveUIDocument.Document


collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Views).OfClass(ViewSection).ToElements()
views=[]
for i in collector:
	viewType = i.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
	if i.IsTemplate == 0:
		views.append(i)
numb=1

for i in views:
    try:
		param1 = i.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).AsString().Split("_")[0]
		param2 = i.get_Parameter(Guid('c935b866-e921-4356-952b-4f573a82f7a8')).AsString()
		try:
			lens = len(param2.Split("_"))
		except:
			lens =1
		if lens < 2:
			param2 = param2 + "_Coord"
		i.Name = param1 + "_" + param2 + "_" + + str(numb)
    except:
		param1 = i.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).AsString().Split("_")[0]
		param2 = i.get_Parameter(Guid('c935b866-e921-4356-952b-4f573a82f7a8')).AsString()		
		try:
			lens = len(param2.Split("_"))
		except:
			lens = 1
		if lens < 2:
			param2 = param2 + "_Coord"
		numb+=1
		i.Name = param1 + "_" + param2 + "_" + str(numb) + "_Copy "+str(numb)
		continue
T.Commit()