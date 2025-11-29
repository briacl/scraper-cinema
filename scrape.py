#!/usr/bin/env python3
"""
scrape.py
Fait une requête vers l'URL fournie et sauvegarde le HTML dans le dossier d'exécution.

Remarques:
- Utilise un User-Agent explicite indiquant qu'il s'agit d'un scraper.
- Si la requête échoue, écrit l'erreur dans `filmes_error.txt`.
"""
from pathlib import Path
import sys
import argparse
import json
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time

# URL par défaut (peut être surchargée via la ligne de commande)
DEFAULT_URL = "https://www.cinemas/nos.pt/filmes"

# Dossier pour stocker les fichiers générés par les recherches
DATA_DIR = Path("searching_film_data")


def sanitize_for_filename(s: str) -> str:
    if not s:
        return "unknown"
    # remplacer les caractères non alphanumériques par underscore
    safe = re.sub(r"[^0-9A-Za-z\-]+", "_", s)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe


def get_shwt_date_from_url(url: str) -> str | None:
    m = re.search(r"shwt_date=(\d{4}-\d{2}-\d{2})", url)
    return m.group(1) if m else None


def make_run_dir() -> Path:
    """Crée un sous-dossier horodaté sous DATA_DIR et le retourne."""
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run = DATA_DIR / ts
    run.mkdir(parents=True, exist_ok=True)
    # écrire un fichier indiquant le dernier run (utile au viewer statique)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        latest_file = DATA_DIR / 'latest_run.txt'
        latest_file.write_text(ts, encoding='utf-8')
    except Exception:
        # Ne pas bloquer l'exécution si l'écriture échoue
        pass
    return run


