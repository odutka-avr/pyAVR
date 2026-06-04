import clr # Import language .Net in Python

# CLR In Revit API
clr.AddReference('RevitAPI') # RevitAPI Library
clr.AddReference('RevitAPIUI') # RevitAPI Library
from Autodesk.Revit.DB import * # Class Settings to Import
# from Autodesk.Revit.UI import * # Class Settings UI to Import

# Libraries Dynamo
clr.AddReference('RevitNodes') # Converting Elements and Geometry
import Revit # Extending class source methods through ImportExtensions
clr.ImportExtensions(Revit.GeometryConversion)
clr.ImportExtensions(Revit.Elements)
clr.AddReference('RevitServices') # Work with document and transactions
from RevitServices.Persistence import DocumentManager  # Document manager
from RevitServices.Transactions import TransactionManager  # Transaction manager

# System libraries
import System # Working with .NET System Types and Data Structures
from System.Collections.Generic import * #You can specify List
import sys # IronPython interpreter settings, library path
sys.path.append(r'C:\Program Files (x86)\IronPython 2.7\Lib')


# Document and Transactions
doc = DocumentManager.Instance.CurrentDBDocument # Retrieving a document file
# uidoc = DM.Instance.CurrentUIApplication.ActiveUIDocument # Interface

### DEF
cat_list = [
BuiltInCategory.OST_Views,
BuiltInCategory.OST_Sheets,
BuiltInCategory.OST_Schedules
]

typed_list = List[BuiltInCategory](cat_list)
filter = ElementMulticategoryFilter(typed_list)
elements = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()

nav = IN[0]
stvw = IN[1]



TransactionManager.Instance.EnsureInTransaction(doc)
for i in elements:
	if i.IsTemplate != True and i.Name != nav:
		viewDel.append(i)
	if i.Name == nav:
		nvView.append(i)
TransactionManager.Instance.TransactionTaskDone()	

OUT = [viewDel,nvView]