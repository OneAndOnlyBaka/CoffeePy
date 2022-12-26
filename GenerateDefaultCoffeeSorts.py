from lib.database.DatabaseConnector import Connector

dbConnector = Connector()

baseprice = 0.3

dbConnector.CreateCoffeeSort('cafe crema',baseprice,1)
dbConnector.CreateCoffeeSort('coffee jug',baseprice*2,2)
dbConnector.CreateCoffeeSort('espresso',baseprice,1)
dbConnector.CreateCoffeeSort('double espresso',baseprice,1)
dbConnector.CreateCoffeeSort('espresso lungo',baseprice,1)
dbConnector.CreateCoffeeSort('cappuccino',baseprice,1)
dbConnector.CreateCoffeeSort('milk coffee',baseprice,1)