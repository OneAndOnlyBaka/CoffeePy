# How the numbers are calculated

Every number CoffeePy shows - a balance, a podium place, the pillory, a chart - comes from the
same few rules. They live in [`coffee/services.py`](../coffee/services.py) (all aggregate
queries) and a few constants in [`kiosk/views.py`](../kiosk/views.py); this page states them
in words, with where each one appears.

- [The basics](#the-basics)
- [Balance](#balance)
- [The pillory and the month reminder](#the-pillory-and-the-month-reminder)
- [Rankings and medals](#rankings-and-medals)
- [Thank-you messages](#thank-you-messages)
- [Paying](#paying)
- [Dashboard overview and statistics](#dashboard-overview-and-statistics)
- [Your stats in the portal](#your-stats-in-the-portal)
- [Time, rounding and departed members](#time-rounding-and-departed-members)

## The basics

**A cup is a machine stroke.** Every coffee sort has a price and a number of strokes; a jug or
a double is a sort with two. An order stores both, copied from the sort at the moment it is
booked:

```
order.value           = sort.price            (what this order costs)
order.machine_strokes = sort.machine_strokes  (how many cups it counts as)
```

So a later price change never rewrites history, and every ranking, statistic and comparison
counts **cups = the sum of `machine_strokes`**, not the number of orders. The one exception
is the statistics page's distribution by coffee sort, which counts orders (see below).

**A payment** is an amount and a method name ("Cash" or a configured payment method). It is a
note that someone says they paid - nothing is transferred, and an admin can correct it under
"Bookings".

## Balance

```
balance = sum of all payments − sum of all order values
```

Positive means credit, negative means the member owes that much. Shown, rounded to cents, on
the kiosk's **Pay** screen and in the portal; the dashboard shows what is owed through the
pillory (below).

## The pillory and the month reminder

The pillory is not "everyone with a negative balance". It looks at the balance **as it stood
when the current month began**, counting every payment so far:

```
balance before this month = sum of all payments (up to now)
                          − sum of order values strictly before the 1st of this month, 00:00
```

A member is on the pillory when that is below zero: they still owe for last month or earlier,
even after everything they have paid since. Coffee drunk this month doesn't count yet - it is
settled at the next month change. Sorted by that amount, most owed first.

| Where | What it shows |
|---|---|
| Kiosk, ranking screen, "walk of shame" | the 7 members who owe most, with the amount (only when the pillory is switched on under System) |
| Kiosk, order screen | a reminder with the amount, to the member who just tapped, when their balance before this month is negative |
| Dashboard, Pillory | everyone on it (up to 1000), with payments, orders before this month and the balance |
| Dashboard, overview | how many are on it, and the open amount = the sum of what they owe |

The kiosk's reminder and the pillory use the same function
(`balance_before_current_month`), so they can't disagree.

**Example.** Anna's orders until 31 March add up to 42.00 €; she paid 30.00 € in February and
10.00 € on 3 April, and has drunk 6.00 € worth since 1 April. On 10 April:
balance before this month = 40.00 − 42.00 = **−2.00 €** → on the pillory with 2.00 €, and her
order screen reminds her of 2.00 €. Her balance on the Pay screen is 40.00 − 48.00 = −8.00 €.

## Rankings and medals

| Board | Who | Ranked by |
|---|---|---|
| All time | active members with at least one order | sum of strokes, all time |
| This month | active members with an order this month | sum of strokes since the 1st, 00:00 local time |

The kiosk shows the top 3 of each on a podium - all time as the podium stands (2nd, 1st,
3rd from left to right), the month in order of the standings so far. A place nobody holds yet
stays empty. A member who holds one of those places sees its medal on the order screen after
tapping their card. Equal cups keep the database's order; there is no tie-break.

The ranking is optional (System → kiosk boards, off on a new device). The kiosk recomputes it
only when something behind it changed - a new order or payment, a member added, renamed,
departed or merged, the month rolling over, or any correction in the dashboard
(`ranking_fingerprint`) - otherwise it serves the cached rows, for at most five minutes.

## Thank-you messages

After an order the kiosk shows an animation and a message for 20 seconds, picked by the cups
(strokes) this member has drawn **today**, including the order just booked. The first rule that
matches wins:

| Rule | Message |
|---|---|
| the member's name doesn't pass the name rules | "Who are you?! Set your name under Settings." |
| more than 100 cups this ISO week (since Monday 00:00) | "100+ cups this week?! Maybe take a break..." |
| 1 cup today | "Enjoy your coffee!" |
| 2-3 | "Cheers - enjoy!" |
| 4-5 | "You're on a roll today!" |
| 6-8 | "Whoa, pace yourself!" |
| 9 or more | "Certified caffeine junkie today!" |

## Paying

- **Suggested amount** on the kiosk's Pay screen: what the member owes (the negative balance
  as a positive amount), or 0.00 with nothing owed. The member can change it; "1,50" and
  "1.50" are both accepted, and only amounts above zero are booked.
- **Payment links and QR codes** appear only while something is owed, prefilled with exactly
  that amount: PayPal as `https://paypal.me/<user>/<amount>EUR`, a custom link by replacing
  `{amount}` (two decimals, a dot). Methods without a link (WERO) show a text with the amount
  instead.

## Dashboard overview and statistics

| Tile or chart | Calculation |
|---|---|
| Cups today / this month | strokes since today 00:00 / since the 1st 00:00 |
| Active members | members not marked as departed |
| Debtors / open amount | count and sum of the pillory (above) |
| Monthly totals (12 months) | per calendar month: sum of order values, sum of payments (purchases credited to members left out), and - as a series of its own, only when there are any - sum of all purchases, the coffee box's included (below) |
| Payment methods | per method name, all time: sum paid and number of payments |
| Coffee sorts | per sort, all time: **number of orders** (imported orders without a known sort are left out) |
| Price trend (12 quarters) | per sort and quarter: average order value = sum of values ÷ number of orders - shows when a price changed |
| By hour / by weekday | strokes per local hour (00-23) / per weekday (Monday first), all time |

The CSV exports (orders, payments) contain every row, oldest first, unaggregated.

## Your stats in the portal

The portal compares one member with everybody else, on equal terms:

| Figure | Calculation |
|---|---|
| Your cups | your strokes, all time |
| Average of the others | the others' all-time strokes ÷ the number of other members who ever ordered |
| Compared with the average | (yours − average) ÷ average, in percent; nothing shown while there is nobody to compare with |
| Your place | 1 + the number of other members with more cups (counts departed members too, unlike the kiosk's podium) |
| Per month (12 months) | your strokes against the average of the others **who ordered that month** - members who stopped coming don't drag it down |
| By hour, weekday, coffee sort | each side as a **share of its own total**, in percent - in absolute cups one member would lose every bar to two dozen others |

## Purchases, stock and the price suggestion

Only while the inventory is switched on (dashboard → System: "only in the dashboard" or "for
members too").

**A purchase a member paid for is a payment in kind.** It creates a payment of its full price with
the method "Purchase", so it counts in the balance, the pillory and everywhere else a payment does -
and editing or deleting the purchase changes or removes that payment with it. **A purchase paid
from the coffee box** (dashboard only) creates no payment: it counts in the stock, the spending,
the costs and the statistics' purchases bar, in nobody's balance.

```
stock = newest stock-take + quantity bought since      (nothing is taken off per cup)
```

Without a stock-take the stock is everything ever bought.

**Costs** (dashboard → Inventory → Costs) cover the last 3, 6 or 12 months. Every item is
reckoned over its own stretch:

```
with two stock-takes in the period:
  used     = first count + bought between the counts − last count     (never below 0)
  cost     = used × (sum of prices ÷ sum of quantities bought in the period)
  stretch  = first count → last count
otherwise:
  cost     = sum of the item's purchase prices in the period
  stretch  = the whole period

per cup (item)  = cost ÷ strokes ordered in the item's stretch
cost per cup    = sum over all items
```

Stock-takes value what was used - a large purchase at the end of the period that is still on the
shelf doesn't make the cups dearer. Without them, purchases stand in for use.

| Figure | Calculation |
|---|---|
| Revenue − purchases | sum of order values minus sum of purchase prices in the period |
| Written off | for every member marked as departed in the period: orders − payments, if positive; as a share of the period's revenue |
| Suggestion per sort | strokes × cost per cup × (1 + margin), rounded **up** to 5 cents; margin set on the same page (default 10%) |

No suggestion is shown below purchases in 3 different months of the period (in all of them for
a shorter period) or below 100 cups. It is only a suggestion: prices are still changed under
Coffee sorts, and an order keeps the price it was booked with.

## Time, rounding and departed members

- **Local time.** "Today", "this month", "this week", the hour of day and the weekday are all
  in the device's time zone (`TIME_ZONE`), not UTC - the database stores UTC, the queries
  convert. A month starts on the 1st at 00:00 local time, a week on Monday at 00:00.
- **Money** is stored as decimals with two places and rounded to cents for display; nothing is
  ever calculated in floating point except the charts' values.
- **Departed members** (dashboard → Members → "Mark as departed") drop out of the kiosk's
  rankings, the pillory and the active-member count - marking someone as departed is how a debt
  that won't be paid is written off. Their orders and payments stay, and still count in the
  statistics and the totals.
- **Deleted bookings** are gone from every calculation; each correction writes an audit entry.
