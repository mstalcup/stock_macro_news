# Signal experiment sweep

As-of: 2026-05-26 | Min n: 3

## Winners

- **ValueAct Capital: 13D new+amend** — n=4, med α20d=27.0255%, hit=75.0% (test n=3, test med=17.5512)
- **Starboard Value: volume pre-filing** — n=6, med α20d=10.6949%, hit=66.7% (test n=5, test med=17.8166)
- **Starboard Value: confluence** — n=6, med α20d=10.6949%, hit=66.7% (test n=5, test med=17.8166)
- **Greenlight Capital (Einhorn): volume pre-filing** — n=44, med α20d=8.9217%, hit=65.9% (test n=0, test med=None)
- **Greenlight Capital (Einhorn): confluence** — n=44, med α20d=8.9217%, hit=65.9% (test n=0, test med=None)
- **Pershing Square Capital Management: confluence** — n=3, med α20d=5.9165%, hit=66.7% (test n=1, test med=-2.8412)
- **Pershing Square Capital Management: volume pre-filing** — n=3, med α20d=4.9262%, hit=66.7% (test n=1, test med=-7.6836)
- **Volume spike (pre-filing)** — n=65, med α20d=4.9262%, hit=60.0% (test n=6, test med=10.6949)
- **Greenlight Capital (Einhorn): 13G new+amend** — n=74, med α20d=4.102%, hit=56.8% (test n=0, test med=None)
- **2+ layers confluence** — n=79, med α20d=2.5216%, hit=57.0% (test n=19, test med=-1.2146)
- **13D first per fund+ticker** — n=20, med α20d=1.9856%, hit=55.0% (test n=10, test med=-0.4232)
- **Starboard Value: 13d_new** — n=6, med α20d=1.8507%, hit=50.0% (test n=4, test med=1.8507)
- **Starboard Value: 13D deduped** — n=11, med α20d=0.6869%, hit=54.5% (test n=8, test med=-1.7541)
- **Point72 Asset Management: 13G new+amend** — n=206, med α20d=0.1373%, hit=50.5% (test n=166, test med=2.3818)

## Top 10 by med20 (any n>=3)

- ValueAct Capital: 13D new+amend: n=4, med20=27.0255%, hit=75.0%
- Starboard Value: volume pre-filing: n=6, med20=10.6949%, hit=66.7%
- Starboard Value: confluence: n=6, med20=10.6949%, hit=66.7%
- Greenlight Capital (Einhorn): volume pre-filing: n=44, med20=8.9217%, hit=65.9%
- Greenlight Capital (Einhorn): confluence: n=44, med20=8.9217%, hit=65.9%
- Pershing Square Capital Management: confluence: n=3, med20=5.9165%, hit=66.7%
- Volume spike (pre-filing): n=65, med20=4.9262%, hit=60.0%
- Pershing Square Capital Management: volume pre-filing: n=3, med20=4.9262%, hit=66.7%
- Greenlight Capital (Einhorn): 13G new+amend: n=74, med20=4.102%, hit=56.8%
- 2+ layers confluence: n=79, med20=2.5216%, hit=57.0%

## Recommendation

Deploy the top winner(s) with `works_oos_hint` and enough test-period n.
Prefer **13d_new + volume confirm** or **per-fund volume** over blind 13G.