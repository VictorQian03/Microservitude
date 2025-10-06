-- symbols
INSERT INTO symbols(ticker) VALUES
  ('AAPL'), ('MSFT'), ('TSLA')
ON CONFLICT (ticker) DO NOTHING;

-- daily_liquidity (ADV in USD)
-- use a few recent dates
INSERT INTO daily_liquidity(ticker, d, adv_usd) VALUES
  ('AAPL', '2025-09-17', 2.5e11),
  ('AAPL', '2025-09-18', 2.6e11),
  ('AAPL', '2025-09-19', 2.55e11),
  ('MSFT', '2025-09-19', 1.90e11),
  ('TSLA', '2025-09-19', 1.20e11)
ON CONFLICT (ticker, d) DO NOTHING;

-- impact_models
-- pct_adv: cost_usd = c * q * Q * P, q = min(Q*P/ADV$, cap)
INSERT INTO impact_models(name, version, params, active)
VALUES
  ('pct_adv', 1, '{"c": 0.5, "cap": 0.10}'::jsonb, TRUE)
ON CONFLICT (name, version) DO NOTHING;

-- sqrt: cost_bps = A * sqrt(Q / ADV_shares) + B
-- parameters expect A and B in bps
INSERT INTO impact_models(name, version, params, active)
VALUES
  ('sqrt', 1, '{"A": 25.0, "B": 2.0}'::jsonb, TRUE)
ON CONFLICT (name, version) DO NOTHING;