def make_output_path_from_url(url: str) -> Path:
    """Crée un chemin lisible pour la sortie à partir de l'URL.

    Les fichiers sont rangés dans `searching_film_data` et le nom retire le schéma
    et transforme le chemin en un nom lisible. Exemple:
      https://www.allocine.fr/seance/... -> searching_film_data/allocine_www_allocine_fr_seance_....html
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    host = parsed.netloc.replace('www.', '')
    # concat path + fragment + query pour garder l'info utile
    parts = parsed.path or ''
    if parsed.fragment:
        parts = parts + '_' + parsed.fragment
    if parsed.query:
        parts = parts + '_' + parsed.query
    combined = f"{host}{parts}"
    # remplace tout ce qui n'est pas alphanumérico/_/- par underscore
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", combined)
    safe = re.sub(r"_+", "_", safe).strip("_")
    # Prefixe indiquant le site pour plus de lisibilité
    if 'allocine' in host:
        safe = f"allocine_{safe}"
    return DATA_DIR / (safe + '.html')


OUTPUT = Path("filmes.html")
ERROR_FILE = Path("filmes_error.txt")

HEADERS = {
    # User-Agent plus détaillé (contient le contact demandé)
    "User-Agent": "MyScraper/1.0 (+briac.le.meillat@gmail.com) - scraping for research"
}


def main():
    parser = argparse.ArgumentParser(description="Simple scraper: récupère une page HTML et la sauvegarde sur disque.")
    parser.add_argument("url", nargs="?", help="URL à scrapper (si omise, une saisie interactive sera proposée)")
    parser.add_argument("--salle-name", dest="salle_name", help="Nom lisible de la salle (ex: 'Le Prévert de Harnes')")
    parser.add_argument("--film", "-f", dest="film", help="Titre du film à filtrer sur la page du cinéma (optionnel)")
    args = parser.parse_args()

    # Si l'URL n'a pas été fournie en argument, demander à l'utilisateur
    if args.url:
        url = args.url
    else:
        try:
            # interactive prompt (utile pour usage direct)
            url = input(f"Entrez l'URL à scrapper (laisser vide pour utiliser la valeur par défaut {DEFAULT_URL}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # newline propre
            url = ""

        if not url:
            url = DEFAULT_URL

    # créer un répertoire d'exécution horodaté
    run_dir = make_run_dir()

    print(f"Requête vers: {url}")
    print(f"User-Agent: {HEADERS['User-Agent']}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # Sauvegarde l'erreur sur le disque pour inspection
        error_path = run_dir / "request_error.txt"
        error_path.write_text(str(exc), encoding="utf-8")
        print(f"La requête a échoué: {exc}")
        print(f"Détails de l'erreur écrits dans: {error_path.resolve()}")
        sys.exit(1)

    # Parser la page pour pouvoir extraire le nom lisible de la salle avant d'écrire
    soup = BeautifulSoup(resp.text, "html.parser")

    # tenter d'extraire un nom de salle lisible depuis le HTML (fallback si fourni via CLI)
    def extract_salle_name(soup_obj: BeautifulSoup) -> str | None:
        # candidates: éléments courants (h1, .titlebar-title, breadcrumb items)
        try:
            h = soup_obj.find("h1")
            if h and h.get_text(strip=True):
                txt = h.get_text(strip=True)
                if len(txt) > 3:
                    return txt
        except Exception:
            pass
        try:
            tb = soup_obj.find(class_=lambda c: c and 'titlebar-title' in c)
            if tb and tb.get_text(strip=True):
                return tb.get_text(strip=True)
        except Exception:
            pass
        try:
            # rechercher un élément contenant 'cin' ou 'salle'
            candidate = soup_obj.find(lambda tag: tag.name in ("div", "span") and tag.get_text(strip=True) and ("cin" in tag.get_text(strip=True).lower() or "salle" in tag.get_text(strip=True).lower()))
            if candidate:
                return candidate.get_text(strip=True)
        except Exception:
            pass
        return None

    # déterminer le nom de la salle
    salle_name = args.salle_name or extract_salle_name(soup) or urlparse(url).path.split('=')[-1]
    safe_salle = sanitize_for_filename(salle_name)
    # date pour le nom de fichier (priorité: fragment #shwt_date=; fallback: date du jour)
    seance_date = get_shwt_date_from_url(url) or datetime.now().date().isoformat()

    # noms de fichiers selon votre convention
    html_filename = f"allocine_{safe_salle}_all_seances_{seance_date}.html"
    page_json_filename = f"allocine_{safe_salle}_all_seances_{seance_date}.json"

    output_path = run_dir / html_filename
    error_path = run_dir / f"{safe_salle}_error.txt"

    # Écrire le HTML récupéré dans le dossier horodaté
    output_path.write_text(resp.text, encoding="utf-8")
    print(f"HTML sauvegardé dans: {output_path.resolve()} (taille: {len(resp.text)} octets)")

    # Titre: prefere og:title, sinon <title>
    title = None
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title.get("content").strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    else:
        # tentative sur les balises h1 communes
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else None

    # Synopsis: meta description ou éléments avec classes communes
    synopsis = None
    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    if meta_desc and meta_desc.get("content"):
        synopsis = meta_desc.get("content").strip()
    else:
        # recherche dans les classes contenant des mots-clés
        def find_by_keywords(keywords):
            for kw in keywords:
                el = soup.find(class_=lambda c: c and kw in c.lower())
                if el:
                    return el.get_text(" ", strip=True)
            return None

        synopsis = find_by_keywords(["synopsis", "synop", "resume", "résumé", "resume", "description", "summary"]) or None

    # Horaires / séances: collecte de textes dans classes pouvant contenir ces infos
    showtimes = []
    for kw in ["seance", "seances", "horaire", "horaires", "showtime", "sessions"]:
        els = soup.find_all(class_=lambda c: c and kw in c.lower())
        for el in els:
            text = el.get_text(" ", strip=True)
            if text and text not in showtimes:
                showtimes.append(text)

    # NOTE: page-level JSON will be construit plus bas après l'extraction
    # des cartes films et de leurs séances, pour préserver l'ordre d'affichage.
    output_json = run_dir / page_json_filename

    # Si l'utilisateur a demandé l'extraction des séances pour un film précis
    def normalize_text(s: str) -> str:
        if not s:
            return ""
        return re.sub(r"\W+", " ", s, flags=re.UNICODE).strip().lower()

    def parse_cinema_showtimes(soup: BeautifulSoup, film_title: str, page_url: str):
        """Tente d'extraire les séances d'un film sur une page 'salle' d'AlloCiné.

        Retourne un dict avec keys: found (bool), film, count, showtimes (list)
        Chaque showtime est un dict {date, time, text}.
        """
        target = normalize_text(film_title)

        # Rechercher des ancres qui semblent pointer vers la fiche film et dont le texte correspond
        anchors = soup.find_all("a", href=True)
        candidates = []
        for a in anchors:
            text = a.get_text(" ", strip=True) or ""
            if target and target in normalize_text(text):
                candidates.append(a)
                continue
            if a.has_attr("title") and target and target in normalize_text(a["title"]):
                candidates.append(a)

        # Si aucun ancres directes, tenter des anchors pointant vers des URLs /film/ ou fichefilm
        if not candidates:
            for a in anchors:
                if re.search(r"/film(/|$)|fichefilm_gen_cfilm|film-", a["href"]):
                    parent = a.find_parent()
                    if parent and target and target in normalize_text(parent.get_text(" ", strip=True)):
                        candidates.append(a)

        if not candidates:
            return {"found": False, "reason": "film not found on page", "film": film_title}

        # On prend la première correspondance raisonnable
        a = candidates[0]
        # Remonter jusqu'à un container logique
        container = a.find_parent()
        for cls in ["showtimes-movie", "showtimes-movie-holder", "result-item", "entity-card", "tpl-seances-list"]:
            c = a.find_parent(class_=lambda c: c and cls in c.lower())
            if c:
                container = c
                break

        # Récupérer les spans contenant les horaires
        spans = container.find_all(lambda tag: tag.name in ("span", "a", "time") and (tag.has_attr("data-showtime-time") or (tag.get("class") and any("show" in cl or "hour" in cl for cl in (tag.get("class") or [])))))

        # Si aucun span trouvé dans le container, chercher globalement à proximité du lien
        if not spans:
            # rechercher éléments proches: frères / suivants
            siblings = []
            parent = a.parent
            for _ in range(4):
                if not parent:
                    break
                siblings.extend(parent.find_all(lambda t: t.name in ("span", "time") and (t.has_attr("data-showtime-time") or "showtime" in (t.get("class") or []))))
                parent = parent.parent
            spans = siblings

        showtimes = []
        for s in spans:
            # Préférer le texte visible (ce qui est affiché sur la page) plutôt que l'attribut data-showtime-time
            time_text = s.get_text(" ", strip=True)
            time_val = None
            if time_text:
                # Normaliser les formats courants : "20:30", "20h30", "20h"
                m = re.search(r"(\d{1,2})[hH:](\d{2})", time_text)
                if m:
                    hh = int(m.group(1))
                    mm = m.group(2)
                    time_val = f"{hh:02d}:{mm}"
                else:
                    m2 = re.search(r"(\d{1,2})[hH]\b", time_text)
                    if m2:
                        hh = int(m2.group(1))
                        time_val = f"{hh:02d}:00"
                    else:
                        # fallback: garder le texte brut si aucun format détecté
                        time_val = time_text
            else:
                # si aucun texte visible, on peut tenter l'attribut data-showtime-time en fallback
                time_val = s.get("data-showtime-time") or s.get_text(" ", strip=True)
            date_val = None
            # rechercher date dans l'élément ou ses ancêtres
            el = s
            while el and el != container:
                if el.has_attr("data-showtime-date"):
                    date_val = el["data-showtime-date"]
                    break
                el = el.parent
            # fallback: extraire la date depuis le fragment de l'URL (ex: #shwt_date=2025-11-29)
            if not date_val:
                parsed = urlparse(page_url)
                if parsed.fragment:
                    m = re.search(r"shwt_date=(\d{4}-\d{2}-\d{2})", parsed.fragment)
                    if m:
                        date_val = m.group(1)

            showtimes.append({"date": date_val, "time": time_val, "text": s.get_text(" ", strip=True)})

        # dédupliquer
        unique = []
        seen = set()
        for st in showtimes:
            key = f"{st.get('date')}_{st.get('time')}"
            if key not in seen:
                unique.append(st)
                seen.add(key)

        return {"found": True, "film": film_title, "count": len(unique), "showtimes": unique, "source_url": page_url}

    if args.film:
        print(f"Extraction des séances pour le film demandé: '{args.film}'")
        try:
            showtimes_data = parse_cinema_showtimes(soup, args.film, url)
        except Exception as exc:
            showtimes_data = {"found": False, "error": str(exc), "film": args.film}

        # écrire les résultats dans un fichier dédié (nouvelle convention de nommage)
        safe_film = sanitize_for_filename(args.film)
        # Nom selon votre convention: {title}_data_by_{salle}_by_allocine.json
        film_json_name = f"{safe_film}_data_by_{safe_salle}_by_allocine.json"
        st_path = run_dir / film_json_name
        # pour simplifier l'usage côté viewer, ne conserver que la valeur 'time'
        try:
            times_only = []
            for s in showtimes_data.get('showtimes', []) or []:
                if isinstance(s, dict):
                    t = s.get('time') or s.get('text') or None
                    if t:
                        times_only.append(t)
                elif isinstance(s, str):
                    times_only.append(s)
            # remplacer la structure showtimes par une liste de times (strings)
            showtimes_data['showtimes'] = times_only
            showtimes_data['count'] = len(times_only)
        except Exception:
            # si transformation échoue, écrire la donnée brute
            pass

        st_path.write_text(json.dumps(showtimes_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Résultats séances écrits dans: {st_path.resolve()}")

    # --- Extraction des fiches films listées sur la page (AlloCiné) ---
    # Helper: extraction d'une carte film
    def extract_film_card(card, base_url):
        title_tag = card.find("a", class_="meta-title-link")
        title = title_tag.get_text(strip=True) if title_tag else None
        rel_link = title_tag["href"] if title_tag and title_tag.has_attr("href") else None
        link = urljoin(base_url, rel_link) if rel_link else None

        img = card.find("img", class_="thumbnail-img")
        poster = None
        if img:
            poster = img.get("data-src") or img.get("src")
            if poster:
                poster = urljoin(base_url, poster)

        date_el = card.find("span", class_="date")
        release_date = date_el.get_text(strip=True) if date_el else None

        duration = None
        info_el = card.find("div", class_="meta-body-item meta-body-info")
        if info_el:
            txt = info_el.get_text(" ", strip=True)
            m = re.search(r"(\d+h\s*\d*min|\d+h|min)", txt)
            if m:
                duration = m.group(0)

        genres = [g.get_text(strip=True) for g in (info_el.find_all("span", class_=lambda c: c and "dark-grey-link" in c) if info_el else [])]

        director = None
        dir_el = card.find("div", class_="meta-body-item meta-body-direction")
        if dir_el:
            d = dir_el.find("span", class_=lambda c: c and "dark-grey-link" in c)
            director = d.get_text(strip=True) if d else dir_el.get_text(" ", strip=True).replace("De", "").strip()

        actors = []
        actor_el = card.find("div", class_="meta-body-item meta-body-actor")
        if actor_el:
            actors = [a.get_text(strip=True) for a in actor_el.find_all("span", class_=lambda c: c and "dark-grey-link" in c)]

        synopsis = None
        syn = card.find("div", class_="synopsis")
        if syn:
            synopsis = syn.get_text(" ", strip=True)

        sessions = None
        btn = card.find("a", href=re.compile(r"/seance/film-"))
        if btn:
            txt = btn.get_text(" ", strip=True)
            m = re.search(r"(\d+[\s\u202f]*\d*)", txt)
            if m:
                sessions = int(m.group(0).replace("\u202f", "").replace(" ", ""))

        ratings = {}
        rating_items = card.find_all("div", class_="rating-item")
        for ri in rating_items:
            title_span = ri.find(class_=re.compile(r"rating-title"))
            note_span = ri.find("span", class_=re.compile(r"stareval-note"))
            if title_span and note_span:
                key = title_span.get_text(strip=True)
                val = note_span.get_text(strip=True)
                ratings[key] = val

        return {
            "title": title,
            "link": link,
            "poster": poster,
            "release_date": release_date,
            "duration": duration,
            "genres": genres,
            "director": director,
            "actors": actors,
            "synopsis": synopsis,
            "sessions": sessions,
            "ratings": ratings,
        }

    # Recherche des cartes film sur la page
    films = []
    base_url = "https://www.allocine.fr"
    cards = soup.find_all("div", class_=lambda c: c and "entity-card" in c)
    if not cards:
        cards = [li.find("div", class_=lambda c: c and "entity-card" in c) for li in soup.find_all("li", class_="mdl")]
        cards = [c for c in cards if c]

    for card in cards:
        films.append(extract_film_card(card, base_url))

    # Pagination: détecter nombre de pages
    pagination = soup.find(class_=lambda c: c and "pagination-item-holder" in c)
    max_page = 1
    pages = []
    if pagination:
        for a in pagination.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"[?&]page=(\d+)", href)
            if m:
                pages.append(int(m.group(1)))
        if pages:
            max_page = max(pages)

    if max_page > 1:
        print(f"Pagination détectée: {max_page} pages — récupération des pages 2..{max_page} (limite 30)")
        limit = min(max_page, 30)
        for p in range(2, limit + 1):
            page_url = urljoin(base_url, urlparse(url).path) + f"?page={p}"
            try:
                time.sleep(0.6)
                r = requests.get(page_url, headers=HEADERS, timeout=15)
                r.raise_for_status()
            except requests.RequestException as exc2:
                print(f"Échec récupération page {p}: {exc2}")
                continue
            soup_p = BeautifulSoup(r.text, "html.parser")
            cards_p = soup_p.find_all("div", class_=lambda c: c and "entity-card" in c)
            for card in cards_p:
                films.append(extract_film_card(card, base_url))

    if films:
        all_json_path = run_dir / page_json_filename.replace('.json', '_all.json')
        all_data = {
            "url": url,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "count": len(films),
            "films": films,
        }
        all_json_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Liste complète ({len(films)}) sauvegardée dans: {all_json_path.resolve()}")

        # Pour chaque film, joindre la liste des horaires (sous forme de chaînes HH:MM)
        for f in films:
            try:
                title = f.get('title')
                if not title:
                    f['showtimes'] = []
                    continue
                st = parse_cinema_showtimes(soup, title, url)
                times = []
                for s in st.get('showtimes', []) or []:
                    if isinstance(s, dict):
                        t = s.get('time') or s.get('text')
                        if t:
                            times.append(t)
                    elif isinstance(s, str):
                        times.append(s)
                f['showtimes'] = times
            except Exception:
                f['showtimes'] = []

        # Construire le JSON de page avec ordre préservé: header, seances, meta
        # Extraire un bloc header minimal (texte brut des nav / header si présent)
        header_raw = None
        try:
            hdr = soup.find(lambda tag: tag.name in ('header', 'nav') or (tag.get('id') and 'header' in tag.get('id').lower()))
            if hdr:
                header_raw = hdr.get_text(" ", strip=True)
            else:
                # fallback: prendre le premier gros texte présent en haut de page
                top = soup.find(True)
                header_raw = top.get_text(" ", strip=True) if top else None
        except Exception:
            header_raw = None

        page_struct = {
            "header": {"raw": header_raw},
            "seances": {"date": seance_date, "films": films},
            "meta": {"url": url, "fetched_at": datetime.utcnow().isoformat() + "Z", "html_file": str(output_path.name)},
        }

        # écrire le page-level JSON structuré
        output_json.write_text(json.dumps(page_struct, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Données extraites (page-structured) sauvegardées dans: {output_json.resolve()}")


if __name__ == "__main__":
    main()
