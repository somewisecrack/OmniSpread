import math
import numpy as np
import pandas as pd
import yfinance as yf
import scipy.stats as st
from itertools import combinations
from statsmodels.tsa.stattools import adfuller
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.vector_ar.vecm import coint_johansen
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logger = logging.getLogger("OmniSpreadEngine")


class OmniSpreadEngine:
    """
    Full cointegration scanner combining:
      - Cell A: CADF + Johansen cointegration screening with Kalman beta
      - Cell B: Ensemble Monte Carlo with parameter uncertainty, block bootstrap,
                extreme-Z tracking, PnL since extreme Z
    """

    # --- Ensemble MC parameters ---
    ENSEMBLE_M = 80
    SIMS_PER_DRAW = 2000
    USE_BOOTSTRAP_RESID = True
    BLOCK_LEN_FACTOR = 0.25
    RNG_SEED = 42
    MAX_TOTAL_SIMS = 400_000

    # --- Screening filters ---
    Z_SCORE_LIMIT = 2.0
    ADF_P_VALUE = 0.1
    HURST_LIMIT = 0.45

    def __init__(self, tickers, start_date=None, end_date=None, period="3y", interval="1d",
                 top_n=50):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.period = period
        self.interval = interval
        self.top_n = top_n
        self.data = None
        self.industry_map = {}

    # ========================
    #   DATA FETCHING
    # ========================

    def fetch_data(self):
        """Download price data in small batches to avoid yfinance throttling inside FastAPI."""
        import time
        logger.info(f"Fetching data for {len(self.tickers)} tickers...")

        base_kwargs = {
            "interval": self.interval,
            "auto_adjust": False,
            "progress": False,
        }
        if self.start_date and self.end_date:
            base_kwargs.update({"start": self.start_date, "end": self.end_date})
        else:
            base_kwargs.update({"period": self.period})

        BATCH_SIZE = 20
        all_batches = [self.tickers[i:i + BATCH_SIZE] for i in range(0, len(self.tickers), BATCH_SIZE)]
        data_frames = []

        for batch_idx, batch in enumerate(all_batches):
            if batch_idx > 0:
                time.sleep(1.5)  # brief pause between batches to avoid rate-limiting

            batch_df = None
            for attempt in range(3):
                if attempt > 0:
                    time.sleep(3 * attempt)
                try:
                    raw = yf.download(batch, **base_kwargs)
                    if not raw.empty:
                        batch_df = raw
                        break
                except Exception as e:
                    logger.warning(f"Batch {batch_idx+1} attempt {attempt+1} error: {e}")

            if batch_df is None or batch_df.empty:
                logger.warning(f"Batch {batch_idx+1} returned no data after 3 attempts")
                continue

            # Extract Adj Close — always MultiIndex in modern yfinance
            if isinstance(batch_df.columns, pd.MultiIndex):
                try:
                    adj = batch_df["Adj Close"]
                except KeyError:
                    logger.warning(f"Batch {batch_idx+1}: 'Adj Close' missing in MultiIndex")
                    continue
            else:
                if "Adj Close" in batch_df.columns:
                    adj = batch_df[["Adj Close"]]
                    adj.columns = [batch[0]]
                else:
                    logger.warning(f"Batch {batch_idx+1}: no Adj Close column found")
                    continue

            # Handle single-ticker downloads that return a Series under MultiIndex
            if isinstance(adj, pd.Series):
                adj = adj.to_frame(name=batch[0])

            valid_cols = adj.dropna(axis=1, how="all").columns.tolist()
            if valid_cols:
                data_frames.append(adj[valid_cols])
                logger.info(f"Batch {batch_idx+1}: got {len(valid_cols)}/{len(batch)} tickers")
            else:
                logger.warning(f"Batch {batch_idx+1}: all columns NaN after dropna")

        if not data_frames:
            logger.warning("No data returned from yfinance")
            self.data = pd.DataFrame()
            return []

        combined = pd.concat(data_frames, axis=1)
        # Remove duplicate columns (same ticker in multiple batches shouldn't happen, safety net)
        combined = combined.loc[:, ~combined.columns.duplicated()]

        # Drop tickers with >10% missing rows
        threshold = int(len(combined) * 0.9)
        combined = combined.dropna(axis=1, thresh=threshold)

        # Fill remaining gaps
        self.data = combined.ffill().bfill()

        # Ensure index is sorted and strictly unique (important for intraday data)
        self.data = self.data.loc[~self.data.index.duplicated(keep="last")].sort_index()

        # Preserve original ticker ordering
        active = [s for s in self.tickers if s in self.data.columns]
        self.data = self.data[active]
        logger.info(f"Active tickers after cleaning: {len(active)} / {len(self.tickers)}")
        return active

    def fetch_industries(self):
        """Fetch industry classification for each ticker."""
        for t in self.tickers:
            try:
                info = yf.Ticker(t).info
                self.industry_map[t] = info.get("industry", "Unknown")
            except Exception:
                self.industry_map[t] = "Unknown"
        logger.info(f"Fetched industries for {len(self.industry_map)} tickers")

    # ========================
    #   HELPER FUNCTIONS
    # ========================

    @staticmethod
    def hurst(ts):
        ts = np.array(ts)
        if len(ts) < 20:
            return np.nan
        max_lag = min(100, len(ts) - 1)
        lags = range(2, max_lag + 1)
        tau = [np.std(ts[lag:] - ts[:-lag]) for lag in lags]
        if any(t == 0 for t in tau):
            return np.nan
        return np.polyfit(np.log(list(lags)), np.log(tau), 1)[0] * 2.0

    @staticmethod
    def kalman_beta_series(x, y, beta0, R=1e-5):
        x_arr, y_arr = x.values, y.values
        n = len(x_arr)
        beta, P = beta0, 1.0
        Q = np.var(y_arr - beta0 * x_arr)
        betas = np.zeros(n)
        for t in range(n):
            P += R
            H = x_arr[t]
            resid = y_arr[t] - H * beta
            S = H * P * H + Q
            K = P * H / S
            beta += K * resid
            P *= (1 - K * H)
            betas[t] = beta
        return pd.Series(betas, index=x.index)

    @staticmethod
    def _block_bootstrap_errors(resid, horizon, rng, block_len):
        n = len(resid)
        if n == 0:
            return rng.normal(0, 1, size=horizon)
        block_len = max(1, int(block_len))
        samples = []
        while len(samples) < horizon:
            start = rng.integers(0, n)
            end = start + block_len
            if end <= n:
                block = resid[start:end]
            else:
                part1 = resid[start:n]
                part2 = resid[0:(end % n)]
                block = np.concatenate([part1, part2])
            samples.extend(block.tolist())
        return np.array(samples[:horizon])

    @staticmethod
    def _simulate_path_ar1(a, phi, sigma, r0, eps_seq):
        horizon = len(eps_seq)
        path = np.empty(horizon + 1)
        path[0] = r0
        for t in range(1, horizon + 1):
            path[t] = a + phi * path[t - 1] + eps_seq[t - 1]
        return path

    @staticmethod
    def _safe_float(val, default=0.0):
        v = float(val)
        return v if math.isfinite(v) else default

    # ========================
    #   CELL A: COINTEGRATION SCREENING
    # ========================

    def screen_pair(self, x_sym, y_sym):
        """
        Run CADF + Johansen cointegration tests on a pair.
        Returns dict with pair metadata if cointegrated and passes filters, else None.
        """
        try:
            pair_prices = self.data[[x_sym, y_sym]].dropna()
        except Exception:
            return None

        if len(pair_prices) < 50:
            return None

        pair_returns = pair_prices.pct_change().dropna()
        if len(pair_returns) < 50:
            return None

        price_corr = round(pair_prices.corr().iloc[0, 1], 2)
        return_corr = round(pair_returns.corr().iloc[0, 1], 2)
        px = round(float(pair_prices[x_sym].iloc[-1]), 2)
        py = round(float(pair_prices[y_sym].iloc[-1]), 2)

        ix = self.industry_map.get(x_sym, "Unknown")
        iy = self.industry_map.get(y_sym, "Unknown")

        cadf_pass = False
        johansen_pass = False
        beta_ts = None
        spread = None
        beta0_j = None

        # --- CADF with Kalman ---
        try:
            ols = OLS(pair_prices[y_sym], add_constant(pair_prices[x_sym])).fit()
            beta0 = float(ols.params.iloc[1])
            beta_ts = self.kalman_beta_series(pair_prices[x_sym], pair_prices[y_sym], beta0)
            spread = pair_prices[y_sym] - beta_ts * pair_prices[x_sym]
            try:
                pval = adfuller(spread.dropna(), autolag="AIC")[1]
            except Exception:
                pval = 1.0
            if pval < self.ADF_P_VALUE:
                cadf_pass = True
        except Exception:
            cadf_pass = False

        # --- Johansen ---
        try:
            jr = coint_johansen(pair_prices, det_order=0, k_ar_diff=1)
            trace, ct = jr.lr1, jr.cvt[:, 1]
            maxe, cm = jr.lr2, jr.cvm[:, 1]
            if any(trace[i] > ct[i] and maxe[i] > cm[i] for i in range(2)):
                johansen_pass = True
                idx = int(np.argmax(jr.eig))
                v1, v2 = jr.evec[:, idx]
                beta0_j = -v1 / v2
        except Exception:
            johansen_pass = False
            beta0_j = None

        if not (cadf_pass or johansen_pass):
            return None

        # --- Final beta/spread selection ---
        if cadf_pass:
            pass_method = "CADF" if not johansen_pass else "Both"
        else:
            try:
                beta_ts = self.kalman_beta_series(
                    pair_prices[x_sym], pair_prices[y_sym], float(beta0_j)
                )
                spread = pair_prices[y_sym] - beta_ts * pair_prices[x_sym]
                pass_method = "Johansen"
            except Exception:
                return None

        # --- Compute basic metrics for filtering ---
        lag = spread.shift(1).bfill()
        ret = spread - lag
        b = np.polyfit(lag, ret, 1)[0] if np.std(lag) > 0 else 0
        hl = max(1, int(round(-np.log(2) / b))) if b != 0 else 1
        # Cap half-life to 1/3 of available data so the rolling window is meaningful.
        # A huge hl (e.g. 500 bars on intraday data) would give a nearly constant mean
        # and near-zero std, collapsing z-scores toward 0 and failing the abs(z)>2 filter.
        max_hl = max(1, len(spread) // 3)
        hl = min(hl, max_hl)

        mavg = spread.rolling(window=hl, min_periods=max(1, hl // 2)).mean()
        mstd = spread.rolling(window=hl, min_periods=max(1, hl // 2)).std()

        z = round(float((spread.iloc[-1] - mavg.iloc[-1]) / mstd.iloc[-1]), 1) \
            if (mstd.iloc[-1] and not np.isnan(mstd.iloc[-1]) and mstd.iloc[-1] != 0) else np.nan

        hurst_val = self.hurst(spread.values)

        # --- Filter (matches Colab: abs(z) > limit AND hurst < limit) ---
        if not math.isfinite(z) or abs(z) <= self.Z_SCORE_LIMIT:
            return None
        if (not math.isfinite(hurst_val)) or hurst_val >= self.HURST_LIMIT:
            return None

        qty = round(abs(float(beta_ts.iloc[-1])), 2)

        # Strip .NS/.BO suffixes for cleaner display
        x_disp = x_sym.replace(".NS", "").replace(".BO", "")
        y_disp = y_sym.replace(".NS", "").replace(".BO", "")

        if z > 0:
            combo_str = f"Sell {qty} of {x_disp} ({px}, {ix})  &  Buy 1 of {y_disp} ({py}, {iy})"
        else:
            combo_str = f"Buy {qty} of {x_disp} ({px}, {ix})  &  Sell 1 of {y_disp} ({py}, {iy})"

        return {
            "x": x_sym, "y": y_sym,
            "method": pass_method,
            "cadf_pass": cadf_pass, "johansen_pass": johansen_pass,
            "price_corr": price_corr, "return_corr": return_corr,
            "px": px, "py": py,
            "combo_str": combo_str,
            "beta_ts": beta_ts, "spread": spread,
            "half_life": hl,
            "industry_x": ix, "industry_y": iy,
        }

    # ========================
    #   CELL B: ENSEMBLE MONTE CARLO
    # ========================

    def run_ensemble_mc(self, item):
        """
        Run ensemble MC on a screened pair to compute P(profit) with
        parameter uncertainty, block bootstrap residuals, extreme-Z tracking.
        """
        x, y = item["x"], item["y"]
        px, py = item["px"], item["py"]
        spread = item["spread"]
        beta_ts = item["beta_ts"]
        combo_str = item["combo_str"]
        method = item["method"]
        price_corr = item["price_corr"]
        return_corr = item["return_corr"]
        hl = item["half_life"]
        # Cap half-life to 1/3 of spread length (same as screen_pair) so rolling windows are meaningful
        hl = min(hl, max(1, len(spread) // 3))

        industry_x = item.get("industry_x", "Unknown")
        industry_y = item.get("industry_y", "Unknown")
        same_sector = "Yes" if (industry_x != "Unknown" and industry_x == industry_y) else "No"

        block_len = max(1, int(round(max(1, hl * self.BLOCK_LEN_FACTOR))))

        # --- Fit AR(1) ---
        yvals = spread.values[1:]
        Xvals = spread.values[:-1]
        Xc = np.column_stack([np.ones(len(Xvals)), Xvals])
        model = np.linalg.lstsq(Xc, yvals, rcond=None)[0]
        a_hat, phi_hat = float(model[0]), float(model[1])
        fitted = Xc.dot(model)
        resid = yvals - fitted
        sigma_hat = float(np.std(resid, ddof=1))

        n_obs = len(yvals)
        sse = np.sum((yvals - fitted) ** 2)
        mse = sse / max(1, n_obs - 2)
        try:
            XtX_inv = np.linalg.inv(Xc.T.dot(Xc))
            se = np.sqrt(np.diag(XtX_inv) * mse)
            cov_params = np.diag(se ** 2)
        except Exception:
            se = np.array([1.0, 1.0])
            cov_params = np.eye(2)

        rng_main = np.random.default_rng(self.RNG_SEED)

        total_sims = int(self.ENSEMBLE_M) * int(self.SIMS_PER_DRAW)
        if total_sims > self.MAX_TOTAL_SIMS:
            sims_per_local = max(1, int(self.MAX_TOTAL_SIMS // max(1, int(self.ENSEMBLE_M))))
        else:
            sims_per_local = self.SIMS_PER_DRAW

        # --- Ensemble MC ---
        p_draws = []
        for m in range(int(self.ENSEMBLE_M)):
            try:
                params_sample = rng_main.multivariate_normal(mean=[a_hat, phi_hat], cov=cov_params)
                a_s, phi_s = float(params_sample[0]), float(params_sample[1])
            except Exception:
                a_s = float(rng_main.normal(a_hat, se[0]))
                phi_s = float(rng_main.normal(phi_hat, se[1]))

            phi_s = max(min(phi_s, 0.999), -0.999)
            df_chi = max(1, n_obs - 2)
            chi2_draw = st.chi2.rvs(df_chi, random_state=rng_main)
            sigma_s = sigma_hat * np.sqrt(df_chi / chi2_draw) if chi2_draw > 0 else sigma_hat

            r0 = float(spread.iloc[-1])
            mavg_val = float(spread.rolling(window=hl, min_periods=1).mean().iloc[-1])
            mstd_val = float(spread.rolling(window=hl, min_periods=1).std().iloc[-1])
            z = (r0 - mavg_val) / (mstd_val if mstd_val != 0 else 1e-12)
            trade_sign = -1 if z > 0 else 1

            wins = 0
            for si in range(int(sims_per_local)):
                if self.USE_BOOTSTRAP_RESID and len(resid) > 0:
                    eps_seq = self._block_bootstrap_errors(resid, hl, rng_main, block_len)
                else:
                    eps_seq = rng_main.normal(0, sigma_s, size=hl)

                path = self._simulate_path_ar1(a_s, phi_s, sigma_s, r0, eps_seq)
                for t in range(1, hl + 1):
                    delta = path[t] - r0
                    pnl_currency = trade_sign * delta
                    if pnl_currency > 0:
                        wins += 1
                        break

            p_draws.append(wins / sims_per_local if sims_per_local > 0 else 0.0)

        p_draws_clean = np.array([p for p in p_draws if not np.isnan(p)])
        if p_draws_clean.size == 0:
            p_median_pct, p_low, p_high = 0.0, 0.0, 0.0
        else:
            p_median_pct = round(float(np.median(p_draws_clean)) * 100.0, 1)
            p_low = round(float(np.percentile(p_draws_clean, 5)) * 100.0, 1)
            p_high = round(float(np.percentile(p_draws_clean, 95)) * 100.0, 1)

        # --- Display metrics ---
        mp = max(1, hl // 2)
        mavg = spread.rolling(window=hl, min_periods=mp).mean()
        mstd = spread.rolling(window=hl, min_periods=mp).std()
        z_display = round(float(
            (spread.iloc[-1] - mavg.iloc[-1]) / (mstd.iloc[-1] if mstd.iloc[-1] != 0 else 1e-12)
        ), 1)

        move = round(float(-z_display * mstd.iloc[-1]), 2) if (not np.isnan(z_display) and mstd.iloc[-1]) else 0.0
        unit = round(float(abs(beta_ts.iloc[-1] * px) + abs(py)), 2) if not np.isnan(beta_ts.iloc[-1]) else 0.0
        exp_r = abs(round(float(move * 100 / unit), 1)) if unit else 0.0

        hurst_val = self.hurst(spread.values)

        # --- Extreme Z in HL window ---
        highest_z_in_hl_flag = "No"
        extreme_z_formatted = ""
        is_profitable_since_extremum = "N/A"
        pnl_since_extremum = 0.0

        historical_z_scores = []
        if not np.isnan(z_display) and hl > 0 and len(spread) >= hl:
            all_z = (spread - mavg) / mstd
            z_window = all_z.iloc[-hl:].dropna()
            
            # Format for frontend chart: [{time: unix_timestamp, value: z}, ...]
            for idx, val in all_z.dropna().items():
                if math.isfinite(val):
                    historical_z_scores.append({
                        "time": int(idx.timestamp()),
                        "value": round(float(val), 2)
                    })
            
            # Ensure historical_z_scores are unique by timestamp and sorted
            seen_times = set()
            unique_z_scores = []
            for item in historical_z_scores:
                if item["time"] not in seen_times:
                    unique_z_scores.append(item)
                    seen_times.add(item["time"])
            
            historical_z_scores = sorted(unique_z_scores, key=lambda x: x["time"])

            if not z_window.empty:
                current_z_unrounded = float(all_z.iloc[-1])

                if current_z_unrounded > 0:
                    extremum_z = float(z_window.max())
                    if np.isclose(current_z_unrounded, extremum_z):
                        highest_z_in_hl_flag = "Yes"
                else:
                    extremum_z = float(z_window.min())
                    if np.isclose(current_z_unrounded, extremum_z):
                        highest_z_in_hl_flag = "Yes"

                if math.isfinite(extremum_z):
                    idxs = z_window[np.isclose(z_window, extremum_z)].index
                    date_str = idxs[0].strftime("%Y-%m-%d") if len(idxs) > 0 else "N/A"
                    extreme_z_formatted = f"{round(extremum_z, 1)} ({date_str})"

                    # PnL since extreme Z
                    if highest_z_in_hl_flag == "No" and len(idxs) > 0:
                        extremum_date = idxs[0]
                        spread_at_ext = float(spread.loc[extremum_date])
                        trade_sign_at_ext = -1 if extremum_z > 0 else 1
                        hypothetical_pnl = -trade_sign_at_ext * (float(spread.iloc[-1]) - spread_at_ext)
                        pnl_since_extremum = round(hypothetical_pnl, 2)
                        is_profitable_since_extremum = "Yes" if hypothetical_pnl > 0 else "No"

        return {
            "pair": f"{x.replace('.NS','').replace('.BO','')}/{y.replace('.NS','').replace('.BO','')}",
            "combo": combo_str,
            "method": method,
            "price_corr": self._safe_float(price_corr),
            "z_score": self._safe_float(z_display),
            "half_life": hl,
            "move_to_mean": self._safe_float(move),
            "exp_return": self._safe_float(exp_r),
            "unit_price": self._safe_float(unit),
            "hurst": self._safe_float(hurst_val, 0.5),
            "prob_profit": self._safe_float(p_median_pct),
            "prob_profit_low": self._safe_float(p_low),
            "prob_profit_high": self._safe_float(p_high),
            "same_sector": same_sector,
            "extreme_z_in_hl": highest_z_in_hl_flag,
            "extreme_z_detail": extreme_z_formatted,
            "profitable_since_extreme": is_profitable_since_extremum,
            "pnl_since_extreme": self._safe_float(pnl_since_extremum),
            "historical_z_scores": historical_z_scores,
        }

    # ========================
    #   MAIN SCAN
    # ========================

    def run_scan(self):
        active_tickers = self.fetch_data()
        if len(active_tickers) < 2:
            logger.warning("Need at least 2 active tickers to form pairs")
            return []

        # Fetch industries
        self.fetch_industries()

        # Cell A: screen for cointegrated pairs
        pairs_all = list(combinations(active_tickers, 2))
        logger.info(f"Screening {len(pairs_all)} pairs for cointegration...")

        screened = []
        for x, y in pairs_all:
            if len(screened) >= self.top_n:
                break
            try:
                result = self.screen_pair(x, y)
                if result:
                    screened.append(result)
                    logger.info(f"  ✓ Cointegrated: {x}/{y} ({result['method']})")
            except Exception as e:
                logger.warning(f"  ✗ {x}/{y} screening failed: {e}")

        logger.info(f"Found {len(screened)} cointegrated pairs. Running ensemble MC...")

        # Cell B: ensemble MC on screened pairs
        results = []
        for i, item in enumerate(screened):
            try:
                mc_result = self.run_ensemble_mc(item)
                results.append(mc_result)
                logger.info(
                    f"  [{i+1}/{len(screened)}] {mc_result['pair']} "
                    f"z={mc_result['z_score']} p={mc_result['prob_profit']}%"
                )
            except Exception as e:
                logger.warning(f"  ✗ MC failed for {item['x']}/{item['y']}: {e}")

        logger.info(f"Scan complete. {len(results)} pairs with full metrics.")
        return sorted(results, key=lambda x: x["prob_profit"], reverse=True)
