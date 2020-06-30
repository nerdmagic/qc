'''
SpyStraddleClose2Open.py

QuantConnect backtest algo.

Simple algo to buy SPY straddles near market close and sell them
after open each day. Entry indicators to be added.


v0.1     6/21/2020

 - Only buy if VXX price is lower than both today's open and yesterday's close.
 - Use market orders to buy and sell.

The problem is that limit open orders for liquid option straddles can
usually be filled near the bid-ask midpoint, and even sell orders don't
always have to be filled all the way at the bid.

Whereas in this initial backtest, market order buys will always fill at
the combined asks and sells always fill at the combined bids.


v0.2     6/29/2020

 - Abandon QC's fancy order/portfolio/chart system and just print results
  to the console.
 - Add the ability to mix in the underlying (e.g. 50% shares, 50% straddles)
 - Workaround for bad option data.
 - Workaround for limit down circuit breakers in March.

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
from QuantConnect.Data.Custom.CBOE import *

from datetime import timedelta
from time import sleep

class SpyStraddleCloseToOpen(QCAlgorithm):

    def Initialize(self):

        self.SetStartDate(2020,1,1)
        self.SetEndDate(2020,2,1)

        ## See self.money below
        ##
        ## We are not actually using QC's order/portfolio mechanism, because
        ## it is not possible in their options order system to treat straddles
        ## and other multi-leg options as a unit like we can at real brokers.
        ## Thus we can't do limit orders on them, and limit option buys near the
        ## midpoint waiting for price to come to us make a big difference
        ## in this strategy.
        self.SetCash(50000)
        self.DefaultOrderProperties.TimeInForce = TimeInForce.Day

        self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
        self.option = self.AddOption("SPY", Resolution.Minute)
        self.symbol = self.option.Symbol

        ## This determines the option strike prices and expiries we'll see
        ## The reason we need this many strikes is too complex for a comment
        self.option.SetFilter(-30, 20, timedelta(56), timedelta (96))

        self.Securities["SPY"].FeeModel = ConstantFeeModel(0)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.AfterMarketOpen("SPY", 1), self.PauseBeforeOnData)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.AfterMarketOpen("SPY", 2), self.SellTheOpen)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.AfterMarketOpen("SPY", 30), self.SellAfterCircuitBreaker)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.BeforeMarketClose("SPY", 11), self.PauseBeforeOnData)

        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.BeforeMarketClose("SPY", 10), self.BuyTheClose)

        ## circuit breaker workaround, market was stopped shortly after 9:30 on these days
        self.cb_days = ["2020-03-09", "2020-03-12", "2020-03-16"]

        ## This is our actual starting fundage for
        ## our primitive asset tracking
        self.money = 100000.00

        ## Straddle strike prices need to be slightly below at-the-money,
        ## because ATM straddles that "look" delta-neutral are vega-biased
        ## to the low side -- the puts will make money but it will be next
        ## to impossible to make money on the high side.. I found from live
        ## and paper trade experiments that about 3-4 points below ATM works
        ## well for SPY. Quick testing on QC found that -3 gave the best
        ## results in both very low and very high VIX (surprisingly),
        ## using early 2017 and early 2020 environments.
        ## (I thought it would vary with volatility).
        self.strike_offset = -3

        ## straddle price offsets, buy limit from midpoint, sell from bid
        self.option_buy_offset = float(0)
        self.option_sell_offset = 0.02

        ## 50% in straddles, 50% in shares
        ## this is probably better varied by volatility
        self.option_portion = 0.5
        self.share_portion = 0.5

        self.put_now={}
        self.call_now={}

        self.call_invested={}
        self.put_invested={}

        self.option_qty = 0
        self.share_qty = 0

        self.holding = False

        self.spy_price_invested = float(0)

        self.fees = float(0)

        self.expiry = self.Time
        self.strike = float(0)

        self.option_buy_price = float(0)
        self.option_total_profit = float(0)

        self.share_buy_price = float(0)
        self.share_total_profit = float(0)

        self.last_pct = float(0)
        self.last_profit = float(0)

        self.circuit_breaker = False


    ## QC options contracts have a unique identifier for each individual
    ## one-minute data candle.  We have to dig those ID's out in the OnData
    ## tick function, and assign to globals accessible elsewhere.

    def OnData(self, slice):

        for i in slice.OptionChains:

            if i.Key != self.symbol: continue

            chain = i.Value

            calls = [x for x in chain if x.Right == 0]
            puts  = [x for x in chain if x.Right == 1]

            ## find the best contracts to buy
            ## sort puts and calls by closest strike to offset-ATM
            ## then sort by expiry, longest preferred
            pctrcts = sorted(puts, key = lambda x: abs(chain.Underlying.Price + self.strike_offset - x.Strike))
            cctrcts = sorted(calls, key = lambda x: abs(chain.Underlying.Price + self.strike_offset - x.Strike))

            pctrcts = sorted(pctrcts, key = lambda x:x.Expiry, reverse = True)
            cctrcts = sorted(cctrcts, key = lambda x:x.Expiry, reverse = True)

            ## Sometimes I don't get good option data on QC, though it seems
            ## somewhat random. Give it seven tries to get a working contract
            ## as we'd rather buy a suboptimal strike than not buy.
            ## Not logging this but I think it's falling back sometimes because
            ## I noticed an offset-5 strike, which shouldn't normally happen.
            if pctrcts and cctrcts:
                for j in range(7):
                    empty=True
                    if cctrcts[j] and pctrcts[j]:
                        if not re.search(r"\|", "{}{}".format(cctrcts[j].Symbol, pctrcts[j].Symbol)):
                            self.put_now = pctrcts[j]
                            self.call_now = cctrcts[j]
                            empty=False
                            break
                    else:
                        empty=True
                        break
            else:
                empty=True

            if empty:
                self.put_now={}
                self.call_now={}


            ## Find currently-invested contract slices if any
            if self.option_qty > 0:
                call_match = [x for x in calls if x.Expiry == self.expiry if x.Strike == self.strike]
                put_match = [x for x in puts if x.Expiry == self.expiry if x.Strike == self.strike]

                if call_match and put_match:
                    self.call_invested = call_match[0]
                    self.put_invested = put_match[0]
                else:
                    self.call_invested = {}
                    self.put_invested = {}


    def GetStraddleMidPrice(self, call, put):
        straddle_ask = (call.AskPrice + put.AskPrice)
        straddle_bid = (call.BidPrice + put.BidPrice)
        straddle_rough = (straddle_ask + straddle_bid)/2
        straddle_mid = float("{:.2f}".format(straddle_rough))

        return(straddle_mid)


    def PauseBeforeOnData(self):
        sleep(1)


    def BuyTheClose(self):

        buy_shares = True

        if not self.holding:
            self.Debug("{} BUY".format(str(self.Time)))

            if self.option_portion > 0:

                ## if the options were left empty, skip the day
                if self.call_now and self.put_now:

                    self.expiry = self.call_now.Expiry
                    put_expiry = self.put_now.Expiry

                    self.strike = self.call_now.Strike
                    put_strike = self.put_now.Strike

                    self.spy_price_invested = self.call_now.UnderlyingLastPrice

                    if put_expiry != self.expiry:
                        self.Error ("Exp mismatch: P {} C {}".format(put_expiry, self.expiry))

                    if put_strike != self.strike:
                        self.Error ("Str mismatch: P {} C {}".format(put_strike, self.strike))

                    straddle_mid = self.GetStraddleMidPrice(self.call_now, self.put_now)

                    self.option_buy_price = straddle_mid + self.option_buy_offset
                    option_money = self.money * self.option_portion
                    self.option_qty = int( (option_money - 20.00) / (self.option_buy_price * 100) )
                    self.option_cost_basis = (self.option_qty * self.option_buy_price * 100) + 20.00

                    self.fees = self.fees + 20.00

                    self.call_invested = self.call_now
                    self.put_invested = self.put_now

                    self.Debug("{} Str {} {:8s} @{:.2f} Dbt {:.2f}".format(self.option_qty,self.strike, str(self.expiry), self.option_buy_price, self.option_cost_basis))
                    self.holding = True
                else:
                    self.Debug("{}: No option data, not buying".format(str(self.Time)))
                    ## If we don't buy options, don't buy shares either
                    buy_shares = False

            if self.share_portion > 0 and buy_shares:

                self.share_buy_price = self.Securities[self.spy].AskPrice
                share_money = self.money*self.share_portion
                self.share_qty = int(share_money / self.share_buy_price)
                self.share_cost_basis = self.share_qty * self.share_buy_price

                self.Debug("{} SPY @{:.2f} Dbt {:.2f}".format(self.share_qty, self.share_buy_price, self.share_cost_basis))
                self.holding = True

            if self.holding:
                self.money = self.money - self.option_cost_basis - self.share_cost_basis


    def SellEverything(self):
        if self.holding:
            self.Debug("{} SELL".format(str(self.Time)))

            day_profit = float(0)

            if self.option_qty > 0:

                if self.call_invested and self.put_invested:
                    straddle_bid = self.call_invested.BidPrice + self.put_invested.BidPrice
                    option_price = straddle_bid + self.option_sell_offset
                    spy_underlying_price = self.call_invested.UnderlyingLastPrice
                else:
                    self.Error("Could not match invested option contracts, setting closing price to cost.")
                    option_price = self.option_buy_price
                    spy_underlying_price = 0.0

                option_credit = option_price * self.option_qty * 100
                option_profit = option_credit - self.option_cost_basis
                option_pct = (option_profit / self.option_cost_basis) * 100
                self.option_total_profit += option_profit

                self.Debug("{} Str {} @{:.2f} Cr {:.2f}".format(self.option_qty, self.strike, option_price, option_credit))
                self.Debug("Opt P/L {:.2f}, {:.2f}%  Agg {:.2f}".format(option_profit, option_pct, self.option_total_profit))
                self.money += option_credit
                day_profit += option_profit
                self.option_qty = 0

            if self.share_qty > 0:
                share_price = self.Securities[self.spy].BidPrice
                share_credit = self.share_qty * share_price
                share_profit = share_credit - self.share_cost_basis
                share_pct = (share_profit/self.share_cost_basis) * 100
                self.share_total_profit += share_profit

                self.Debug("{} SPY @{:.2f} Cr {:.2f}".format(self.share_qty, share_price, share_credit))
                self.Debug("Shr P/L {:.2f}, {:.2f}%  Agg {:.2f}".format(share_profit, share_pct, self.share_total_profit))
                self.money += share_credit
                day_profit += share_profit
                self.share_qty = 0

            day_pct = (day_profit / (self.share_cost_basis + self.option_cost_basis)) * 100

            self.Debug("Day P/L {:.2f}, {:.2f}%  Bal {:.2f}%".format(day_profit, day_pct, self.money))

            self.holding = False
            self.option_cost_basis = float(0)
            self.share_cost_basis = float(0)


    def SellTheOpen(self):
        # Check for March 2020 opening circuit breakers
        for day in self.cb_days:
            if re.search(day, str(self.Time)):
                self.circuit_breaker = True
                self.Debug ("{} Circuit breaker at open".format(str(self.Time)))

        if not self.circuit_breaker:
            self.SellEverything()


    def SellAfterCircuitBreaker(self):
        if self.circuit_breaker:
            self.circuit_breaker = False

            self.SellEverything()
