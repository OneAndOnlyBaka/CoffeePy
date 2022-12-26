import sqlite3
import os
from datetime import datetime
import time
from threading import Thread, Lock
import io

class Connector():
    def __init__(self):
        self.__databaseFile = os.getcwd() + '/coffee.db'
        self.__InitDatabase()

    def __InitDatabase(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS coffee_sorts(coffee_name TEXT, coffee_price REAL,coffee_machine_strokes INT)')
        cur.execute('CREATE TABLE IF NOT EXISTS user(user_uid INTEGER PRIMARY KEY, user_nick_name TEXT, user_favourite_coffee INT, FOREIGN KEY(user_favourite_coffee) REFERENCES coffee_sorts(rowid))')
        cur.execute('CREATE TABLE IF NOT EXISTS coffee_order(user_uid INTEGER, order_value REAL, order_coffee_machine_strokes INT, order_timestamp INT, order_datetime TEXT, order_info TEXT, FOREIGN KEY(user_uid) REFERENCES user(user_uid))')
        cur.execute('CREATE TABLE IF NOT EXISTS payments(user_uid INTEGER, payment_value REAL, payment_timestamp INT, payment_datetime TEXT, payment_info TEXT, FOREIGN KEY(user_uid) REFERENCES user(user_uid))')
        con.commit()
        con.close()

    def IsUserExisting(self,uid:int):
        return self.GetUserMeta(uid) != None

    def CreateNewUser(self,uid:int,nickName:str,favouriteCoffeeUid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO user VALUES(' + str(uid) + ',\'' + nickName + '\',' + str(favouriteCoffeeUid) +')')
        con.commit()
        con.close()

    def UpdateUser(self,uid:int,nickName:str,favouriteCoffeeUid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('UPDATE user SET user_nick_name = \'' + nickName + '\', user_favourite_coffee = ' + str(favouriteCoffeeUid) + ' WHERE user_uid = ' + str(uid))
        con.commit()
        con.close()

    def GetUserMeta(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT * FROM user WHERE user_uid = ' + str(uid))
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        return {'uid': rows[0][0], 'nick_name': rows[0][1], 'favourite_coffee': rows[0][2]}


    def CreateCoffeeSort(self,name:str,price:float,strokes:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO coffee_sorts VALUES(\'' + name + '\', ' + str(price) + ', ' + str(strokes) +')')
        con.commit()
        con.close()

    def GetCoffeeSorts(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT rowid,* FROM coffee_sorts')
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        ret = []
        for item in rows:
            ret.append({'coffee_id': item[0], 'coffee_name': item[1], 'coffee_price': item[2], 'coffee_machine_strokes': item[3]})

        return ret

    def GetCoffeeSort(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        try:
            cur.execute('SELECT rowid,* FROM coffee_sorts WHERE rowid = ' + str(uid))
            rows = cur.fetchall()
            if len(rows) == 0:
                rows = None
        except:
            rows = None
        finally:
            con.close()

        if rows == None:
            return None

        return {'coffee_id': rows[0][0], 'coffee_name': rows[0][1], 'coffee_price': rows[0][2], 'coffee_machine_strokes': rows[0][3]}

    def AddOrder(self,uid:int,value:float,strokes:int,note:str=''):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO coffee_order VALUES(' + str(uid) + ',' + str(value) + ',' + str(strokes) + ',' + str(int(time.time())) + ',\'' + str(datetime.now())  + '\',\''+ note +'\')')
        con.commit()
        con.close()

    def GetTotalCupsForUID(self,uid:int):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT SUM(order_coffee_machine_strokes) FROM coffee_order WHERE user_uid = ' + str(uid))
        rows = cur.fetchall()
        con.close()
        
        if len(rows) == 0:
            return 0

        if rows[0][0] == None:
            return 0

        return int(rows[0][0])

    def GetCurrentMonthCupsForUID(self,uid:int):
        date = time.mktime(datetime(datetime.now().year,datetime.now().month,1,0,0,0).timetuple())
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT SUM(order_coffee_machine_strokes) FROM coffee_order WHERE user_uid = ' + str(uid)
                    + ' AND order_timestamp >= ' + str(date))
        rows = cur.fetchall()
        con.close()
        
        if len(rows) == 0:
            return 0

        if rows[0][0] == None:
            return 0

        return int(rows[0][0])

    def GetAccountBalance(self,uid:int,round_value:bool=True):
        payments = self.GetPayments(uid,round_value=False)
        deposit = self.GetDeposit(uid,round_value=False)

        diff = payments - deposit
        if round_value:
            return round(diff,2)
        return diff

    def GetPayments(self,uid:int,round_value:bool=True) -> float:
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT SUM(payment_value) FROM payments WHERE user_uid = ' + str(uid))
        rows = cur.fetchall()
        con.close()

        if len(rows) == 0:
            return 0.0

        if rows[0][0] == None:
            return 0.0

        if round_value:
            return round(float(rows[0][0]),2)
        else:
            return float(rows[0][0])

    def GetDeposit(self,uid:int,for_year:int=-1,for_month:int=-1,round_value:bool=True) -> float:
        unixTimeSpanElements = self.__GetStartEndEndPoint(for_year,for_month) 
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        if unixTimeSpanElements[0] == None:
            cur.execute('SELECT SUM(order_value) FROM coffee_order WHERE user_uid = ' + str(uid))
        else:
            cur.execute('SELECT SUM(order_value) FROM coffee_order WHERE user_uid = ' + str(uid) 
                        + ' AND order_timestamp >= ' + str(unixTimeSpanElements[0])
                        + ' AND order_timestamp < ' + str(unixTimeSpanElements[1]))
        rows = cur.fetchall()
        con.close()
        
        if len(rows) == 0:
            return 0.0

        if rows[0][0] == None:
            return 0.0

        if round_value:
            return round(float(rows[0][0]),2)
        else:
            return float(rows[0][0])

    def __GetStartEndEndPoint(self,for_year,for_month):
        dateStart = None
        dateEnd = None
        if for_year > -1 or for_month > -1:
            if for_year == -1:
                for_year = datetime.now().year
            if for_month == -1:
                dateStart = datetime(for_year,1,1,0,0,0)
                dateEnd = datetime(for_year + 1,1,1,0,0,0)
            else:
                dateStart = datetime(for_year,for_month,1,0,0,0)
                if for_month != 12:
                    dateEnd = datetime(for_year,for_month + 1,1,0,0,0)
                else:
                    dateEnd = datetime(for_year + 1,1,1,0,0,0)

        if dateStart == None or dateEnd == None:
            return (None,None)

        return (time.mktime(dateStart.timetuple()),time.mktime(dateEnd.timetuple()))

    def AddPayment(self,uid:int,value:float):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('INSERT INTO payments VALUES(' + str(uid) + ',' + str(value) + ',' + str(int(time.time())) + ',\'' + str(datetime.now())  + '\',\'\')')
        con.commit()
        con.close()

    def GetUIDSortedByTotalCupsDecreasing(self):
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT user_uid, SUM(order_coffee_machine_strokes) FROM coffee_order GROUP BY user_uid ORDER BY SUM(order_coffee_machine_strokes) DESC')
        rows = cur.fetchall()
        con.close()

        ret = []

        try:
            for line in rows:
                ret.append(int(line[0]))
        except:
            ret = []

        return ret

    def GetUIDSortedByCurrentMonthsCupsDecreasing(self):
        date = time.mktime(datetime(datetime.now().year,datetime.now().month,1,0,0,0).timetuple())
        con = sqlite3.connect(self.__databaseFile)
        cur = con.cursor()
        cur.execute('SELECT user_uid, SUM(order_coffee_machine_strokes) FROM coffee_order'
                    + ' WHERE order_timestamp >= ' + str(date)
                    + ' GROUP BY user_uid ORDER BY SUM(order_coffee_machine_strokes) DESC')
        rows = cur.fetchall()
        con.close()

        ret = []

        try:
            for line in rows:
                ret.append(int(line[0]))
        except:
            ret = []

        return ret

    def CreateBackup(self,backupFile:str='coffee_backup.db'):
        con = sqlite3.connect(self.__databaseFile)
        with io.open(backupFile, 'w') as p:
            for line in con.iterdump():
                p.write('%s\n' % line)

class DatabaseBackupThread(Thread):
    def __init__(self,connector:Connector,path:str,backupIntervalMinutes:int=60,backupDepth=10):
        Thread.__init__(self)
        self.__running = True
        self.__backupIntervalSeconds = backupIntervalMinutes * 60
        self.__backupDepth = backupDepth
        self.__connector = connector
        self.__path = path
        self.__mutex = Lock()

    def run(self) -> None:
        backupCounter = 1
        running = self.__running
        while running:
            backupFileName = self.__path +  'coffee' + str(backupCounter) + '.db'

            self.__connector.CreateBackup(backupFileName)

            backupCounter = backupCounter + 1
            if backupCounter > self.__backupDepth :
                backupCounter = 1

            time.sleep(float(self.__backupIntervalSeconds))
            
            self.__mutex.acquire()
            running = self.__running
            self.__mutex.release()

    def kill(self):
        self.__mutex.acquire()
        self.__running = False
        self.__mutex.release()
        self.join()
