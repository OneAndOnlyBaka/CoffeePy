from tkinter import *
from tkinter import ttk
import tkinter
from PIL import ImageTk, Image
from enum import Enum
from threading import Lock
from threading import Timer
from lib.nfc import CardScanner
from lib.database import DatabaseConnector

from gui.dialogs import VirtualKeyboard, ListSelector, VirtualNumpad, KeyboardResult, SelectorResult, DialogResultType

class MenuSelectionType(Enum):
    MENU_ORDER = 0
    MENU_INFO_AND_PAY = 1
    MENU_SETTINGS = 2
    MENU_RANKING = 3

class GuiConstants():
    @staticmethod
    def GetDefaultBackgroundColor() -> str:
        return 'grey'

    @staticmethod
    def GetFont() -> str:
        return 'Times'

    @staticmethod
    def GetHeaderFont():
        return (GuiConstants.GetFont(),40)

    @staticmethod
    def GetMessageFont():
        return (GuiConstants.GetFont(),24)

    @staticmethod
    def GetCoffeeSelectFont():
        return (GuiConstants.GetFont(),32)

    @staticmethod
    def GetMessageInfoTextFont():
        return (GuiConstants.GetFont(),16)

    @staticmethod
    def GetMessageFontCoutdown():
        return (GuiConstants.GetFont(),16)

class BaseSelectionPanel():
    def __init__(self,mainWindow,panel:PanedWindow):
        for widget in panel.winfo_children():
            widget.destroy()

        for i in range(8):
            panel.columnconfigure(i,minsize=0)
            panel.rowconfigure(i,minsize=0)

        self.mainWindow = mainWindow

    def UpdateSecond(self):
        pass

    def IsStillInUsage(self) -> bool:
        return False

    def FinalizeAction(self):
        pass
            
class RequiredCardPanel(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow):
        super().__init__(mainWindow,panel)
        panel.columnconfigure(0,minsize=30)
        self.__infoText = Label(panel, text="APPLY YOUR ID CARD",bg=GuiConstants.GetDefaultBackgroundColor(),font=GuiConstants.GetHeaderFont())
        self.__infoText.grid(row=0,column=1)
        self.__infoText2 = Label(panel, text="SCANNER IS ON RIGHT SIDE",bg=GuiConstants.GetDefaultBackgroundColor(),font=GuiConstants.GetHeaderFont())
        self.__infoText2.grid(row=3,column=1)
        self.__img = ImageTk.PhotoImage(Image.open("gui/res/Industry-Rfid-Signal-icon.png"))
        self.__panel = Label(panel, image = self.__img,bg=GuiConstants.GetDefaultBackgroundColor())
        self.__panel.grid(row=1,column=1,rowspan=2,columnspan=2)

class OrderPanelResult():
    def __init__(self,uid:int,coffeeId:int) -> None:
        self.__uid = uid
        self.__coffeeId = coffeeId

    def GetUID(self):
        return self.__uid

    def GetCoffeeOrderID(self):
        return self.__coffeeId

