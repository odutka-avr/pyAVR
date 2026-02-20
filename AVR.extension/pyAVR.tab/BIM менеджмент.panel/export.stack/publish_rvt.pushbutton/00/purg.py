#Part of script by Matthew Taylor translated in Python by Oliver E Green

#Alban de Chasteigner 2020
#twitter : @geniusloci_bim
#geniusloci.bim@gmail.com

import clr
import System
from System.Collections.Generic import List

clr.AddReference("RevitAPI")
import Autodesk
from Autodesk.Revit.DB import *

clr.AddReference("RevitServices")
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

bool=IN[0]
deeply=IN[1]
inputdoc = UnwrapElement(IN[2]) if isinstance(IN[2],list) else [UnwrapElement(IN[2])]
inputdoc = inputdoc[0]

if inputdoc == None:
	doc = DocumentManager.Instance.CurrentDBDocument
elif isinstance (inputdoc, Document) :
	doc = inputdoc
elif isinstance (inputdoc, RevitLinkInstance) :
	doc = inputdoc.GetLinkDocument()
else: doc = DocumentManager.Instance.CurrentDBDocument

#GetPurgeableElements by Oliver E Green 
def GetPurgeableElements(doc, rule_id_list):
	failure_messages = PerformanceAdviser.GetPerformanceAdviser().ExecuteRules(doc, rule_id_list)
	if failure_messages.Count > 0:
		purgeable_element_ids = failure_messages[0].GetFailingElements()
		return purgeable_element_ids

#A constant 
PURGE_GUID = "e8c63650-70b7-435a-9010-ec97660c1bda"

#A generic list of PerformanceAdviserRuleIds as required by the ExecuteRules method
rule_id_list = List[PerformanceAdviserRuleId]()

#Iterating through all PerformanceAdviser rules looking to find that which matches PURGE_GUID
for rule_id in PerformanceAdviser.GetPerformanceAdviser().GetAllRuleIds():
	if str(rule_id.Guid) == PURGE_GUID:
		rule_id_list.Add(rule_id)
		break

#Attempting to retrieve the elements which can be purged
purgeable_element_ids = GetPurgeableElements(doc, rule_id_list)

TransactionManager.Instance.ForceCloseTransaction()

trans = Transaction(doc,'Purge')
trans.Start()

if bool and purgeable_element_ids != None:
	doc.Delete(purgeable_element_ids)

#Materials to delete
toKeep=[]
elements = FilteredElementCollector(doc).WhereElementIsElementType().ToElements()
for elem in elements:
	matlist=[]
	for matid in elem.GetMaterialIds(False):
		matlist.append(matid)
	toKeep.extend(matlist)

compound=FilteredElementCollector(doc).OfClass(HostObjAttributes).WhereElementIsElementType().ToElements()
compoundStructure = [comp.GetCompoundStructure() for comp in compound]
for compoundStr in compoundStructure:
	if compoundStr != None:
		layerCount=compoundStr.LayerCount
		for j in range (0, layerCount):
			if compoundStr.GetMaterialId(j) != ElementId(-1):
				toKeep.append(compoundStr.GetMaterialId(j))

materials = FilteredElementCollector(doc).OfClass(Material).ToElementIds()

#Assets to delete
appearanceAssetIds = FilteredElementCollector(doc).OfClass(AppearanceAssetElement).ToElementIds()
#The PropertySetElement contains either the Thermal or Structural Asset
propertySet = FilteredElementCollector(doc).OfClass(PropertySetElement).ToElementIds()

thermal=[doc.GetElement(id).ThermalAssetId for id in set(toKeep)]
structural=[doc.GetElement(id).StructuralAssetId for id in set(toKeep)]
appearanceAssets=[doc.GetElement(id).AppearanceAssetId for id in set(toKeep)]

propertySet2 = [e for e in propertySet if e not in thermal and e not in structural]
appearanceAssetIds2 = [e for e in appearanceAssetIds if e not in appearanceAssets]
matToDelete=[m for m in materials if not m in toKeep]

views = FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements()

#Unused filters to delete
filterIds = FilteredElementCollector(doc).OfClass(ParameterFilterElement).ToElementIds()

usedFilterIds = []
if not doc.IsFamilyDocument:
	for view in views:
		viewFilterIds = []
		if view.AreGraphicsOverridesAllowed():
			viewFilterIds = view.GetFilters()
		usedFilterIds.extend(viewFilterIds)
unusedFilterIds = [u for u in filterIds if u not in usedFilterIds]
	
#Unused view Templates to delete
appliedtemplates = [v.ViewTemplateId for v in views]
templates = [v.Id for v in views if v.IsTemplate == True]
templatesToDelete = [t for t in templates if t not in appliedtemplates]

#LinePatterns to delete
linePatterns = FilteredElementCollector(doc).OfClass(LinePatternElement).ToElements()
linesToDelete = [l.Id for l in linePatterns if l.Name.startswith("IMPORT")]

#"Imports in Families" Object Styles to delete
ImportCat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_ImportObjectStyles)
importsInFamily=[c.Id for c in ImportCat.SubCategories]

if bool and matToDelete != None:
	[doc.Delete(m) for m in matToDelete]
if bool and appearanceAssetIds2 != None:
	for a in appearanceAssetIds2:
		try:doc.Delete(a)
		except:pass
if bool and propertySet2 != None:
	[doc.Delete(p) for p in propertySet2]
if deeply and unusedFilterIds != None:
	[doc.Delete(u) for u in unusedFilterIds]
if deeply and templatesToDelete != None:
	[doc.Delete(t) for t in templatesToDelete]
if deeply and linesToDelete != None:
	[doc.Delete(l) for l in linesToDelete]
if deeply and importsInFamily != None:
	[doc.Delete(i) for i in importsInFamily]

trans.Commit()

if purgeable_element_ids != None:
	OUT= '%d elements deleted' %(len([purg for purg in purgeable_element_ids]+matToDelete+appearanceAssetIds2+propertySet2+[f for f in unusedFilterIds]+templatesToDelete+linesToDelete+importsInFamily)),[purg for purg in purgeable_element_ids]+matToDelete+appearanceAssetIds2+propertySet2+[f for f in unusedFilterIds]+templatesToDelete+linesToDelete+importsInFamily
else:
	OUT= '%d elements deleted' %(len(matToDelete+appearanceAssetIds2+propertySet2+[f for f in unusedFilterIds]+templatesToDelete+linesToDelete+importsInFamily)),matToDelete+appearanceAssetIds2+propertySet2+[f for f in unusedFilterIds]+templatesToDelete+linesToDelete+importsInFamily