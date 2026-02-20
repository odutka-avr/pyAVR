#node inspired by Thomas Mahon @Thomas__Mahon info@bimorph.co.uk Package: bimorphNodes
#Modification by Alban de Chasteigner, 2018

import clr
# Import RevitAPI
clr.AddReference("RevitAPI")
import Autodesk
from Autodesk.Revit.DB import *

# Import DocumentManager and TransactionManager
clr.AddReference("RevitServices")
import RevitServices
from RevitServices.Persistence import DocumentManager
#
def convertToList(listToConvert):
	if isinstance(listToConvert, list):
		return listToConvert
	else:
		return [listToConvert]

def rvtAuditReporter(doc):
	rvtLinkCollector = FilteredElementCollector(doc).OfClass(RevitLinkInstance)
	rvtLinks = rvtLinkCollector.ToElements()
	
	RVTLinkList = []
	RVTLinkTypeList = []
	report = []
	for rvt in rvtLinks:
		name = rvt.get_Parameter(BuiltInParameter.RVT_LINK_INSTANCE_NAME).AsString()
		linkname = Element.Name.GetValue(rvt)
		linkPinned = rvt.Pinned
		workSetId = rvt.WorksetId
		worksetNames = None
		wSetsInDoc = FilteredWorksetCollector( doc )
		for w in wSetsInDoc:
			if w.Id == workSetId:
				worksetNames = "Workset : " + w.Name
		
		try:
			rvtType = doc.GetElement( rvt.GetTypeId() )
			rvtExternalRef = rvtType.GetExternalFileReference()
			path = ModelPathUtils.ConvertModelPathToUserVisiblePath( rvtExternalRef.GetAbsolutePath() )
		
		except:
			path = "Path : N/A"
		
		rvtType = doc.GetElement( rvt.GetTypeId() )
		exRef = rvtType.GetExternalFileReference()
		linkStatus = exRef.GetLinkedFileStatus()
		
		RVTLinkList.Add( rvt)
		RVTLinkTypeList.Add(rvtType)		
		report.Add( [rvt, "Assigned Name : "+name,linkname,  "Status : "+ str(linkStatus),"Pinned : " + str(linkPinned), worksetNames, path] )
			
	return report, RVTLinkList,RVTLinkTypeList

uiapp = DocumentManager.Instance.CurrentUIApplication
app = uiapp.Application

processDocument = None
if IN[0] == "" or IN[0] == []:
	doc = DocumentManager.Instance.CurrentDBDocument
	processDocument = rvtAuditReporter(doc)
	report = processDocument[0]
	RVTLinkList = processDocument[1]
	RVTLinkTypeList=processDocument[2]
	if report == []:
		OUT = ["No Linked Revit files found"], [], []
	else:
		OUT = report, RVTLinkList,  RVTLinkTypeList
		
else:
	report = []
	RVTLinkList = []
	RVTLinkTypeList = []
	filePath = convertToList(IN[0])
	for path in filePath:
		doc = app.OpenDocumentFile(FilePath( str(path) ), OpenOptions())
		processDocument = rvtAuditReporter(doc) 
		report.Add(processDocument[0])
		RVTLinkList.Add(processDocument[1])
		RVTLinkTypeList.Add(processDocument[2])

	if report == []:
		OUT = ["No Linked Revit files found"], [], []
	else:
		OUT = report[0], [[]] if RVTLinkList == [] else RVTLinkList[0], [[]] if RVTLinkTypeList == [] else RVTLinkTypeList[0]