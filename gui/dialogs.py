from tkinter import ttk,Text,Toplevel,Listbox
import tkinter.font as font
import tkinter
from enum import Enum
from multiprocessing import Process

class DialogResultType(Enum):
    DIALOG_RESULT_ABORT = -1
    DIALOG_RESULT_OK = 0


class DiaglogResult():
    def __init__(self,result:DialogResultType) -> None:
        self.__result = result

    def GetResult(self):
        return self.__result

class KeyboardResult(DiaglogResult):
    def __init__(self, result: DialogResultType,text:str) -> None:
        super().__init__(result)
        self.__text = text

    def GetKeyboardText(self):
        return self.__text 

class SelectorResult(DiaglogResult):
    def __init__(self, result: DialogResultType,selectedIndex:int) -> None:
        super().__init__(result)
        self.__selectedIndex = selectedIndex

    def GetSelectedIndex(self):
        return self.__selectedIndex


class ListSelector(Toplevel):
    def __init__(self,master=None,resultCallback=None,borderless:bool=False,listToSelect:list=None,selectedIndex:int=-1) -> None:
        super().__init__(master = master)
        self.__resultCallback = resultCallback
        self.__result = DialogResultType.DIALOG_RESULT_OK
        if borderless:
            self.overrideredirect(True)
        self.__SetupBaseFrame()
        self.__SetupComponents(listToSelect,selectedIndex)

    def ShowDialog(self):
        self.mainloop()

    def __SetupBaseFrame(self):
        self.title('List Selector')
        self.geometry('1024x600')
        self.minsize(1024, 600)
        self.maxsize(1024, 600)
        self.attributes("-topmost", True)
        self.configure(bg='gray')
        self.protocol("WM_DELETE_WINDOW",self.__EventFormClosing)

    def __SetupComponents(self,listToSelect,selectedIndex):
        listFont = font.Font(family='Times', size=32, weight='bold')

        self.rowconfigure(0,minsize=25)
        self.rowconfigure(3,minsize=25)

        items = []
        if listToSelect != None:
            for option in listToSelect:
                items.append(option['coffee_name'])
        orderList = tkinter.Variable(value=tuple(items))
        self.__currentOrderIndexMin = 6
        self.__currentOrderIndex = self.__currentOrderIndexMin
        self.__currentOrderIndexMax = len(items) - 1
        self.__orderSelection = Listbox(self,listvariable=orderList,height=self.__currentOrderIndexMin + 1,selectmode=tkinter.SINGLE, font=listFont,width=39)
        self.__orderSelection.grid(row=1,column=0,rowspan=2,columnspan=2, ipady=60, ipadx=50)

        if selectedIndex >= 0:
            self.__orderSelection.selection_set(selectedIndex,selectedIndex)

        style = ttk.Style()
        style.configure('listselector.dialog.TButton', font=('Times', 42))

        self.__btUp = ttk.Button(self, text="▲",style="listselector.dialog.TButton",width=1)
        self.__btUp .grid(row=1,column=2, ipady=73, ipadx=28)
        self.__btUp.bind("<Button-1>",self.__EventUpList)
        self.__btDown = ttk.Button(self, text="▼",style="listselector.dialog.TButton",width=1)
        self.__btDown.grid(row=2,column=2, ipady=73, ipadx=28)
        self.__btDown.bind("<Button-1>",self.__EventDownList)

        abortBt = ttk.Button(self, text='X', style='listselector.dialog.TButton',width=1)
        abortBt.grid(row=4,column=1, ipady=28, ipadx=28)
        abortBt.bind("<Button-1>",self.__EventAbortPressed)
        okBt = ttk.Button(self, text='OK', style='listselector.dialog.TButton',width=1)
        okBt.grid(row=4,column=2, ipady=28, ipadx=28)
        okBt.bind("<Button-1>",self.__EventOkPressed)

    def __EventUpList(self,sender):
        self.__currentOrderIndex = self.__currentOrderIndex -1
        if self.__currentOrderIndex <= self.__currentOrderIndexMin:
            self.__currentOrderIndex = 0
        self.__orderSelection.yview_moveto(self.__currentOrderIndex)

    def __EventDownList(self,sender):
        if self.__currentOrderIndex < self.__currentOrderIndexMin:
            self.__currentOrderIndex = self.__currentOrderIndexMin
        self.__currentOrderIndex = self.__currentOrderIndex + 1
        if self.__currentOrderIndex > self.__currentOrderIndexMax:
            self.__currentOrderIndex = self.__currentOrderIndexMax
        self.__orderSelection.yview_moveto(self.__currentOrderIndex)

    def __EventOkPressed(self,sender):
        self.__result = DialogResultType.DIALOG_RESULT_OK
        self.__EventFormClosing()

    def __EventAbortPressed(self,sender):
        self.__result = DialogResultType.DIALOG_RESULT_ABORT
        self.__EventFormClosing()

    def __EventFormClosing(self):
        if self.__resultCallback != None:
            index = -1
            if self.__result == DialogResultType.DIALOG_RESULT_OK:
                index = self.__orderSelection.curselection()[0]
            arg = SelectorResult(self.__result,index)
            self.__resultCallback(arg)
        self.destroy()

