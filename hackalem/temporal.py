"""Calendar-day flow matching; pure calculations with exact integer tiyn."""

from collections import deque

import numpy as np
import pandas as pd

from .validation import money_cents


def temporal_support(tx: pd.DataFrame, window_days: int = 7) -> dict[int, float]:
    """Outgoing amount with an observed incoming operation 1–7 days earlier.

    Dates within one day have unknown order. No matching of the same funds is
    claimed, and self transfers cannot support a transit hypothesis.
    """
    dated = tx.loc[tx.src.ne(tx.dst)].copy()
    dated["day"] = pd.to_datetime(dated.date).dt.normalize()
    dated = dated.sort_values(["src", "dst", "day", "sum_kzt"])
    incoming = {gid: np.sort(group.day.to_numpy()) for gid, group in dated.groupby("dst")}
    result = {}
    for gid, outgoing in dated.groupby("src"):
        dates = incoming.get(gid)
        if dates is None:
            result[gid] = 0.0
            continue
        days = outgoing.day.to_numpy()
        previous = np.searchsorted(dates, days, side="left") - 1
        valid = previous >= 0
        lag = days - dates[np.maximum(previous, 0)]
        supported = valid & (lag <= np.timedelta64(window_days, "D"))
        result[gid] = float(outgoing.loc[supported, "sum_kzt"].sum() / outgoing.sum_kzt.sum())
    return result


def amount_temporal_support(
    tx: pd.DataFrame, window_days: int = 7
) -> dict[int, dict[str, int | float]]:
    """FIFO in integer tiyn; same-day coverage is uncertain and also consumed.

    Expire receipts older than seven calendar days. Allocate earlier receipts
    first, then consume possible same-day coverage without calling it support.
    Only unused same-day receipts are available on subsequent days. This is a
    matching convention for observed flows, not a reconstructed account balance.
    """
    dated = tx.loc[tx.src.ne(tx.dst)].copy()
    dated["day"] = dated["day"] if "day" in dated else pd.to_datetime(dated.date).dt.date
    dated["cents"] = (
        dated.sum_tiyn
        if "sum_tiyn" in dated
        else money_cents(dated.sum_kzt, "transactions.sum_kzt", positive=True)
    )
    incoming, outgoing = {}, {}
    for row in dated.itertuples(index=False):
        for mapping, gid in ((incoming, row.dst), (outgoing, row.src)):
            daily = mapping.setdefault(gid, {})
            daily[row.day] = daily.get(row.day, 0) + int(row.cents)
    result = {}
    for gid in sorted(set(incoming) | set(outgoing)):
        credits, debits = incoming.get(gid, {}), outgoing.get(gid, {})
        available = deque()
        matched = uncertain = 0
        for day in sorted(set(credits) | set(debits)):
            while available and (day - available[0][0]).days > window_days:
                available.popleft()
            remaining = debits.get(day, 0)
            while remaining and available:
                used = min(remaining, available[0][1])
                matched += used
                remaining -= used
                available[0][1] -= used
                if available[0][1] == 0:
                    available.popleft()
            same_day = min(remaining, credits.get(day, 0))
            uncertain += same_day
            unused_credit = credits.get(day, 0) - same_day
            if unused_credit:
                available.append([day, unused_credit])
        total = sum(debits.values())
        result[gid] = {
            "temporal_matched_tiyn": matched,
            "same_day_uncertain_tiyn": uncertain,
            "temporal_outgoing_tiyn": total,
            "temporal_matched_kzt": matched / 100,
            "temporal_matched_out_share": matched / total if total else 0.0,
            "same_day_uncertain_kzt": uncertain / 100,
            "same_day_uncertain_out_share": uncertain / total if total else 0.0,
            "temporal_unmatched_out_share": (total - matched - uncertain) / total if total else 0.0,
        }
    return result
