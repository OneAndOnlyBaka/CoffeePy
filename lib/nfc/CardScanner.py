from threading import Thread
from threading import Lock
from datetime import datetime
import time
from lib.nfc.Flowtter import nfc

class UIDConverter():
    @staticmethod
    def ToInt(uid:bytearray) -> int:
        if len(uid) != 4:
            raise Exception('invalid format')

        id = uid[3]
        id *= 256
        id += uid[2]
        id *= 256
        id += uid[1]
        id *= 256
        id += uid[0]

        return id

class CardScannerThead(Thread):
    def __init__(self,newCardCallback = None,readErrorCard = None):
        Thread.__init__(self)
        self.__mutex = Lock()
        self.__uid = None
        self.__lastUpdate = None
        self.__run = True
        self.__stillConnected = False
        self.__newCardCallback = newCardCallback
        self.__readErrorCard = readErrorCard
        self.setDaemon(True)

    def GetLastCardInformation(self):
        self.__mutex.acquire()
        data = {'date': self.__lastUpdate ,'uid': self.__uid}
        self.__mutex.release()
        return data


    def run(self):
        while(self.__run):
            time.sleep(0.05)
            reader = None
            try:
                reader = nfc.Reader()
            except:
                self.__stillConnected = False
            finally:
                if reader != None:
                    if not self.__stillConnected:
                        try:
                            data = reader.get_uid().copy()
                        
                            self.__mutex.acquire()
                            self.__uid = data
                            self.__lastUpdate = datetime.now()
                            self.__mutex.release()

                            self.__stillConnected = True

                            if self.__newCardCallback:
                                self.__newCardCallback(self)
                        except:
                            if self.__readErrorCard:
                                self.__readErrorCard(self)


    def Kill(self):
        self.__run = True
        self.join