class VirtualKeyboard(Toplevel):
    def __init__(self,master=None,resultCallback=None,borderless:bool=False,initalText:str='') -> None:
        super().__init__(master = master)

        self.__keyBoardLayoutNormal = [['1','2','3','4','5','6','7','8','9','0','ß'],
                                       ['q','w','e','r','t','z','u','i','o','p','ü'],
                                       ['a','s','d','f','g','h','j','k','l','ö','ä'],
                                       ['y','x','c','v','b','n','m']]

        self.__keyBoardLayoutShiftPressed = [['1','2','3','4','5','6','7','8','9','0','ß'],
                                       ['Q','W','E','R','T','Z','U','I','O','P','Ü'],
                                       ['A','S','D','F','G','H','J','K','L','Ö','Ä'],
                                       ['Y','X','C','V','B','N','M']]

        self.__resultCallback = resultCallback
        self.__result = DialogResultType.DIALOG_RESULT_OK
        self.__shiftState = False
        self.__keyboardContent = initalText
        if borderless:
            self.overrideredirect(True)
        self.__SetupBaseFrame()
        self.__SetupKeyBoard()

    def __SetupBaseFrame(self):
        self.title('VirtualKeyboard DE')
        self.geometry('1024x600')
        self.minsize(1024, 600)
        self.maxsize(1024, 600)
        self.attributes("-topmost", True)
        self.configure(bg='gray')
        self.protocol("WM_DELETE_WINDOW",self.__EventFormClosing)

    def __SetupKeyBoard(self,shiftPressed:bool=False):
        for widget in self.winfo_children():
            widget.destroy()

        layout = self.__keyBoardLayoutNormal
        if shiftPressed:
            layout = self.__keyBoardLayoutShiftPressed

        self.rowconfigure(0,minsize=5)
        self.rowconfigure(2,minsize=5)

        style = ttk.Style()
        btFont = font.Font(family='Times', size=32, weight='bold')
        style.configure('keyboard.TButton', font=btFont)

        rowCounter = 3
        self.__keys = []
        for keyboardRow in layout:
            columnCounter =0
            for symbol in keyboardRow:
                keybt = ttk.Button(self, text=symbol, style='keyboard.TButton',width=1)
                keybt.grid(row=rowCounter,column=columnCounter, ipady=25, ipadx=25)
                keybt.bind("<Button-1>",self.__EventButtonPressed)
                self.__keys.append(keybt)
                columnCounter = columnCounter + 1
            rowCounter = rowCounter + 1

        shiftBt = ttk.Button(self, text='⇫', style='keyboard.TButton',width=1)
        shiftBt.grid(row=rowCounter,column=0, ipady=25, ipadx=25)
        shiftBt.bind("<Button-1>",self.__EventShiftPressed)

        spaceBt = ttk.Button(self, text='', style='keyboard.TButton',width=1)
        spaceBt.grid(row=rowCounter,column=1, ipady=25, ipadx=118,columnspan=5)
        spaceBt.bind("<Button-1>",self.__EventButtonPressed)

        backBt = ttk.Button(self, text='⌫', style='keyboard.TButton',width=1)
        backBt.grid(row=rowCounter,column=6, ipady=25, ipadx=25)
        backBt.bind("<Button-1>",self.__EventBackButtonPressed)

        abortBt = ttk.Button(self, text='X', style='keyboard.TButton',width=1)
        abortBt.grid(row=rowCounter,column=9, ipady=25, ipadx=25)
        abortBt.bind("<Button-1>",self.__EventAbortPressed)
        okBt = ttk.Button(self, text='OK', style='keyboard.TButton',width=1)
        okBt.grid(row=rowCounter,column=10, ipady=25, ipadx=25)
        okBt.bind("<Button-1>",self.__EventOkPressed)

        self.__textOutput = Text(self, state=tkinter.DISABLED,height=1,width=32,font=('Times', 48))
        self.__textOutput.grid(row=1,column=0,columnspan=12)
        self.__textOutput.configure(state=tkinter.NORMAL)
        self.__textOutput.delete(1.0, tkinter.END)
        self.__textOutput.insert('1.0',self.__keyboardContent)
        self.__textOutput.configure(state=tkinter.DISABLED)

                
    def ShowDialog(self):
        self.mainloop()

    def __EventButtonPressed(self,sender):
        if len(self.__keyboardContent) >= 20:
            return
        symbol = sender.widget.cget('text')
        if not symbol:
            symbol = ' '
        self.__keyboardContent = self.__keyboardContent + symbol
        self.__textOutput.configure(state=tkinter.NORMAL)
        self.__textOutput.delete(1.0, tkinter.END)
        self.__textOutput.insert('1.0',self.__keyboardContent)
        self.__textOutput.configure(state=tkinter.DISABLED)

    def __EventBackButtonPressed(self,sender):
        if len(self.__keyboardContent) == 0:
            return
        self.__keyboardContent = self.__keyboardContent[:-1]
        self.__textOutput.configure(state=tkinter.NORMAL)
        self.__textOutput.delete(1.0, tkinter.END)
        self.__textOutput.insert('1.0',self.__keyboardContent)
        self.__textOutput.configure(state=tkinter.DISABLED)

    def __EventShiftPressed(self,sender):
        self.__shiftState = not self.__shiftState
        self.__SetupKeyBoard(self.__shiftState)

    def __EventOkPressed(self,sender):
        self.__result = DialogResultType.DIALOG_RESULT_OK
        self.__keyboardContent = self.__keyboardContent.strip()
        self.__EventFormClosing()

    def __EventAbortPressed(self,sender):
        self.__result = DialogResultType.DIALOG_RESULT_ABORT
        self.__keyboardContent = ''
        self.__EventFormClosing()

    def __EventFormClosing(self):
        if self.__resultCallback != None:
            arg = KeyboardResult(self.__result,self.__keyboardContent)
            self.__resultCallback(arg)
        self.destroy()

