import clr
from pyrevit import forms
from pyrevit import script
from pyrevit.compat import safe_strtype
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


param1 = Guid('91549a35-74a8-4909-a7cd-09badc3d90db')
param2 = Guid('c935b866-e921-4356-952b-4f573a82f7a8')


##########____COLLECT FLOOR PLAN VIEW ____#######################	
collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Views).OfClass(ViewPlan).WhereElementIsNotElementType().ToElements()
views=[]

for i in collector:
    if i.IsTemplate == 0:
       views.append(i)
numb = 1

T = Transaction(doc, "Rename vews")
T.Start()

for i in views:
    try:
		param1 = i.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).AsString().Split("_")[0]
		p06 = i.get_Parameter(Guid('2d6e37cc-d8ba-4755-837e-ee9740adbbf0')).AsString()

		param = i.get_Parameter(BuiltInParameter.VIEW_TEMPLATE_FOR_SCHEDULE).AsValueString()
		
		p01 = param.Split("_")[2]
		p02 = param.Split("_")[1]
		p03 = param.Split("_")[3]
		try:
			p04 = param.Split("_")[4] 
			p04 = "_" + p04 
		except:
			p04 = ""
		try:
			if p06 != "##":
				#p06 = p06.Split("_")[4] 
				p06 = "_" + p06		
			else:
				p06 = ""
			
				
		except:
			p06 = "bbb"

		p05 = i.get_Parameter(BuiltInParameter.PLAN_VIEW_LEVEL).AsString()
		"""""
		try:
			lens = len(param2.Split("_"))
		except:
			lens =1
		if lens < 2:
			param2 = param2 + force_to_unicode("_Coord")
		"""
		i.Name = p01 + "_" + p02 + "_" + p03 + p04 + p06 + "_" + p05
    except:
		s=0		
		"""

		param1 = i.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).AsString().Split("_")[0]
		param2 = i.get_Parameter(Guid('c935b866-e921-4356-952b-4f573a82f7a8')).AsString()		
		level = i.get_Parameter(BuiltInParameter.PLAN_VIEW_LEVEL).AsString()
		try:
			lens = len(param2.Split("_"))
		except:
			lens = 1
		if lens < 2:
			param2 = param2 + force_to_unicode("_Coord")
		numb+=1
		i.Name = param1 + "_" + param2 + "_" + level + force_to_unicode("_Copy ")+str(numb)
		continue
		"""
T.Commit()