class OrderPanel(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow, uid:int, username:str, favouriteSelection:int, coffeeOptions:list, resultEvent=None):
        super().__init__(mainWindow,panel)

        self.__uid = uid
        self.__coffeeOptions = coffeeOptions
        self.__favouriteSelection = favouriteSelection
        self.__resultEvent = resultEvent
        self.__maxSecsForOutCheckout = 20
        self.__isAutoCheckoutEnabled = True
        panel.columnconfigure(0,minsize=300)
        panel.rowconfigure(0,minsize=20)
        panel.rowconfigure(2,minsize=20)

        fav = list(filter(lambda x : x['coffee_id'] == favouriteSelection,coffeeOptions))[0]
        
        welcomeText = "Hi " + username + "! \"" + fav['coffee_name'] +"\"?\nOr something different?"

        self.__infoText = Label(panel, text=welcomeText,bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetCoffeeSelectFont(),borderwidth=2,relief='ridge')
        self.__infoText.grid(row=1,column=0,columnspan=3,sticky='w')

        items = []
        for option in coffeeOptions:
            items.append(option['coffee_name'] + ' ({:.2f})'.format(option['coffee_price']))
        orderList = tkinter.Variable(value=tuple(items))
        self.__currentOrderIndexMin = 6
        self.__currentOrderIndex = self.__currentOrderIndexMin
        self.__currentOrderIndexMax = len(items) - 1
        self.__orderSelection = Listbox(panel,listvariable=orderList,height=self.__currentOrderIndexMin + 1,selectmode=tkinter.SINGLE, font=GuiConstants.GetCoffeeSelectFont(),width=34)
        self.__orderSelection.grid(row=3,column=0,rowspan=2,columnspan=2)
        self.__orderSelection.bind("<Button-1>",self.__EventChangedOrder)

        self.__checkoutInfo= Label(panel, text="Automatic Checkout in "+str(self.__maxSecsForOutCheckout)+"s",bg=GuiConstants.GetDefaultBackgroundColor(), font=GuiConstants.GetMessageFontCoutdown(), fg='orange')
        self.__checkoutInfo.grid(row=5,column=0,columnspan=3,sticky='w')

        style = ttk.Style()
        style.configure('order.TButton', font=('Times', 24))

        self.__btUp = ttk.Button(panel, text="▲",style="order.TButton", width=1)
        self.__btUp .grid(row=3,column=2, ipady=57, ipadx=35)
        self.__btUp.bind("<Button-1>",self.__EventUpList)
        self.__btDown = ttk.Button(panel, text="▼",style="order.TButton", width=1)
        self.__btDown.grid(row=4,column=2, ipady=57, ipadx=35)
        self.__btDown.bind("<Button-1>",self.__EventDownList)

        self.__btCancel = ttk.Button(panel, text="OK",style="order.TButton", width=1)
        self.__btCancel .grid(row=5,column=2, ipady=35, ipadx=35)
        self.__btCancel.bind("<Button-1>",self.__EventOkOrder)

        self.__btCancel = ttk.Button(panel, text="X",style="order.TButton", width=1)
        self.__btCancel .grid(row=5,column=1, ipady=35, ipadx=35)
        self.__btCancel.bind("<Button-1>",self.__EventCancelOrder)

        self.__timeoutCounter = 0 

    def UpdateSecond(self):
        if self.__isAutoCheckoutEnabled:
            self.__timeoutCounter  = self.__timeoutCounter + 1
            self.__checkoutInfo.configure(text="Automatic Checkout in "+ str(self.__maxSecsForOutCheckout - self.__timeoutCounter ) +"s")

    def IsStillInUsage(self) -> bool:
        usage = self.__timeoutCounter < self.__maxSecsForOutCheckout

        if self.__isAutoCheckoutEnabled and not usage:
            self.__NotifyOrder()

        return usage

    def __EventCancelOrder(self,sender):
        self.__DisableAutoCheckout()
        self.__timeoutCounter = self.__maxSecsForOutCheckout

    def __EventUpList(self,sender):
        self.__DisableAutoCheckout()
        self.__currentOrderIndex = self.__currentOrderIndex -1
        if self.__currentOrderIndex <= self.__currentOrderIndexMin:
            self.__currentOrderIndex = 0
        self.__orderSelection.yview_moveto(self.__currentOrderIndex)

    def __EventDownList(self,sender):
        self.__DisableAutoCheckout()
        if self.__currentOrderIndex < self.__currentOrderIndexMin:
            self.__currentOrderIndex = self.__currentOrderIndexMin
        self.__currentOrderIndex = self.__currentOrderIndex + 1
        if self.__currentOrderIndex > self.__currentOrderIndexMax:
            self.__currentOrderIndex = self.__currentOrderIndexMax
        self.__orderSelection.yview_moveto(self.__currentOrderIndex)
        
    def __DisableAutoCheckout(self):
        if self.__isAutoCheckoutEnabled:
            self.__isAutoCheckoutEnabled = False
            self.__checkoutInfo.configure(text="")

    def __NotifyOrder(self):
        if self.__resultEvent != None:
            coffeeId = self.__favouriteSelection
            if len(self.__orderSelection.curselection()) > 0:
                index = self.__orderSelection.curselection()[0]
                coffeeId = self.__coffeeOptions[index]['coffee_id']

            arg = OrderPanelResult(self.__uid,coffeeId)
            self.__resultEvent(arg)

    def __EventOkOrder(self,sender):
        self.__DisableAutoCheckout()
        self.__NotifyOrder()
        self.__timeoutCounter = self.__maxSecsForOutCheckout

    def __EventChangedOrder(self,sender):
        self.__DisableAutoCheckout()


class SettingsPanelResult():
    def __init__(self,uid:int,nick:str,favouriteUid:int) -> None:
        self.__uid = uid
        self.__nick = nick
        self.__favouriteUid = favouriteUid

    def GetUID(self):
        return self.__uid

    def GetNick(self):
        return self.__nick

    def GetFavouriteUID(self):
        return self.__favouriteUid