class VirtualNumpad(Toplevel):
    def __init__(self,master=None,resultCallback=None,borderless:bool=False,initalText:str='') -> None:
        super().__init__(master = master)

        self.__resultCallback = resultCallback
        self.__result = DialogResultType.DIALOG_RESULT_OK
        try:    
            self.__keyboardContent = str(float(initalText))
        except:
            self.__keyboardContent = ''
        if borderless:
            self.overrideredirect(True)
        self.__SetupBaseFrame()
        self.__SetupKeyBoard()

    def __SetupKeyBoard(self,shiftPressed:bool=False):
        layout = [['7','8','9'],
                  ['4','5','6'],
                  ['1','2','3'],
                  ['0','.']]


        self.rowconfigure(0,minsize=5)
        self.rowconfigure(2,minsize=5)

        style = ttk.Style()
        btFont = font.Font(family='Times', size=32, weight='bold')
        style.configure('keyboard.TButton', font=btFont)

        rowCounter = 3
        self.__keys = []
        for keyboardRow in layout:
            columnCounter =1
            for symbol in keyboardRow:
                keybt = ttk.Button(self, text=symbol, style='keyboard.TButton',width=1)
                keybt.grid(row=rowCounter,column=columnCounter, ipady=25, ipadx=50)
                keybt.bind("<Button-1>",self.__EventButtonPressed)
                self.__keys.append(keybt)
                columnCounter = columnCounter + 1
            rowCounter = rowCounter + 1

        backBt = ttk.Button(self, text='⌫', style='keyboard.TButton',width=1)
        backBt.grid(row=rowCounter,column=1, ipady=25, ipadx=50)
        backBt.bind("<Button-1>",self.__EventBackButtonPressed)

        abortBt = ttk.Button(self, text='X', style='keyboard.TButton',width=1)
        abortBt.grid(row=rowCounter,column=3, ipady=25, ipadx=50)
        abortBt.bind("<Button-1>",self.__EventAbortPressed)
        okBt = ttk.Button(self, text='OK', style='keyboard.TButton',width=1)
        okBt.grid(row=rowCounter,column=4, ipady=25, ipadx=50)
        okBt.bind("<Button-1>",self.__EventOkPressed)

        self.__textOutput = Text(self, state=tkinter.DISABLED,height=1,width=32,font=('Times', 48))
        self.__textOutput.grid(row=1,column=0,columnspan=5)
        self.__textOutput.configure(state=tkinter.NORMAL)
        self.__textOutput.delete(1.0, tkinter.END)
        self.__textOutput.insert('1.0',self.__keyboardContent)
        self.__textOutput.configure(state=tkinter.DISABLED)

                
    def ShowDialog(self):
        self.mainloop()

    def __SetupBaseFrame(self):
        self.title('Virtual Numpad')
        self.geometry('1024x600')
        self.minsize(1024, 600)
        self.maxsize(1024, 600)
        self.attributes("-topmost", True)
        self.configure(bg='gray')
        self.protocol("WM_DELETE_WINDOW",self.__EventFormClosing)


    def __EventFormClosing(self):
        if self.__resultCallback != None:
            try:    
                val = str(float(self.__keyboardContent))
            except:
                val = '0.0'
            arg = KeyboardResult(self.__result,val)
            self.__resultCallback(arg)
        self.destroy()

    def __EventButtonPressed(self,sender):
        if len(self.__keyboardContent) >= 20:
            return
        symbol = sender.widget.cget('text')
        if symbol == '.' and ('.' in self.__keyboardContent or not self.__keyboardContent):
            return 
        self.__keyboardContent = self.__keyboardContent + symbol
        self.__textOutput.configure(state=tkinter.NORMAL)
        self.__textOutput.delete(1.0, tkinter.END)
        self.__textOutput.insert('1.0',self.__keyboardContent)
        self.__textOutput.configure(state=tkinter.DISABLED)

    def __EventBackButtonPressed(self,sender):
        if len(self.__keyboardContent) == 0:
            return
        self.__keyboardContent = self.__keyboardContent[:-1]
        self.__textOutput.configure(state=tkinter.NORMAL)
        self.__textOutput.delete(1.0, tkinter.END)
        self.__textOutput.insert('1.0',self.__keyboardContent)
        self.__textOutput.configure(state=tkinter.DISABLED)

    def __EventOkPressed(self,sender):
        self.__result = DialogResultType.DIALOG_RESULT_OK
        self.__keyboardContent = self.__keyboardContent.strip()
        self.__EventFormClosing()

    def __EventAbortPressed(self,sender):
        self.__result = DialogResultType.DIALOG_RESULT_ABORT
        self.__keyboardContent = ''
        self.__EventFormClosing()