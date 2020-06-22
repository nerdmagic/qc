'''
SpyStraddleClose2Open.py

QuantConnect backtest algo.

Simple algo to buy SPY straddles near market close and sell them 
after open each day.  In manual backtesting on ThinkOrSwim OnDemand,
this works great.


v0.1     6/21/2020     

 - Only buy if VXX price is lower than both today's open and yesterday's close.
 - Use market orders to buy and sell.

Unsurprisingly, this algo loses money in backtesting.

The difference is that limit orders for liquid option straddles can 
usually be filled near the bid-ask midpoint. In fact even market orders 
tend to fill near the midpoint at least in ToS OD.  

Whereas in this initial backtest, market order buys will always fill at 
the combined asks and sells always fill at the combined bids.   
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

class SpyStraddleCloseToOpen(QCAlgorithm):

    def Initialize(self):

        self.SetStartDate(2019,1,1)
        self.SetEndDate(2019,12,31)
        self.SetCash(100000)
        
        ## VIX would be better but there is no free minrute-resolution data 
        ## available, and I want to see what today's VIX pattern was.
        ## The VXX ETN is functionally close to a slowly decaying VIX EMA, 
        ## and should function okay for our needs.
        self.vxx = self.AddEquity("VXX", Resolution.Minute)
        self.equity = self.AddEquity("SPY", Resolution.Minute)
        self.option = self.AddOption("SPY", Resolution.Minute)
        self.symbol = self.option.Symbol
        self.option.SetFilter(-5, 3, timedelta(59), timedelta (90))

        self.Securities["SPY"].FeeModel = ConstantFeeModel(0)

        self.Schedule.On(self.DateRules.EveryDay("VXX"), self.TimeRules.AfterMarketOpen("VXX", 1), self.GetVxxOpen)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.AfterMarketOpen("SPY", 30), self.SellStraddles) 

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.BeforeMarketClose("SPY", 10), self.BuyStraddles)

        self.pcontracts={}
        self.ccontracts={}

        self.vxx_open=10.0
        self.vxx_close=11.0


    def OnData(self, slice):

        for i in slice.OptionChains:
            
            if i.Key != self.symbol: continue
            
            chain = i.Value

            calls = [x for x in chain if x.Right == 0]
            puts  = [x for x in chain if x.Right == 1]

            ## sort puts and calls by closest to (ATM -1) and by expiry
            pctrcts = sorted(puts, key = lambda x: abs(chain.Underlying.Price + 1 - x.Strike))
            cctrcts = sorted(calls, key = lambda x: abs(chain.Underlying.Price + 1 - x.Strike))

            self.pcontracts = sorted(pctrcts, key = lambda x:x.Expiry, reverse = True) 
            self.ccontracts = sorted(cctrcts, key = lambda x:x.Expiry, reverse = True) 


    def GetVxxOpen(self):
        self.vxx_open = self.Securities["VXX"].Open
        

    def BuyStraddles(self):

        ## only buy if VXX < today's open and VXX < yesterday's close
        if self.Securities["VXX"].Open < self.vxx_open and self.Securities["VXX"].Close < self.vxx_close:

            csymbol = self.ccontracts[0].Symbol
            psymbol = self.pcontracts[0].Symbol
         
            ## combined put+call ask price, x100 for contract price
            straddle_price = (self.ccontracts[0].AskPrice + self.pcontracts[0].AskPrice) * 100

            self.Debug("straddle price is {}".format(straddle_price))

            money = self.Portfolio.GetMarginRemaining(csymbol, OrderDirection.Buy)

            ## subtract 1 from qty to ensure brokerage commissions are covered
            qty = int(money / straddle_price) - 1
         
            ## Market orders. In my experience, in real life, limit orders will fill 
            ## near the bid-ask midpoint.  That difference is almost definitely necessary
            ## to make a profit with this scheme.
            self.MarketOrder(psymbol, qty)
            self.MarketOrder(csymbol, qty)

        ## get VXX near end-of-day
        self.vxx_close = self.Securities["VXX"].Close


    def SellStraddles(self):
        self.Liquidate()

       