class SettingsPanel(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow, uid:int, nick:str, favouriteSelection:int, coffeeOptions:list, resultEvent=None):
        super().__init__(mainWindow,panel)

        panel.rowconfigure(1,minsize=40)

        self.__autoLogoutSecoundsMax = 20
        self.__autoLogoutCounter = 0
        self.__keyboardOpen = False
        self.__running = True
        self.__uid = uid
        self.__favourite = list(filter(lambda x : x['coffee_id'] == favouriteSelection,coffeeOptions))[0]
        self.__coffeeOptions = coffeeOptions
        if nick:
            self.__nick = nick
        else:
            self.__nick = str(uid)
        self.__resultEvent = resultEvent

        welcomeText = 'Hello ' + self.__nick + '!\nHere you can change your personal preferences'
        self.__welcomeLabel = Label(panel, text=welcomeText,bg='#663300' ,fg='white' , justify=LEFT, font=(GuiConstants.GetFont(),28), borderwidth=2,relief='ridge',width=1)
        self.__welcomeLabel.grid(row=0,column=0,columnspan=4,sticky='w',ipady=10,ipadx=395)
        
        style = ttk.Style()
        style.configure('settings.small.TButton', font=('Times', 18))

        titelUidStr = StringVar()
        titelUidStr.set('UID')
        titleUid = Entry(panel, textvariable=titelUidStr,bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFont(),state=DISABLED,disabledbackground="#663300",disabledforeground='white',width=1)
        titleUid.grid(row=2,column=0,sticky='w', ipady=30, ipadx=80)
        uidVar = StringVar()
        uidVar.set(str(uid))
        uidValView = Entry(panel, textvariable=uidVar,bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFont(),state=DISABLED,disabledbackground="#202020",disabledforeground='white',width=1)
        uidValView.grid(row=2,column=1,columnspan=2,sticky='w',ipady=30,ipadx=230)

        titelUserStr = StringVar()
        titelUserStr.set('Nickname')
        titleUser = Entry(panel, textvariable=titelUserStr,bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFont(),state=DISABLED,disabledbackground="#663300",disabledforeground='white',width=1)
        titleUser.grid(row=3,column=0,sticky='w', ipady=30, ipadx=80)
        self.__tknick = StringVar()
        self.__tknick.set(self.__nick)
        self.__nicknameLabel = Entry(panel, textvariable=self.__tknick,bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFont(),state=DISABLED,disabledbackground="#202020",disabledforeground='white',width=1)
        self.__nicknameLabel.grid(row=3,column=1,columnspan=2,sticky='w',ipady=30,ipadx=230)
        self.__btEditNick = ttk.Button(panel, text="EDIT",style="settings.small.TButton")
        self.__btEditNick.grid(row=3,column=3, ipady=30, ipadx=20)
        self.__btEditNick.bind("<Button-1>",self.__EventEditUserNick)

        titleFavouriteStr = StringVar()
        titleFavouriteStr.set('Favourite')
        titleFavourite = Entry(panel, textvariable=titleFavouriteStr,bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFont(),state=DISABLED,disabledbackground="#663300",disabledforeground='white',width=1)
        titleFavourite.grid(row=4,column=0,sticky='w', ipady=30, ipadx=80)
        self.__tkFav = StringVar()
        self.__tkFav.set(self.__favourite['coffee_name'])
        self.__favLabel = Entry(panel, textvariable=self.__tkFav,bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFont(),state=DISABLED,disabledbackground="#202020",disabledforeground='white',width=1)
        self.__favLabel.grid(row=4,column=1,sticky='w',ipady=30,ipadx=230)
        self.__btEditFav = ttk.Button(panel, text="EDIT",style="settings.small.TButton")
        self.__btEditFav.grid(row=4,column=3, ipady=30, ipadx=20)
        self.__btEditFav.bind("<Button-1>",self.__EventEditFavourite)

        self.__btSave = ttk.Button(panel, text="SAVE",style="settings.small.TButton")
        self.__btSave.grid(row=6,column=3, ipady=30, ipadx=20)
        self.__btSave.bind("<Button-1>",self.__EventSaveButtonPressed)

        self.__autoLogoutInfo = Label(panel, text="Automatic logout in "+ str(self.__autoLogoutSecoundsMax - self.__autoLogoutCounter ) +"s",bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFontCoutdown(), fg='orange')
        self.__autoLogoutInfo.grid(row=6,column=0,columnspan=4,sticky='w')

    def UpdateSecond(self):
        if self.__keyboardOpen:
            return
        self.__autoLogoutCounter = self.__autoLogoutCounter + 1
        self.__autoLogoutInfo.configure(text="Automatic logout in "+ str(self.__autoLogoutSecoundsMax - self.__autoLogoutCounter ) +"s")
        if  self.__autoLogoutCounter >= self.__autoLogoutSecoundsMax:
            self.__running = False

    def IsStillInUsage(self) -> bool:
        return self.__running

    def __SubdialogInUsage(self):
        self.__keyboardOpen = True
        self.__autoLogoutCounter = 0
        self.__autoLogoutInfo.configure(text="Automatic logout in "+ str(self.__autoLogoutSecoundsMax - self.__autoLogoutCounter ) +"s")

    def __EventEditUserNick(self,event):
        self.__SubdialogInUsage()
        keyb = VirtualKeyboard(self.mainWindow,self.__EventDialogEditUserNick,True,self.__nick)
        keyb.ShowDialog()

    def __EventDialogEditUserNick(self,result:KeyboardResult):
        if result.GetResult() == DialogResultType.DIALOG_RESULT_OK:
            self.__tknick.set(result.GetKeyboardText())
            self.__nick = result.GetKeyboardText()
        self.__keyboardOpen = False

    def __EventEditFavourite(self,event):
        self.__SubdialogInUsage()
        currentIndex = self.__coffeeOptions.index(self.__favourite)
        selector = ListSelector(self.mainWindow,self.__EventDialogEditFavourite,True,self.__coffeeOptions,currentIndex)
        selector.ShowDialog()

    def __EventDialogEditFavourite(self,result:SelectorResult):
        if result.GetResult() == DialogResultType.DIALOG_RESULT_OK:
            self.__favourite = self.__coffeeOptions[result.GetSelectedIndex()]
            self.__tkFav.set(self.__favourite['coffee_name'])
        self.__keyboardOpen = False

    def __EventSaveButtonPressed(self,sender):
        if self.__resultEvent != None:
            arg = SettingsPanelResult(self.__uid,self.__nick,self.__favourite['coffee_id'])
            self.__resultEvent(arg)
        self.__running = False


