'''
SpyEndOfDayPump

QuantConnect backtest algo.

Buy SPY 16 minutes before EOD, sell at close
'''


from clr import AddReference
AddReference("System")
AddReference("QuantConnect.Algorithm")
AddReference("QuantConnect.Common")

from System import *
from QuantConnect import *
from QuantConnect.Algorithm import *
from QuantConnect.Data import *
from datetime import timedelta

class SpyEndOfDayPump(QCAlgorithm):

    def Initialize(self):

        self.SetStartDate(2020,3,26)
        self.SetEndDate(2020,6,25)
        self.SetCash(100000)
        self.DefaultOrderProperties.TimeInForce = TimeInForce.Day

        self.equity = self.AddEquity("SPY", Resolution.Second)

        self.yest_price = 100.0
        
        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.BeforeMarketClose("SPY", 15), self.BuyEodPump)


    def OnData(self, data):
        pass

    def BuyEodPump(self):

        if self.Securities["SPY"].Last > (self.yest_price * 0.97)
            self.SetHoldings("SPY", 1.0)

            qty = self.Portfolio["SPY"].Quantity

            self.MarketOnCloseOrder("SPY", qty)
        
        self.yest_price = self.Securities["SPY"].Price    
