from lib.nfc import CardScanner
from gui.gui import *
from lib.database.DatabaseConnector import DatabaseBackupThread,Connector
import configparser
import os

autoBackupDB = False
if os.path.exists('CoffeePy.ini'):
    config = configparser.ConfigParser()
    config.read('CoffeePy.ini')
    if 'Backup' in config.sections():
        autoBackupDB = bool(config['Backup']['Enabled'])
        autoBackupPath = config['Backup']['Path']
        try:
            autoBackupInterval = int(config['Backup']['Inverval'])
        except:
            autoBackupInterval = 60
        try:
            autoBackupDepth = int(config['Backup']['Depth'])
        except:
            autoBackupDepth = 10


connector = Connector()

mainApp = CoffeePyMain(connector,borderless=True)

databaseThread = None
if autoBackupDB:
    databaseThread = DatabaseBackupThread(connector,autoBackupPath,autoBackupInterval)
    databaseThread.start()


cardscannerThread = CardScanner.CardScannerThead(mainApp.EventNewCardApplied,mainApp.EventCardNotRecognized)
cardscannerThread.start()

mainApp.RunLoop()
if databaseThread != None:
    databaseThread.kill()    
cardscannerThread.Kill()