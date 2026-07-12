#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live-Bridge für den Online-Shops-Funnel.
  GET  /                 -> liefert den Funnel (onlineshops/index.html)
  POST /api/antrag       -> Payload -> Schätz-Tarif -> echter HISCOX-Antrag (PDF, signiert)
  POST /api/ausschreibung-> Payload -> Ausschreibungs-Briefing (txt) für die 3-Träger-Anfrage

Start:  python3 tools/serve.py   (Default Port 8080)
Der Funnel erkennt http:// automatisch und holt sich die Datei vom Backend.
"""
import os, sys, json, tempfile, base64, datetime
from flask import Flask, request, send_file, Response
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import antrag_engine as eng
import dno_engine as dno

FUNNEL = os.path.join(ROOT, "onlineshops", "index.html")
VORLAGE = os.path.join(ROOT, "vorlagen", "HISCOX-Shops-Antrag_blank.pdf")
DNO_FUNNEL = os.path.join(ROOT, "dno", "index.html")
# Echte Killerfragen fuer D&O -- "bilanz" (Eigenkapital positiv? Ja=gut) und "vorversicherung"/
# "beteiligungen" (rein informativ) sind bewusst ausgenommen, s. data/dno_strecke.json annahme_fragen[].killer.
DNO_KILLER_KEYS = ("verfahren", "boerse", "anspruch", "vorschaeden", "ablehnung", "insolvenz")

app = Flask(__name__)

@app.get("/")
def home():
    return send_file(FUNNEL)

@app.get("/dno/")
@app.get("/dno")
def dno_home():
    return send_file(DNO_FUNNEL)

@app.get("/health")
def health():
    return {"ok": True, "vorlage": os.path.exists(VORLAGE)}

@app.get("/api/tarifkonstanten")
def tarifkonstanten():
    """Single Source of Truth für die JS-Anzeige (index.html loadKonstanten()) -
    verhindert Drift zwischen Anzeige-Rechner und Antrags-Engine (HANDOFF §7.1)."""
    return {
        "VST": eng.VST, "UMSATZ_BANDS": eng.UMSATZ_BANDS, "SUM_FACTOR": eng.SUM_FACTOR,
        "BASE": eng.BASE, "AMAZON_COI": eng.AMAZON_COI, "USA_EXPORT": eng.USA_EXPORT,
        "DISC": eng.DISCOUNTS, "PAY_SUR": eng.PAYMENT_SURCHARGE,
    }

def _lead_webhook(kind, payload, tarif, killer_keys=None, url_env="LEAD_WEBHOOK_URL"):
    """Best-effort: Lead + Tarif-Indikator an Make -> Pipedrive. Nie den Haupt-Response blockieren/kaputt machen.

    killer_keys: welche payload['killer']-Schluessel als Red-Flag zaehlen. None = alle Werte (Online-Shop-Verhalten,
    dort ist bei JEDER Killerfrage "Ja" ein Red-Flag). Sparten mit gemischt-positiven Fragen (z.B. D&O "Eigenkapital
    positiv?") MUESSEN eine explizite Teilmenge uebergeben, sonst wird eine gute Antwort faelschlich als auffaellig gemeldet.
    """
    url = os.environ.get(url_env)
    if not url:
        return
    try:
        import requests
        korridor = tarif.get("korridor") or (None, None)
        killer = payload.get("killer") or {}
        if killer_keys is None:
            auffaellig = any(killer.values()) if killer else False
        else:
            auffaellig = any(killer.get(k) for k in killer_keys)
        # Nur flache Skalarfelder (kein Array/Dict) -- robust fuers Make-Webhook-Mapping,
        # gleiches Muster wie das bewaehrte Szenario "Rechner-Lead -> Pipedrive".
        slim = {
            "kind": kind, "ts": datetime.datetime.now().isoformat(),
            "anrede": payload.get("anrede"), "vorname": payload.get("vorname"), "nachname": payload.get("nachname"),
            "firma": payload.get("firma"), "strasse": payload.get("strasse"), "hausnr": payload.get("hausnr"),
            "plz": payload.get("plz"), "ort": payload.get("ort"), "gruendung": payload.get("gruendung"),
            "kategorien_alle": ", ".join(payload.get("kategorien_alle") or []),
            "kanaele": ", ".join(payload.get("kanaele") or []),
            "umsatz_gesamt": payload.get("umsatz_gesamt"), "umsatz_exakt": payload.get("umsatz_exakt"),
            "usa_export": payload.get("usa_export"), "module": ", ".join(payload.get("module") or []),
            "ausschreibung": bool(payload.get("ausschreibung")),
            "killer_auffaellig": auffaellig,
            "beginn": payload.get("beginn"), "weblinks": payload.get("weblinks"), "beschreibung": payload.get("beschreibung"),
            "tarif_brutto": tarif.get("brutto"), "tarif_korridor_min": korridor[0], "tarif_korridor_max": korridor[1],
            "utm_source": payload.get("utm_source"), "utm_medium": payload.get("utm_medium"), "utm_campaign": payload.get("utm_campaign"),
            "broker_pool": payload.get("broker_pool"), "hv": payload.get("hv"),
            # D&O-Felder (bei anderen Sparten schlicht None -- Make zeigt sie dann nicht/leer)
            "rechtsform": payload.get("rechtsform"), "umsatz_band": payload.get("umsatz_band"),
            "organe": payload.get("organe"), "versicherungssumme": payload.get("versicherungssumme"),
            "gruendung_neu": bool(payload.get("gruendung_neu")) if "gruendung_neu" in payload else None,
        }
        requests.post(url, json=slim, timeout=4)
    except Exception:
        pass

def _sig_to_path(payload):
    """eSign-DataURL -> temporäre PNG-Datei, Pfad in payload['signature_png']."""
    sig = payload.get("signature_png", "")
    if isinstance(sig, str) and sig.startswith("data:image"):
        b64 = sig.split(",", 1)[1]
        fd, p = tempfile.mkstemp(suffix=".png"); os.close(fd)
        with open(p, "wb") as f: f.write(base64.b64decode(b64))
        payload["signature_png"] = p
        return p
    payload["signature_png"] = None
    return None

@app.post("/api/antrag")
def antrag():
    payload = request.get_json(force=True)
    sigpath = _sig_to_path(payload)
    fd, pj = tempfile.mkstemp(suffix=".json"); os.close(fd)
    with open(pj, "w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False)
    fd, out = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
    try:
        t = eng.run(pj, VORLAGE, out)   # rechnet Tarif + befüllt Antrag + stempelt Unterschrift
        _lead_webhook("antrag", payload, t)
        name = (payload.get("firma") or payload.get("nachname") or "Antrag").replace(" ", "-")
        return send_file(out, mimetype="application/pdf", as_attachment=True,
                         download_name="VERSIANER-Antrag_"+name+".pdf")
    finally:
        for p in (pj, sigpath):
            try:
                if p: os.remove(p)
            except OSError: pass

@app.post("/api/ausschreibung")
def ausschreibung():
    p = request.get_json(force=True)
    t = eng.calc_tarif(p)
    _lead_webhook("ausschreibung", p, t)
    L = []
    L.append("VERSIANER · MARKT-AUSSCHREIBUNG (Online-Shop / E-Commerce)")
    L.append("Erstellt: "+datetime.datetime.now().strftime("%d.%m.%Y %H:%M")+"  ·  Makler PL3U1H")
    L.append("="*64)
    L.append("\nVERSICHERUNGSNEHMER")
    L.append(f"  {p.get('anrede','').title()} {p.get('vorname','')} {p.get('nachname','')} · {p.get('firma','')}")
    L.append(f"  {p.get('strasse','')} {p.get('hausnr','')}, {p.get('plz','')} {p.get('ort','')} · gegr. {p.get('gruendung','')}")
    L.append("\nRISIKO")
    L.append(f"  Produktkategorien: {', '.join(p.get('kategorien_alle',[])) or '-'}")
    L.append(f"  Vertriebskanäle:   {', '.join(p.get('kanaele',[])) or '-'}")
    um = p.get('umsatz_exakt') or (f"{p.get('umsatz_gesamt',0):,} €".replace(',', '.'))
    L.append(f"  Jahresumsatz:      {um}{'  (INDIVIDUELL ≥5 Mio)' if p.get('umsatz_individuell') else ''}")
    L.append(f"  USA/Kanada-Export: {p.get('usa_export','keiner')}")
    L.append(f"  Webshop-Link(s):   {p.get('weblinks','') or '-'}")
    L.append(f"  Gewünschte Bausteine: {', '.join(p.get('module',[]))}")
    L.append(f"  Beschreibung: {p.get('beschreibung','') or '-'}")
    L.append("\nBEITRAGS-INDIKATOR (Schätzung, Markt verhandelt)")
    for m in t["module"]:
        L.append(f"  - {m['label']}: {eng.eur(m['brutto'])} brutto/Jahr")
    L.append(f"  GESAMT: {eng.eur(t['brutto'])} / Jahr  (Korridor {eng.eur(t['korridor'][0])}–{eng.eur(t['korridor'][1])})")
    # Träger-Routing
    usa = p.get("usa_export","keiner")
    if p.get("umsatz_individuell"): traeger="HISCOX + Markel + AXA (Großrisiko, individuell)"
    elif usa in ("hoch","sehr_hoch"): traeger="HISCOX + Markel (+ AXA)"
    elif usa in ("gering","mittel"): traeger="HISCOX (Frontline, COI) + Markel + andsafe"
    else: traeger="HISCOX + andsafe + 1 Volumen-Player (Ameise)"
    L.append("\nAUSSCHREIBUNG AN: "+traeger)
    L.append("ANNAHME-CHECK: "+("AUFFÄLLIG – manuell prüfen" if any(p.get('killer',{}).values()) else "sauber (alle Killerfragen nein)"))
    body = "\n".join(L)
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition":"attachment; filename=VERSIANER-Ausschreibung.txt"})

@app.get("/api/dno/tarifkonstanten")
def dno_tarifkonstanten():
    """Single Source of Truth fuer dno/index.html loadKonstanten() -- gleiches Muster wie /api/tarifkonstanten."""
    return {
        "VST": dno.VST, "BASE": dno.BASE, "UMSATZ_BANDS": dno.UMSATZ_BANDS, "SUM_FACTOR": dno.SUM_FACTOR,
        "ORGAN_ZUSCHLAG_PRO_WEITEREM": dno.ORGAN_ZUSCHLAG_PRO_WEITEREM,
        "DISCOUNTS": dno.DISCOUNTS, "PAY_SUR": dno.PAYMENT_SURCHARGE,
    }

@app.post("/api/dno/ausschreibung")
def dno_ausschreibung():
    """D&O ist ausschliesslich Ausschreibung -- kein Self-Service-Antrag (Frontline VOV, s. dno_strecke.json)."""
    p = request.get_json(force=True)
    t = dno.calc_tarif(p)
    _lead_webhook("dno_ausschreibung", p, t, killer_keys=DNO_KILLER_KEYS, url_env="DNO_LEAD_WEBHOOK_URL")
    L = []
    L.append("VERSIANER · MARKT-AUSSCHREIBUNG (D&O · Geschäftsführerhaftung)")
    L.append("Erstellt: "+datetime.datetime.now().strftime("%d.%m.%Y %H:%M")+"  ·  Makler PL3U1H")
    L.append("="*64)
    L.append("\nVERSICHERUNGSNEHMER")
    L.append(f"  {p.get('anrede','').title()} {p.get('vorname','')} {p.get('nachname','')} · {p.get('firma','')}")
    L.append(f"  {p.get('strasse','')} {p.get('hausnr','')}, {p.get('plz','')} {p.get('ort','')} · Rechtsform: {p.get('rechtsform','')}")
    L.append("\nRISIKO")
    L.append(f"  Jahresumsatz: {p.get('umsatz_band','-')}")
    L.append(f"  Gründung < 36 Monate (Startup-Weiche): {'JA — individuell ausschreiben' if p.get('gruendung_neu') else 'nein'}")
    L.append(f"  Anzahl Organe: {p.get('organe',1)}")
    L.append(f"  Versicherungssumme: {p.get('versicherungssumme','-')}")
    L.append(f"  Gewünschte Bausteine: {', '.join(p.get('module',[]))}")
    L.append(f"  Beschreibung: {p.get('beschreibung','') or '-'}")
    L.append("\nBEITRAGS-INDIKATOR (grober Schätz-Anker, Markt verhandelt -- KEINE verbindlichen Tarife, s. data/dno_strecke.json)")
    for mid, betrag in t["lines"].items():
        L.append(f"  - {dno.MODULE_LABEL.get(mid, mid)}: {dno.eur(round(betrag*(1+dno.VST),2))} brutto/Jahr")
    L.append(f"  GESAMT: {dno.eur(t['brutto'])} / Jahr  (Korridor {dno.eur(t['korridor'][0])}–{dno.eur(t['korridor'][1])})")
    # Traeger-Routing (aus data/dno_strecke.json routing)
    if p.get("gruendung_neu") or p.get("umsatz_band") == "über 25 Mio. €":
        traeger = "VOV + AIG + Chubb (Großrisiko/Startup, individuell)"
    else:
        traeger = "VOV (Frontline) + HISCOX + Markel"
    L.append("\nAUSSCHREIBUNG AN: "+traeger)
    # Nur echte Killerfragen zaehlen (s. dno_strecke.json annahme_fragen[].killer) -- "bilanz" (Ja=gut)
    # und "vorversicherung"/"beteiligungen" (rein informativ) sind bewusst KEINE Red-Flags.
    kf = p.get("killer", {})
    dno_auffaellig = any(kf.get(k) for k in DNO_KILLER_KEYS)
    L.append("ANNAHME-CHECK: "+("AUFFÄLLIG – manuell prüfen" if dno_auffaellig else "sauber (alle Killerfragen nein)"))
    body = "\n".join(L)
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition":"attachment; filename=VERSIANER-DO-Ausschreibung.txt"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