class CardNotRecognizedPanel(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow):
        super().__init__(mainWindow,panel)
        panel.columnconfigure(0,minsize=30)
        self.__infoText = Label(panel, text="ERROR",bg=GuiConstants.GetDefaultBackgroundColor(),font=GuiConstants.GetHeaderFont(),fg='red')
        self.__infoText.grid(row=0,column=1)
        self.__infoText2 = Label(panel, text="CARD IS NOT RECOGNIZED",bg=GuiConstants.GetDefaultBackgroundColor(),font=GuiConstants.GetHeaderFont(),fg='red')
        self.__infoText2.grid(row=3,column=1)
        self.__img = ImageTk.PhotoImage(Image.open("gui/res/delete-icon.png"))
        self.__panel = Label(panel, image = self.__img,bg=GuiConstants.GetDefaultBackgroundColor())
        self.__panel.grid(row=1,column=1,rowspan=2,columnspan=2)

class NoCoffeeSortsInDatabase(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow):
        super().__init__(mainWindow,panel)
        panel.columnconfigure(0,minsize=30)
        self.__infoText = Label(panel, text="CONFIGURATION ERROR",bg=GuiConstants.GetDefaultBackgroundColor(),font=GuiConstants.GetHeaderFont(),fg='red')
        self.__infoText.grid(row=0,column=1)
        self.__infoText2 = Label(panel, text="MISSING COFFEE SORTS",bg=GuiConstants.GetDefaultBackgroundColor(),font=GuiConstants.GetHeaderFont(),fg='red')
        self.__infoText2.grid(row=3,column=1)
        self.__img = ImageTk.PhotoImage(Image.open("gui/res/delete-icon.png"))
        self.__panel = Label(panel, image = self.__img,bg=GuiConstants.GetDefaultBackgroundColor())
        self.__panel.grid(row=1,column=1,rowspan=2,columnspan=2)

