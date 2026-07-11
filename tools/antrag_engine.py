#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Online-Shops Engine v2 — Schätz-Tarif (pro Modul) + Antrags-Bestückung + eSign-Stempel.

Funnel-Payload  ->  (1) Tarif PRO MODUL inkl. "Was bringt's"-Text
                ->  (2) echter HISCOX-Antrag policierfertig befüllt (+ Zusatzfelder)
                ->  (3) Kunden-Unterschrift + Ort/Name/Datum zertifikats-mäßig auf S.22
"""
import sys, json, argparse, io, hashlib, datetime, math
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

GRUEN = HexColor("#0b3d2e"); GOLD = HexColor("#b08d3f"); GRAU = HexColor("#6b7280"); TINTE = HexColor("#1a2b4a")
VST = 0.19

# ---------------------------------------------------------------- TARIF
UMSATZ_BANDS = [(100_000,1.00),(250_000,1.35),(500_000,1.8),(1_000_000,2.5),(5_000_000,4.2)]
SUM_FACTOR = {0:1.00,1:1.30,2:1.70,3:2.20}
BASE = {"bhv":89,"vsh":290,"cyber":365,"sach":250}
AMAZON_COI = 15
USA_EXPORT = {"keiner":0,"gering":250,"mittel":600,"hoch":900,"sehr_hoch":1400}
DISCOUNTS = {"gruender":0.15,"laufzeit3j":0.10,"buendel":0.05}
PAYMENT_SURCHARGE = {"jaehrlich":0.0,"halbjaehrlich":0.0,"vierteljaehrlich":0.02,"monatlich":0.03}

MODULE_KATALOG = {
 "bhv":{"label":"Betriebshaftpflicht","pflicht":True,
        "benefit":"Zahlt, wenn dein Produkt oder Betrieb einem Kunden schadet – inkl. Import & Eigenmarke (du haftest wie der Hersteller), Urheberrecht und AGG bis 25.000 €.",
        "ohne":"Ohne diesen Schutz trägst du Schadenersatz an Kunden selbst – existenzbedrohend."},
 "vsh":{"label":"Vermögensschadenhaftpflicht","pflicht":False,
        "benefit":"Deckt reine Vermögensschäden – Falschlieferung, Beratungs- oder Berechnungsfehler – über die 25.000 € der Betriebshaftpflicht hinaus.",
        "ohne":"Ohne VSH bleibst du bei reinen Vermögensschäden über 25.000 € auf den Kosten sitzen."},
 "cyber":{"label":"Cyber & Datenrisiken","pflicht":False,
        "benefit":"Hackerangriff, Datenleck, IT-Betriebsunterbrechung und Erpressung – mit Soforthilfe und voller Kostenübernahme.",
        "ohne":"Ohne Cyber zahlst du Wiederherstellung, Anwalt und Ausfall nach einem Angriff selbst."},
 "sach":{"label":"Sachinhalt & Elektronik","pflicht":False,
        "benefit":"Echte Allgefahren für Ware, Lager und Technik – auch Diebstahl unterwegs/auf Messen, inkl. Vertrauensschäden durch Mitarbeiter.",
        "ohne":"Ohne Sachschutz ist verlorene/zerstörte Ware und Technik dein Risiko."},
}

def umsatz_factor(u):
    for cap,f in UMSATZ_BANDS:
        if u<=cap: return f
    return UMSATZ_BANDS[-1][1]

def calc_tarif(p):
    uf=umsatz_factor(p.get("umsatz_gesamt",0)); sf=SUM_FACTOR.get(p.get("sum_idx",1),1.3)
    module=set(p.get("module",["bhv"])); lines={}
    lines["bhv"]=round(BASE["bhv"]*uf*sf)
    for m in ("vsh","cyber"):
        if m in module: lines[m]=round(BASE[m]*uf*sf)
    if "sach" in module:
        lines["sach"]=round(BASE["sach"]*uf*max(1,p.get("standorte",1)))
    if p.get("amazon"): lines["bhv"]+=AMAZON_COI
    usa=USA_EXPORT.get(p.get("usa_export","keiner"),0)
    if usa: lines["bhv"]+=usa
    subtotal1=sum(lines.values())
    d_bundle=round(subtotal1*DISCOUNTS["buendel"]) if len(lines)>=2 else 0
    s2=subtotal1-d_bundle
    d_run=round(s2*DISCOUNTS["laufzeit3j"]) if p.get("laufzeit3j") else 0
    s3=s2-d_run
    d_start=round(s3*DISCOUNTS["gruender"]) if p.get("gruender") else 0
    s4=s3-d_start
    surcharge=round(s4*PAYMENT_SURCHARGE.get(p.get("zahlweise","jaehrlich"),0.0))
    netto=s4+surcharge; brutto=round(netto*(1+VST),2)
    # pro-Modul Aufstellung (brutto je Modul, anteilig)
    mods=[]
    for mid in ("bhv","vsh","cyber","sach"):
        if mid in lines:
            k=MODULE_KATALOG[mid]
            mods.append({"id":mid,"label":k["label"],"pflicht":k["pflicht"],
                         "benefit":k["benefit"],"ohne":k["ohne"],
                         "netto":lines[mid],"brutto":round(lines[mid]*(1+VST),2)})
    return {"lines":lines,"subtotal1":subtotal1,"d_bundle":d_bundle,"subtotal2":s2,
            "d_run":d_run,"subtotal3":s3,"d_start":d_start,"subtotal4":s4,
            "surcharge":surcharge,"netto":netto,"brutto":brutto,
            "korridor":(round(brutto*0.9),round(brutto*1.15)),"module":mods}

def eur(x): return f"{x:,.2f} €".replace(",","X").replace(".",",").replace("X",".")

# ---------------------------------------------------------------- FELD-BEFÜLLUNG
AKT={"bekleidung":("policyholder_activities__1","Bekleidung und Accessoires"),
     "buero":("policyholder_activities__2","Büro, Haushalt und Freizeit"),
     "it":("policyholder_activities__3","IT und Elektronik"),
     "food":("policyholder_activities__4","Nahrungs- und Genußmittel")}

def nearest(states,target):
    n=[]
    for s in states:
        try:n.append((abs(float(s)-target),s))
        except:pass
    return min(n)[1] if n else None

def build_values(p,t,reader):
    f=reader.get_fields(); v={}
    v["policyholder_salutation"]={"herr":"/Mr.","frau":"/Mrs."}.get(p.get("anrede","").lower(),"/NA")
    v["policyholder_firstname"]=p.get("vorname","");v["policyholder_lastname"]=p.get("nachname","")
    v["policyholder_company"]=p.get("firma","")
    v["policyholder_legal_type"]="/Legal Entity" if p.get("legal_entity",True) else "/Self-Employed"
    # Rechtsform-Dropdown (zuvor leer) – exakte Combo-Option aus Firmierung ableiten
    LF_MAP={"gmbh & co. kg":"GmbH & Co. KG","gmbh":"GmbH","ug":"UG","ag":"AG","gbr":"GBR",
            "ohg":"OHG","kgaa":"KGaA","kg":"KG","e.k.":"e.K.","e.g.":"e.G.","e.v.":"e.V.",
            "se":"SE","ltd":"Ltd.","einzelunternehmen":"Einzelunternehmen"}
    lf=p.get("rechtsform","")
    if not lf:
        fl=p.get("firma","").lower()
        for k,val in LF_MAP.items():
            if k in fl: lf=val; break
    if lf: v["policyholder_legal_form"]=lf
    v["policyholder_founding_date"]=p.get("gruendung","")
    v["policyholder_street"]=p.get("strasse","");v["policyholder_street_no"]=p.get("hausnr","")
    v["policyholder_zip"]=p.get("plz","");v["policyholder_city"]=p.get("ort","")
    v["admin_policyholder_country"]=p.get("land","Deutschland")
    for c in p.get("kategorien",[]):
        if c in AKT: fld,st=AKT[c]; v[fld]="/"+st
    v["sach_risk_location1_street"]=p.get("strasse","");v["sach_risk_location1_street_no"]=p.get("hausnr","")
    v["sach_risk_location1_zip"]=p.get("plz","");v["sach_risk_location1_city"]=p.get("ort","")
    v["insurance_pact_begin_date"]=p.get("beginn","")
    v["has_different_maturity_date"]="/Off"   # zuvor leer: explizit „nein"
    if p.get("amazon"): v["bhv_compliment_amazon"]="/15"
    if "bhv_compliment_usa_export" in f:
        ue=USA_EXPORT.get(p.get("usa_export","keiner"),0); st=f["bhv_compliment_usa_export"].get("/_States_")
        if ue and st:
            ns=nearest([s.strip("/") for s in st if s not in ("/-","/Off")],ue)
            if ns: v["bhv_compliment_usa_export"]="/"+ns
    v["bhv_module_excess"]="/30" if p.get("sb") else "/0"
    for fld,line in (("sum_insured_bhv","bhv"),("sum_insured_vsh","vsh"),("sum_insured_cyber","cyber")):
        if fld in f and line in t["lines"]:
            st=f[fld].get("/_States_")
            if st:
                ns=nearest([s.strip("/") for s in st if s!="/Off"],t["lines"][line])
                if ns: v[fld]="/"+ns
    # Sach-Modul Sub-Felder (zuvor leer)
    if "sach" in t["lines"]:
        v["sum_insured_sach_selection"]="/1"
        if "sum_insured_sach_rl1" in f:
            st=f["sum_insured_sach_rl1"].get("/_States_")
            if st:
                ns=nearest([s.strip("/") for s in st if s!="/Off"],t["lines"]["sach"])
                if ns: v["sum_insured_sach_rl1"]="/"+ns
    v["discount_start_up"]="/Ja" if p.get("gruender") else "/Off"
    v["discount_running_time"]="/Ja" if p.get("laufzeit3j") else "/Off"
    v["discount_stack_text"]="/Ja" if t["d_bundle"]>0 else "/Off"
    kf=p.get("killer",{})
    for fld,key in (("killerquestion_damage_past_choice","schaeden"),
                    ("killerquestion_cyber_subsidiary_choice","us_tochter"),
                    ("killerquestion_cyber_it_security_big_choice","it_security"),
                    ("killerquestion_20k_creditcard_choice","kk_daten"),
                    ("killerquestion_excluded_hardware_choice","hardware"),
                    ("killerquestion_door_security_choice","tuersicherung")):
        v[fld]="/Ja" if kf.get(key) else "/Nein"
    s=p.get("sepa",{})
    if s:
        v["payment_method"]="/sepa";v["sepa_iban"]=s.get("iban","");v["sepa_bic"]=s.get("bic","")
        v["sepa_bankname"]=s.get("bank","");v["sepa_account_owner"]=s.get("inhaber","")
        v["sepa_street"]=p.get("strasse","");v["sepa_street_no"]=p.get("hausnr","")
        v["sepa_zip"]=p.get("plz","");v["sepa_city"]=p.get("ort","");v["sepa_country"]="Deutschland"
        v["sepa_confirm"]="/Ja"
    else: v["payment_method"]="/rechnung"
    v["payment_period"]={"jaehrlich":"/0","halbjaehrlich":"/2","vierteljaehrlich":"/3","monatlich":"/4"}.get(p.get("zahlweise","jaehrlich"),"/0")
    v["broker_name"]=p.get("broker_name","Jan Ruhoff Makler mit Fliege")
    v["broker_email"]=p.get("broker_email","team@maklermitfliege.de")
    v["broker_pool_name"]=p.get("broker_pool","PL3U1H")
    v["hv"]=p.get("hv","3880")
    v["broker_no_binding_contact_name"]=p.get("broker_name","Jan Ruhoff Makler mit Fliege")  # zuvor leer
    v["admin_helper_caller"]=p.get("broker_pool","PL3U1H")                                   # zuvor leer
    v["total_premium_bhv"]=eur(t["lines"].get("bhv",0));v["total_premium_vsh"]=eur(t["lines"].get("vsh",0))
    v["total_premium_cyber"]=eur(t["lines"].get("cyber",0));v["total_premium_sach"]=eur(t["lines"].get("sach",0))
    v["total_premium_subtotal_1"]=eur(t["subtotal1"]);v["total_premium_discount_bundle"]="-"+eur(t["d_bundle"])
    v["total_premium_subtotal_2"]=eur(t["subtotal2"]);v["total_premium_discount_running_time"]="-"+eur(t["d_run"])
    v["total_premium_subtotal_3"]=eur(t["subtotal3"]);v["total_premium_discount_start_up"]="-"+eur(t["d_start"])
    v["total_premium_subtotal_4"]=eur(t["subtotal4"]);v["total_premium_netto"]=eur(t["netto"]);v["total_premium_gross"]=eur(t["brutto"])
    v["overview_confirming_name"]=(p.get("vorname","")+" "+p.get("nachname","")).strip()
    v["overview_confirming_person"]="/maklermandatierung"
    v["contract_date"]=p.get("antragsdatum",p.get("beginn",""))
    return v

# ---------------------------------------------------------------- eSIGN-STEMPEL (Seite 22)
def signature_strokes(c,x,y,w,h,seed):
    """Synthetische, handschrift-ähnliche Unterschrift (Bezier), seed-stabil."""
    import random; rnd=random.Random(seed)
    c.setStrokeColor(TINTE); c.setLineWidth(1.6)
    n=4; px=x; py=y+h*0.5
    c.setLineCap(1)
    for i in range(n):
        x1=px+w/n*rnd.uniform(0.15,0.4); y1=py+h*rnd.uniform(0.2,0.5)
        x2=px+w/n*rnd.uniform(0.6,0.85); y2=py-h*rnd.uniform(0.2,0.5)
        ex=px+w/n; ey=py+h*rnd.uniform(-0.15,0.15)
        c.bezier(px,py,x1,y1,x2,y2,ex,ey); px=ex; py=ey
    # Schwung-Unterstrich
    c.setLineWidth(1.1)
    c.bezier(x-2,y+h*0.15,x+w*0.3,y-h*0.1,x+w*0.7,y+h*0.25,x+w+6,y+h*0.05)

def stamp_signature(writer,p,page_index=22):
    pw,ph=595,842
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=(pw,ph))
    name=(p.get("vorname","")+" "+p.get("nachname","")).strip() or "Versicherungsnehmer"
    ort=p.get("ort","");datum=p.get("antragsdatum",p.get("beginn",""))
    ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ref="OSH-"+hashlib.sha1((name+datum+p.get("firma","")).encode()).hexdigest()[:10].upper()
    bx,by,bw=312,150,250   # Signaturblock unten rechts (über "keine Unterschrift notwendig")
    # weißes Kästchen, das den Hinweis "keine Unterschrift notwendig" überdeckt
    c.setFillColorRGB(1,1,1); c.rect(bx-4,by-44,bw+10,108,fill=1,stroke=0)
    # Rahmen (zertifikats-Look)
    c.setStrokeColor(GOLD); c.setLineWidth(0.8); c.rect(bx-4,by-44,bw+10,108,fill=0,stroke=1)
    # Signatur
    sig=p.get("signature_png")
    if sig:
        try: c.drawImage(sig,bx+6,by+10,width=150,height=46,mask='auto',preserveAspectRatio=True)
        except: signature_strokes(c,bx+6,by+12,150,40,name)
    else:
        signature_strokes(c,bx+6,by+12,150,40,name)
    # Linie + Name + Ort/Datum
    c.setStrokeColor(GRAU); c.setLineWidth(0.6); c.line(bx+4,by+6,bx+bw,by+6)
    c.setFillColor(TINTE); c.setFont("Helvetica-Bold",9); c.drawString(bx+4,by-6,name)
    c.setFillColor(GRAU); c.setFont("Helvetica",7.5)
    c.drawString(bx+4,by-17,f"{ort}, den {datum}  ·  elektronisch unterschrieben (eSign)")
    # eSign-Fußnote (ehrlich: einfache eSignatur mit Zeitstempel, keine QES-Behauptung)
    c.setFont("Helvetica",6); c.setFillColor(GRUEN)
    c.drawString(bx+4,by-30,f"eSign Signaturnachweis mit Datum · VERSIANER · {ts} · Ref {ref}")
    c.drawString(bx+4,by-38,"Identität & Zeitstempel protokolliert")
    c.showPage(); c.save(); buf.seek(0)
    ov=PdfReader(buf).pages[0]
    if page_index < len(writer.pages):
        writer.pages[page_index].merge_page(ov)

def run(payload,src,out):
    p=json.load(open(payload,encoding="utf-8"))
    t=calc_tarif(p)
    reader=PdfReader(src); values=build_values(p,t,reader)
    writer=PdfWriter(); writer.append(reader)
    for pg in writer.pages:
        try: writer.update_page_form_field_values(pg,values,auto_regenerate=False)
        except: pass
    stamp_signature(writer,p,page_index=22)
    try: writer.set_need_appearances_writer(True)
    except: pass
    with open(out,"wb") as fh: writer.write(fh)
    # Report
    print("TARIF PRO MODUL (brutto/Jahr):")
    for m in t["module"]:
        tag="Pflicht" if m["pflicht"] else "optional"
        print(f"  [{tag:8}] {m['label']:32} {eur(m['brutto']):>12}   – {m['benefit'][:54]}…")
    print(f"  Zwischensumme {eur(t['subtotal1'])} | Rabatte -{eur(t['d_bundle']+t['d_run']+t['d_start'])}")
    print(f"  NETTO {eur(t['netto'])}  ·  BRUTTO {eur(t['brutto'])}  ·  Korridor {eur(t['korridor'][0])}–{eur(t['korridor'][1])}")
    print(f"  Felder befüllt: {len([x for x in values if values[x] not in ('/Off','')])}  ·  Unterschrift gestempelt S.23  ·  -> {out}")
    return t

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("payload");ap.add_argument("src");ap.add_argument("out")
    a=ap.parse_args(); run(a.payload,a.src,a.out)
