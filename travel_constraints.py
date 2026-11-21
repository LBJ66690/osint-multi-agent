"""Deterministic checks for dated travel offers and reservations."""
from datetime import date, datetime, timedelta


def quote_lines(trip,hits):
    start=date.fromisoformat(trip["start_date"])
    end=date.fromisoformat(trip["end_date"])
    categories=["交通接驳","门票","餐食"]
    if end>start: categories.append("住宿")
    if trip.get("include_round_trip"): categories.append("往返交通")
    lines=[]
    for category in categories:
        options=[]
        for h in hits:
            offer=h.get("quote") or {}
            if h.get("source_type") not in ("official_notice","authorized_quote") or not h.get("source_url"):
                continue
            if h.get("poi_id") not in trip["poi_ids"] or offer.get("category")!=category:
                continue
            if not ((h.get("valid_from") or "9999")<=str(start) and (h.get("valid_to") or "")>=str(end)):
                continue
            amount=offer.get("lower_fen")
            unit=offer.get("unit")
            if type(amount)!=int or amount<0 or unit not in ("person","person_day","group","room_night"):continue
            upper=offer.get("upper_fen")
            if type(upper)!=int or upper<amount or offer.get("kind")=="starting_price":upper=None
            if unit=="room_night" and not trip.get("rooms"):continue
            n={"person":trip["travelers"],"person_day":trip["travelers"]*((end-start).days+1),"group":1,
               "room_night":trip.get("rooms",1)*(end-start).days}[unit]
            options.append({"label":category,"quantity":n,"lower_fen":amount,"upper_fen":upper,
                            "source_url":h["source_url"],"unit":unit})
        if len(options)==1:lines.append(options[0])
        else:lines.append({"label":category,"quantity":1,"lower_fen":0,"upper_fen":None})
    return lines


def arrival_constraint(ticket,seconds):
    if not all(ticket.get(k) for k in ("date","arrival_time","latest_entry")):return None
    arrival=datetime.fromisoformat(f"{ticket['date']}T{ticket['arrival_time']}")+timedelta(seconds=seconds)
    cutoff=datetime.fromisoformat(f"{ticket.get('reservation_date') or ticket['date']}T{ticket['latest_entry']}")
    if arrival>cutoff:
        return f"最早约 {arrival:%m月%d日 %H:%M} 到入口，晚于预约截止 {cutoff:%H:%M}；请确认是否改约次日"
    return None
