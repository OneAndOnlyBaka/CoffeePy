from lib.nfc import CardScanner
from gui.gui import *
from lib.database import DatabaseConnector

connector = DatabaseConnector.Connector()

mainApp = CoffeePyMain(connector,borderless=False)

cardscannerThread = CardScanner.CardScannerThead(mainApp.EventNewCardApplied,mainApp.EventCardNotRecognized)
cardscannerThread.start()

mainApp.RunLoop()    
cardscannerThread.Kill()