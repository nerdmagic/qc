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

v0.2     6/26/2020

Use limit orders to sell.

'''

from clr import AddReference
AddReference("System")
AddReference("QuantConnect.Algorithm")
AddReference("QuantConnect.Common")

import re
from System import *
from QuantConnect import *
from QuantConnect.Algorithm import *
from QuantConnect.Data import *
from datetime import timedelta

class SpyStraddleCloseToOpen(QCAlgorithm):

    def Initialize(self):

        self.SetStartDate(2019,1,1)
        self.SetEndDate(2019,12,31)
        self.SetCash(20000)
        self.DefaultOrderProperties.TimeInForce = TimeInForce.Day

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

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.AfterMarketOpen("SPY", 5), self.SellStraddles)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.BeforeMarketClose("SPY", 10), self.BuyStraddles)

        self.pcontracts={}
        self.ccontracts={}

        self.vxx_open=10.0
        self.vxx_close=11.0

        self.stop=0.99                  ## percent of mid price for stop orders
        self.limit_buy_offset=0.01   ## offset from midpoint in cents for buy limit orders
        self.limit_sell_offset=0.02  ## offset from midpoint in cents for sell limit orders


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

        if not self.Portfolio.Invested:

            abort = 0

            ## only buy if VXX < today's open and VXX < yesterday's close
            if self.Securities["VXX"].Open < self.vxx_open and self.Securities["VXX"].Close < self.vxx_close:

                ## sometimes we don't get an option list
                ## in that case skip the day
                if self.ccontracts and self.pcontracts:
                    csymbol = self.ccontracts[0].Symbol
                    psymbol = self.pcontracts[0].Symbol
                    ## sometimes these symbols are dirty and will break shit
                    ## if that's the case, just skip this day
                    if re.search(r"\|", "{}{}".format(csymbol, psymbol)):
                        abort=1
                else:
                    abort=1

                if (abort == 0):

                    self.Debug("csymbol is {}".format(csymbol))
                    self.Debug("psymbol is {}".format(psymbol))

                    ## combined put+call ask/bid prices, x100 for contract price
                    straddle_ask = (self.pcontracts[0].AskPrice + self.ccontracts[0].AskPrice)

                    self.Debug("straddle ask price is {}".format(straddle_ask))

                    put_mid_rough  = (self.pcontracts[0].AskPrice + self.pcontracts[0].BidPrice) / 2
                    call_mid_rough = (self.ccontracts[0].AskPrice + self.ccontracts[0].BidPrice) / 2

                    put_mid = float("{:.2f}".format(put_mid_rough))
                    call_mid = float("{:.2f}".format(call_mid_rough))
                    self.Debug("put_mid is {}".format(put_mid))
                    self.Debug("call_mid is {}".format(call_mid))

                    money = self.Portfolio.GetMarginRemaining(csymbol, OrderDirection.Buy)
                    self.Debug("Money is {}".format(money))

                    ## subtract 1 from qty to ensure brokerage commissions are covered
                    qty = int(money / (straddle_ask*100)) - 1

                    self.Debug("Qty is {}".format(qty))

                    callMarketTicket = self.MarketOrder(csymbol, qty)
                    putMarketTicket = self.MarketOrder(psymbol, qty)

        ## get VXX near end-of-day
        self.vxx_close = self.Securities["VXX"].Close



    def SellStraddles(self):

        if self.Portfolio.Invested:
            option_invested = [x.Key.Value for x in self.Portfolio if x.Value.Invested]
            for contract in option_invested:

                symbol = self.Securities[contract].Symbol

                self.Debug("sell symbol is {}".format(symbol))

                qty = self.Portfolio[contract].Quantity

                limit_price_rough = ((self.Securities[contract].AskPrice + self.Securities[contract].BidPrice)/2) + self.limit_sell_offset

                limit_price = float("{:.2f}".format(limit_price_rough))

                self.Debug("sell limit price is {}".format(limit_price))

                try:
                    self.LimitOrder(symbol, -qty, limit_price)
                except:
                    liquidate()
                    break