class RankingPanel(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow,databaseConnector:DatabaseConnector.Connector):
        super().__init__(mainWindow,panel)
        self.__welcomeLabel = Label(panel, text='TOP 3 - ALL TIME',bg='#663300' ,fg='white' , justify=LEFT, font=(GuiConstants.GetFont(),28), borderwidth=2,relief='ridge',width=1)
        self.__welcomeLabel.grid(row=0,column=0,columnspan=4,sticky='w',ipady=10,ipadx=395)

        tab = Label(panel, text='POS',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=1,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='NAME',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=1,column=1,sticky='w',ipady=10,ipadx=248)
        tab = Label(panel, text='CCT',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=1,column=2,sticky='w',ipady=10,ipadx=50)
        tab = Label(panel, text='CCM',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=1,column=3,sticky='w',ipady=10,ipadx=50)
        tab = Label(panel, text='1',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=2,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='2',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=3,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='3',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=4,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='4',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)

        rankIdList = databaseConnector.GetUIDSortedByTotalCupsDecreasing()
        rowCounterMax = 3
        rowCounter = 0

        for id in rankIdList:
            meta = databaseConnector.GetUserMeta(id)
            ccm = databaseConnector.GetCurrentMonthCupsForUID(id)
            cct = databaseConnector.GetTotalCupsForUID(id)

            tab = Label(panel, text=meta['nick_name'],bg='SandyBrown' ,fg='black' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
            tab.grid(row=2 + rowCounter,column=1,sticky='w',ipady=10,ipadx=248)
            tab = Label(panel, text=str(cct),bg='SandyBrown' ,fg='black' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
            tab.grid(row=2 + rowCounter,column=2,sticky='w',ipady=10,ipadx=50)
            tab = Label(panel, text=str(ccm),bg='SandyBrown' ,fg='black' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
            tab.grid(row=2 + rowCounter,column=3,sticky='w',ipady=10,ipadx=50)
            rowCounter = rowCounter + 1
            if rowCounter >= rowCounterMax:
                break

        self.__welcomeLabel = Label(panel, text='TOP 3 - MONTH',bg='#663300' ,fg='white' , justify=LEFT, font=(GuiConstants.GetFont(),28), borderwidth=2,relief='ridge',width=1)
        self.__welcomeLabel.grid(row=5,column=0,columnspan=4,sticky='w',ipady=10,ipadx=395)

        tab = Label(panel, text='POS',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=6,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='NAME',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=6,column=1,sticky='w',ipady=10,ipadx=248)
        tab = Label(panel, text='CCT',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=6,column=2,sticky='w',ipady=10,ipadx=50)
        tab = Label(panel, text='CCM',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=6,column=3,sticky='w',ipady=10,ipadx=50)
        tab = Label(panel, text='1',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=7,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='2',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=8,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='3',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        tab.grid(row=9,column=0,sticky='w',ipady=10,ipadx=25)
        tab = Label(panel, text='4',bg='#000000' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)

        rankIdList = databaseConnector.GetUIDSortedByCurrentMonthsCupsDecreasing()
        rowCounterMax = 3
        rowCounter = 0

        for id in rankIdList:
            meta = databaseConnector.GetUserMeta(id)
            ccm = databaseConnector.GetCurrentMonthCupsForUID(id)
            cct = databaseConnector.GetTotalCupsForUID(id)

            tab = Label(panel, text=meta['nick_name'],bg='SandyBrown' ,fg='black' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
            tab.grid(row=7 + rowCounter,column=1,sticky='w',ipady=10,ipadx=248)
            tab = Label(panel, text=str(cct),bg='SandyBrown' ,fg='black' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
            tab.grid(row=7 + rowCounter,column=2,sticky='w',ipady=10,ipadx=50)
            tab = Label(panel, text=str(ccm),bg='SandyBrown' ,fg='black' , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
            tab.grid(row=7 + rowCounter,column=3,sticky='w',ipady=10,ipadx=50)
            rowCounter = rowCounter + 1
            if rowCounter >= rowCounterMax:
                break

        infoText = Label(panel, text='CC = Cups Count;T/M = Total/Month',bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        infoText.grid(row=10,column=0,columnspan=4,sticky='w',ipadx=395)


class UserInfoAndPayPanel(BaseSelectionPanel):
    def __init__(self, mainWindow, panel: PanedWindow,usermeta,databaseConnector:DatabaseConnector.Connector):
        super().__init__(mainWindow,panel)

        self.__autoLogoutSecoundsMax = 20
        self.__autoLogoutCounter = 0
        self.__keyboardOpen = False
        self.__running = True

        self.__databaseConnector = databaseConnector
        self.__usermeta = usermeta

        panel.rowconfigure(0,minsize=20)
        panel.rowconfigure(2,minsize=20)
        panel.rowconfigure(4,minsize=20)
        panel.rowconfigure(7,minsize=20)
        panel.rowconfigure(9,minsize=20)
        panel.rowconfigure(11,minsize=20)

        if usermeta['nick_name']:
            greetingsName = usermeta['nick_name']
        else:
            greetingsName = str(usermeta['uid'])

        welcomeText = 'Hello ' + greetingsName + '!\nHere your personal stats and payment area'

        infoText = Label(panel, text=welcomeText,bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetCoffeeSelectFont(),borderwidth=2,relief='ridge',width=1)
        infoText.grid(row=1,column=0,columnspan=4,sticky='w',ipadx=395)

        infoText = Label(panel, text='STATS',bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=1)
        infoText.grid(row=3,column=0,columnspan=4,sticky='w',ipadx=395)

        infoText = Label(panel, text='CCT',bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=8)
        infoText.grid(row=5,column=0,sticky='w')
        infoText = Label(panel, text=str(databaseConnector.GetTotalCupsForUID(usermeta['uid'])),bg='#202020' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=15)
        infoText.grid(row=5,column=1,sticky='w')

        infoText = Label(panel, text='CCM',bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=8)
        infoText.grid(row=6,column=0,sticky='w')
        infoText = Label(panel, text=databaseConnector.GetCurrentMonthCupsForUID(usermeta['uid']),bg='#202020' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=15)
        infoText.grid(row=6,column=1,sticky='w')

        infoText = Label(panel, text='ABT',bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=8)
        infoText.grid(row=5,column=2,sticky='w')
        self.__abtText = Label(panel, text= '{:.2f}€'.format(databaseConnector.GetAccountBalance(usermeta['uid'])),bg='#202020' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=15)
        self.__abtText.grid(row=5,column=3,sticky='w')

        infoText = Label(panel, text='CC = Cups Count;AB = Account Balance;T/M = Total/Month',bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageInfoTextFont(),borderwidth=2,relief='ridge',width=1)
        infoText.grid(row=8,column=0,columnspan=4,sticky='w',ipadx=395)
        
        infoText = Label(panel, text='Payment',bg='#663300' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=1)
        infoText.grid(row=10,column=0,columnspan=4,sticky='w',ipadx=395)

        self.__paymentValue = databaseConnector.GetAccountBalance(usermeta['uid'])
        if self.__paymentValue > 0.0:
            self.__paymentValue = 0.0
        else:
            self.__paymentValue = self.__paymentValue * -1.0

        self.__paymentText = Label(panel, text= '{:.2f}€'.format(self.__paymentValue),bg='#202020' ,fg='white' , justify=LEFT, font=GuiConstants.GetMessageFont(),borderwidth=2,relief='ridge',width=1)
        self.__paymentText.grid(row=12,column=0,columnspan=2,sticky='w',ipady=20, ipadx=180)

        style = ttk.Style()
        style.configure('payment.small.TButton', font=('Times', 18))

        self.__btEditPayment = ttk.Button(panel, text="EDIT",style="payment.small.TButton")
        self.__btEditPayment.grid(row=12,column=2, rowspan=2, ipady=58, ipadx=30)
        self.__btEditPayment.bind("<Button-1>",self.__EventEditPayment)

        self.__btPayment = ttk.Button(panel, text="PAY",style="payment.small.TButton")
        self.__btPayment.grid(row=12,column=3,rowspan=2, ipady=58, ipadx=30)
        self.__btPayment.bind("<Button-1>",self.__EventPay)

        self.__autoLogoutInfo = Label(panel, text="Automatic logout in "+ str(self.__autoLogoutSecoundsMax - self.__autoLogoutCounter ) +"s",bg=GuiConstants.GetDefaultBackgroundColor() , justify=LEFT, font=GuiConstants.GetMessageFontCoutdown(), fg='orange')
        self.__autoLogoutInfo.grid(row=13,column=0,columnspan=2,sticky='w',ipady=20)

    def __EventEditPayment(self,event):
        self.__SubdialogInUsage()
        numpad = VirtualNumpad(self.mainWindow,self.__EventDialogEditPayment,True,str(self.__paymentValue))
        numpad.ShowDialog()

    def __EventDialogEditPayment(self,result:KeyboardResult):
        if result.GetResult() == DialogResultType.DIALOG_RESULT_OK:
            self.__paymentValue = float(result.GetKeyboardText())
            self.__paymentText.configure(text='{:.2f}€'.format(self.__paymentValue))
        self.__keyboardOpen = False

    def __EventPay(self,event):
        self.__SubdialogInUsage()
        if self.__paymentValue > 0.0:
            self.__databaseConnector.AddPayment(self.__usermeta['uid'],self.__paymentValue)
            self.__abtText.configure(text='{:.2f}€'.format(self.__databaseConnector.GetAccountBalance(self.__usermeta['uid'])))
            self.__paymentValue = 0.0
            self.__paymentText.configure(text='{:.2f}€'.format(self.__paymentValue))
        self.__keyboardOpen = False


    def __SubdialogInUsage(self):
        self.__keyboardOpen = True
        self.__autoLogoutCounter = 0
        self.__autoLogoutInfo.configure(text="Automatic logout in "+ str(self.__autoLogoutSecoundsMax - self.__autoLogoutCounter ) +"s")

    def UpdateSecond(self):
        if self.__keyboardOpen:
            return
        self.__autoLogoutCounter = self.__autoLogoutCounter + 1
        self.__autoLogoutInfo.configure(text="Automatic logout in "+ str(self.__autoLogoutSecoundsMax - self.__autoLogoutCounter ) +"s")
        if  self.__autoLogoutCounter >= self.__autoLogoutSecoundsMax:
            self.__running = False

    def IsStillInUsage(self) -> bool:
        return self.__running

        

class CoffeePyMain():
    def __init__(self,databaseConnector:DatabaseConnector.Connector,borderless:bool=False) -> None:
        self.__currentMenuSelection = MenuSelectionType.MENU_ORDER
        self.__bgColor = 'grey'
        self.__tkRoot = None
        self.__mainMenuCursor = None
        self.__selectionPanal = None
        self.__innerCardPanel = None
        self.__logoutTimer = None
        self.__loggedIn = False
        self.__borderless = borderless
        self.__databaseConnector = databaseConnector
        self.__mutex = Lock()
        self.__SetupBase()

    def RunLoop(self):
        if self.__borderless:
            self.__tkRoot.overrideredirect(True)
        self.__tkRoot.mainloop()

    def __SetupBase(self):
        self.__tkRoot  = tkinter.Tk()
        self.__SetupBaseFrame()
        self.__GridSetup()
        self.__SetupNavigationBar()
        self.__SetupSelectionPanal()
        
    def __SetupSelectionPanal(self):
        self.__selectionPanal = PanedWindow(self.__tkRoot,bg=self.__bgColor)
        self.__selectionPanal.grid(row=0,column=3,rowspan=4,columnspan=3)
        self.__innerCardPanel = RequiredCardPanel(self.__tkRoot,self.__selectionPanal)
        
    def __SetupBaseFrame(self):
        self.__tkRoot.tk.call('lappend', 'auto_path', 'gui/awthemes-10.4.0')
        self.__tkRoot.tk.call('package', 'require', 'awdark')
        style = ttk.Style()
        style.theme_use("awdark")
        self.__tkRoot.title('CoffeePy')
        self.__tkRoot.geometry('1024x600')
        self.__tkRoot.minsize(1024, 600)
        self.__tkRoot.maxsize(1024, 600)
        self.__tkRoot.configure(bg=self.__bgColor)

    def __SetupNavigationBar(self):
        self.__InitIcons()

        self.__btOrder = ttk.Button(self.__tkRoot, image=self.__orderIco)
        self.__btOrder .grid(row=0,column=0,pady=0)
        self.__btOrder.bind("<Button-1>",self.__EventOrder)
        self.__btInfo = ttk.Button(self.__tkRoot, image=self.__infoIco)
        self.__btInfo.grid(row=1,column=0,pady=0)
        self.__btInfo.bind("<Button-1>",self.__EventInfo)
        self.__btSettings= ttk.Button(self.__tkRoot, image=self.__settingsIco)
        self.__btSettings.grid(row=2,column=0,pady=0)
        self.__btSettings.bind("<Button-1>",self.__EventSettings)
        self.__btRanking = ttk.Button(self.__tkRoot, image=self.__rankingIco)
        self.__btRanking.grid(row=3,column=0,pady=0)
        self.__btRanking.bind("<Button-1>",self.__EventRanking) 

        self.__mainMenuCursor = [] 
        self.__mainMenuCursor.append(Label(self.__tkRoot, image=self.__barIcoSelected,bg=self.__bgColor))
        self.__mainMenuCursor[0].grid(row=0,column=1)
        self.__mainMenuCursor.append(Label(self.__tkRoot, image=self.__barIco,bg=self.__bgColor))
        self.__mainMenuCursor[1].grid(row=1,column=1)
        self.__mainMenuCursor.append(Label(self.__tkRoot, image=self.__barIco,bg=self.__bgColor))
        self.__mainMenuCursor[2].grid(row=2,column=1)
        self.__mainMenuCursor.append(Label(self.__tkRoot, image=self.__barIco,bg=self.__bgColor))
        self.__mainMenuCursor[3].grid(row=3,column=1)
        
    def __InitIcons(self):
        width = 135
        height = width

        im = Image.open("gui/res/Coffee-icon.png")
        imResized = im.resize((width,height), Image.ANTIALIAS)
        self.__orderIco =  ImageTk.PhotoImage(imResized)
        medal = Image.open("gui/res/Badge-Trophy-02-icon.png")
        medal = medal.resize((width,height), Image.ANTIALIAS)
        self.__rankingIco =  ImageTk.PhotoImage(medal)
        info = Image.open("gui/res/Books-2-icon.png")
        info = info.resize((width,height), Image.ANTIALIAS)
        self.__infoIco =  ImageTk.PhotoImage(info)
        options = Image.open("gui/res/spur-gear-icon.png")
        options = options.resize((width,height), Image.ANTIALIAS)
        self.__settingsIco =  ImageTk.PhotoImage(options)
        bar = Image.open("gui/res/bar.png")
        bar = bar.resize((50,145), Image.ANTIALIAS)
        self.__barIco =  ImageTk.PhotoImage(bar)
        barsel = Image.open("gui/res/barselected.png")
        barsel = barsel.resize((50,145), Image.ANTIALIAS)
        self.__barIcoSelected =  ImageTk.PhotoImage(barsel)

    def __GridSetup(self):
        self.__tkRoot.rowconfigure(0,minsize=150)
        self.__tkRoot.rowconfigure(1,minsize=150)
        self.__tkRoot.rowconfigure(2,minsize=150)
        self.__tkRoot.rowconfigure(3,minsize=150)
        self.__tkRoot.columnconfigure(0,minsize=150)

    def EventNewCardApplied(self,sender):
        self.__mutex.acquire()
        coffeeSorts = self.__databaseConnector.GetCoffeeSorts()
        if coffeeSorts == None:
            self.__innerCardPanel = NoCoffeeSortsInDatabase(self.__tkRoot,self.__selectionPanal)
            self.__mutex.release()
            return
        if not self.__loggedIn:
            uid = CardScanner.UIDConverter.ToInt(sender.GetLastCardInformation()['uid'])
            
            userMeta = self.__databaseConnector.GetUserMeta(uid)
            if self.__currentMenuSelection == MenuSelectionType.MENU_ORDER or self.__currentMenuSelection == MenuSelectionType.MENU_RANKING:
                if userMeta != None:
                    self.__innerCardPanel = OrderPanel(self.__tkRoot,self.__selectionPanal,uid,userMeta['nick_name'],userMeta['favourite_coffee'],coffeeSorts,self.__EventPlaceOrder)
                else:
                    self.__innerCardPanel = SettingsPanel(self.__tkRoot,self.__selectionPanal,uid,'',coffeeSorts[0]['coffee_id'],coffeeSorts,self.__EventCreateNewUser)
                    self.__currentMenuSelection = MenuSelectionType.MENU_SETTINGS
                    self.__SetSelectionCursor(2)
            elif self.__currentMenuSelection == MenuSelectionType.MENU_SETTINGS:
                callback = self.__EventCreateNewUser
                if userMeta != None:
                    callback = self.__EventUpdateExistingUser
                self.__innerCardPanel = SettingsPanel(self.__tkRoot,self.__selectionPanal,uid,userMeta['nick_name'],userMeta['favourite_coffee'],coffeeSorts,callback)
            elif self.__currentMenuSelection == MenuSelectionType.MENU_INFO_AND_PAY:
                if userMeta != None:
                    self.__innerCardPanel = UserInfoAndPayPanel(self.__tkRoot,self.__selectionPanal,userMeta,self.__databaseConnector)
                else:
                    self.__innerCardPanel = SettingsPanel(self.__tkRoot,self.__selectionPanal,uid,'',coffeeSorts[0]['coffee_id'],coffeeSorts,self.__EventCreateNewUser)
                    self.__currentMenuSelection = MenuSelectionType.MENU_SETTINGS
                    self.__SetSelectionCursor(2)      
            if self.__logoutTimer != None:
                self.__logoutTimer.cancel()
            self.__loggedIn = True
            self.__logoutTimer = Timer(1,self.__LogoutTimer)
            self.__logoutTimer.start()
            self.__mutex.release()
        
    
    def EventCardNotRecognized(self,sender):
        if not self.__loggedIn:
            self.__mutex.acquire()
            self.__innerCardPanel = CardNotRecognizedPanel(self.__tkRoot,self.__selectionPanal)
            self.__mutex.release()

    def __SetSelectionCursor(self,pos):
        i = 0
        for cursors in self.__mainMenuCursor:
            if i == pos:
                cursors.config(image = self.__barIcoSelected)
            else:
                cursors.config(image = self.__barIco)
            i = i + 1

    def __EventCreateNewUser(self,arg:SettingsPanelResult):
        self.__databaseConnector.CreateNewUser(arg.GetUID(),arg.GetNick(),arg.GetFavouriteUID())

    def __EventUpdateExistingUser(self,arg:SettingsPanelResult):
        self.__databaseConnector.UpdateUser(arg.GetUID(),arg.GetNick(),arg.GetFavouriteUID())

    def __EventPlaceOrder(self,arg:OrderPanelResult):
        coffeeSort = self.__databaseConnector.GetCoffeeSort(arg.GetCoffeeOrderID())
        self.__databaseConnector.AddOrder(arg.GetUID(),coffeeSort['coffee_price'],coffeeSort['coffee_machine_strokes'], 'Ordered ID [' + str(coffeeSort['coffee_id']) + ']')

    def __EventOrder(self,event):
        self.__mutex.acquire()
        self.__currentMenuSelection = MenuSelectionType.MENU_ORDER
        self.__innerCardPanel = RequiredCardPanel(self.__tkRoot,self.__selectionPanal)
        self.__loggedIn = False
        if self.__logoutTimer != None:
            self.__logoutTimer.cancel()
        self.__mutex.release()
        self.__SetSelectionCursor(0)

    def __EventInfo(self,event):
        self.__mutex.acquire()
        self.__currentMenuSelection = MenuSelectionType.MENU_INFO_AND_PAY
        self.__innerCardPanel = RequiredCardPanel(self.__tkRoot,self.__selectionPanal)
        self.__loggedIn = False
        if self.__logoutTimer != None:
            self.__logoutTimer.cancel()
        self.__mutex.release()
        self.__SetSelectionCursor(1)

    def __EventSettings(self,event):
        self.__mutex.acquire()
        self.__currentMenuSelection = MenuSelectionType.MENU_SETTINGS
        self.__innerCardPanel = RequiredCardPanel(self.__tkRoot,self.__selectionPanal)
        self.__loggedIn = False
        if self.__logoutTimer != None:
            self.__logoutTimer.cancel()
        self.__mutex.release()
        self.__SetSelectionCursor(2)

    def __EventRanking(self,event):
        self.__mutex.acquire()
        self.__currentMenuSelection = MenuSelectionType.MENU_RANKING
        self.__innerCardPanel = RankingPanel(self.__tkRoot,self.__selectionPanal,self.__databaseConnector)
        self.__loggedIn = False
        if self.__logoutTimer != None:
            self.__logoutTimer.cancel()
        self.__mutex.release()
        self.__SetSelectionCursor(3)

    def __LogoutTimer(self):
        self.__mutex.acquire()
        self.__innerCardPanel.UpdateSecond()
        if not self.__innerCardPanel.IsStillInUsage():
            self.__innerCardPanel.FinalizeAction()
            self.__currentMenuSelection = MenuSelectionType.MENU_ORDER
            self.__innerCardPanel = RequiredCardPanel(self.__tkRoot,self.__selectionPanal)
            self.__logoutTimer.cancel()
            self.__logoutTimer = None
            self.__loggedIn = False
            self.__SetSelectionCursor(0)
        else:
            self.__logoutTimer = Timer(1,self.__LogoutTimer)
            self.__logoutTimer.start()
        self.__mutex.release()