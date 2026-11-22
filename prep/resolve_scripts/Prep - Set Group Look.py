import importlib, prep_tree
importlib.reload(prep_tree)
looks = prep_tree.looks_available()
ui = fusion.UIManager; disp = bmd.UIDispatcher(ui)
win = disp.AddWindow({"ID": "PrepLook", "WindowTitle": "Prep - Set Group Look", "Geometry": [400, 300, 360, 120]}, [
    ui.VGroup([ui.ComboBox({"ID": "look"}), ui.HGroup([ui.Button({"ID": "ok", "Text": "Put on every group"}), ui.Button({"ID": "no", "Text": "Cancel"})])])])
for l in looks:
    win.Find("look").AddItem(l)
pick = {}
def ok(ev):
    pick["look"] = win.Find("look").CurrentText; disp.ExitLoop()
def no(ev):
    disp.ExitLoop()
win.On.ok.Clicked = ok; win.On.no.Clicked = no; win.On.PrepLook.Close = no
win.Show(); disp.RunLoop(); win.Hide()
if pick.get("look"):
    prep_tree.main(["look", pick["look"]], resolve)
