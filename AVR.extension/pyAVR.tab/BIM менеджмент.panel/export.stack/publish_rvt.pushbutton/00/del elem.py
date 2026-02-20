#Copyright(c) 2016, Dimitar Venkov
# @5devene, dimitar.ven@gmail.com

import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import*
clr.AddReference('RevitServices')
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

def tolist(obj1):
	if hasattr(obj1,"__iter__"): return obj1
	else: return [obj1]

elems = UnwrapElement(tolist(IN[0]))
inputdocs = tolist(UnwrapElement(IN[1]))

#Part of script from Clockwork
for inputdoc in inputdocs :
	if inputdoc == None:
		doc = DocumentManager.Instance.CurrentDBDocument
	elif inputdoc.GetType().ToString() == "Autodesk.Revit.DB.Document":
		doc = inputdoc
	elif inputdoc.GetType().ToString() == "Autodesk.Revit.DB.RevitLinkInstance":
		doc = inputdoc.GetLinkDocument()
	else: doc = None
	
	TransactionManager.Instance.ForceCloseTransaction()
	t = Transaction(doc,'delete')
	t.Start()

deleted, failed = [], []

for e in elems:
	id = None
	try:
		id = e.Id
		del_id = doc.Delete(id)
		deleted.extend([d.ToString() for d in del_id])
	except:
		if id is not None:
			failed.append(id.ToString() )
s = set(deleted)
failed1 = [x for x in failed if x not in s]
t.Commit()
OUT = len(deleted), ';'.join(deleted), ';'.join(failed1)