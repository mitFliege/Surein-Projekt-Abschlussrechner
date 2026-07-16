#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D&O-Engine (Geschaeftsfuehrerhaftung) -- Ausschreibung-only.

Kein Antrag-PDF: Frontline VOV ist ein Spezial-Pool ohne Self-Service-Antrag
(kein Blank-Antrag verfuegbar, s. data/dno_strecke.json ausschreibung_only_grund).
Nur calc_tarif() fuer den Beitrags-Indikator + das Ausschreibungs-Briefing.

PLATZHALTER-Tarifkonstanten, REKALIBRIERT 16.07.2026 auf die bereits live stehende
Preistabelle des D&O-Funnel-Quiz auf /d-o-versicherung-geschaeftsfuehrer (priceTable-
Control der Sektion "D&O Funnel (5 Fragen)") -- s. data/dno_strecke.json tarif._hinweis.
Noch immer NICHT an einem echten VOV/HISCOX/Markel-Testangebot kalibriert.
"""

VST = 0.19
BASE = {"firmen_do": 840, "strafrecht": 120, "persoenlich": 280}
UMSATZ_BANDS = [(2_000_000, 1.0), (5_000_000, 2.0), (25_000_000, 4.5), (999_999_999, 7.0)]
UMSATZ_LABEL = ["bis 2 Mio. €", "2–5 Mio. €", "5–25 Mio. €", "über 25 Mio. €"]
SUM_FACTOR = {0: 1.0, 1: 1.6, 2: 2.4, 3: 3.6, 4: 6.0}
SUM_LABEL = ["250.000 €", "500.000 €", "1 Mio. €", "2 Mio. €", "5 Mio. €"]
ORGAN_ZUSCHLAG_PRO_WEITEREM = 0.15
DISCOUNTS = {"buendel": 0.05, "laufzeit3j": 0.10}
PAYMENT_SURCHARGE = {"jaehrlich": 0.0, "halbjaehrlich": 0.0, "vierteljaehrlich": 0.02, "monatlich": 0.03}

def umsatz_factor(band_idx):
    if 0 <= band_idx < len(UMSATZ_BANDS):
        return UMSATZ_BANDS[band_idx][1]
    return UMSATZ_BANDS[-1][1]

def _umsatz_idx_from_label(label):
    try:
        return UMSATZ_LABEL.index(label)
    except ValueError:
        return 0

def calc_tarif(p):
    umsatz_idx = p.get("umsatz_idx")
    if umsatz_idx is None:
        umsatz_idx = _umsatz_idx_from_label(p.get("umsatz_band", UMSATZ_LABEL[0]))
    uf = umsatz_factor(umsatz_idx)
    sf = SUM_FACTOR.get(p.get("sum_idx", 2), 2.4)
    organe = max(1, p.get("organe", 1))
    organ_z = 1 + max(0, organe - 1) * ORGAN_ZUSCHLAG_PRO_WEITEREM

    module = set(p.get("module", ["firmen_do"]))
    lines = {"firmen_do": round(BASE["firmen_do"] * uf * sf * organ_z)}
    if "strafrecht" in module:
        lines["strafrecht"] = round(BASE["strafrecht"] * uf * sf)
    if "persoenlich" in module:
        lines["persoenlich"] = round(BASE["persoenlich"] * sf * organ_z)

    subtotal1 = sum(lines.values())
    d_bundle = round(subtotal1 * DISCOUNTS["buendel"]) if len(lines) >= 2 else 0
    s2 = subtotal1 - d_bundle
    d_run = round(s2 * DISCOUNTS["laufzeit3j"]) if p.get("laufzeit3j") else 0
    s3 = s2 - d_run
    surcharge = round(s3 * PAYMENT_SURCHARGE.get(p.get("zahlweise", "jaehrlich"), 0.0))
    netto = s3 + surcharge
    brutto = round(netto * (1 + VST), 2)
    return {
        "lines": lines, "subtotal1": subtotal1, "d_bundle": d_bundle,
        "d_run": d_run, "netto": netto, "brutto": brutto,
        "korridor": (round(brutto * 0.85), round(brutto * 1.25)),
    }

def eur(x):
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

MODULE_LABEL = {"firmen_do": "Firmen-D&O (Innen- & Außenhaftung)", "strafrecht": "Straf-Rechtsschutz-Baustein", "persoenlich": "Persönliche D&O (Zweitschutz)"}
